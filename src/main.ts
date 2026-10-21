import maplibregl from 'maplibre-gl';
import type { ExpressionSpecification, MapGeoJSONFeature } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';

// ---- data shapes (see pipeline/build.py) ----
type Quality = 'barangay_data' | 'city_median' | 'estimated' | 'no_data';
interface Props {
  id: string; n: string; c: string; p: string; d?: string; q: Quality;
  lo?: number; hi?: number; typ?: number; typ_m?: 'median' | 'geomean';
  lo_a?: number; hi_a?: number; typ_a?: number; g?: number;
  eff?: string;
}
type Geometry = { type: 'Polygon'; coordinates: number[][][] } | { type: 'MultiPolygon'; coordinates: number[][][][] };
interface Feature { type: 'Feature'; properties: Props; geometry: Geometry }
interface CityProps { id: string; n: string; p: string }
interface Collection<P> { type: 'FeatureCollection'; features: { type: 'Feature'; properties: P; geometry: Geometry }[] }
interface Meta {
  built_at: string; current_year: number; adjusted_available: boolean; adjusted_note?: string;
  schedule_range?: [string, string]; coverage: { features: number; by_flag: Partial<Record<Quality, number>> };
}
type Mode = 'typ' | 'lo' | 'hi';
type Basis = 'raw' | 'adj';
type BBox = [number, number, number, number];

// ---- scale ----
const STOPS = ['#e9f2e1', '#b5dcb4', '#63b39d', '#2d8590', '#265a8a', '#2c2f6e'];
const DOMAIN: [number, number] = [2500, 400000];
const TICKS = [2500, 10000, 30000, 100000, 400000];
const STYLES = {
  light: 'https://tiles.openfreemap.org/styles/positron',
  dark: 'https://tiles.openfreemap.org/styles/dark',
};
const NCR_BOUNDS: [[number, number], [number, number]] = [[120.93, 14.38], [121.13, 14.78]];
const MODE_LABEL: Record<Mode, string> = { typ: 'Typical', lo: 'Interior lot', hi: 'Prime' };
const QUALITY: Record<Quality, { label: string; cls: string; text: (p: Props) => string }> = {
  barangay_data: { label: 'barangay data', cls: 'good', text: () => "From this barangay's own BIR schedule rows." },
  city_median: { label: 'city median only', cls: 'warn', text: p => `No barangay detail yet. This is the median residential value for all of ${p.c}, not this barangay's own value.` },
  estimated: { label: 'estimated', cls: 'warn', text: () => 'No BIR value for this barangay. Estimated as the median of the barangays that share its border.' },
  no_data: { label: 'no data', cls: '', text: p => `No zonal values loaded for ${p.c} yet.` },
};

const lnStops = (() => {
  const [a, b] = DOMAIN.map(Math.log);
  return STOPS.flatMap((c, i) => [a + (b - a) * i / (STOPS.length - 1), c]);
})();
const tickPos = (v: number) => (Math.log(v) - Math.log(DOMAIN[0])) / (Math.log(DOMAIN[1]) - Math.log(DOMAIN[0]));

// ---- formatting ----
const fmt = (v?: number) => v == null ? '' : '₱' + Math.round(v).toLocaleString('en-PH');
const short = (v: number) => v >= 1e6 ? `₱${(v / 1e6).toFixed(1).replace(/\.0$/, '')}M`
  : v >= 1000 ? `₱${(v / 1000).toFixed(v < 10000 ? 1 : 0).replace(/\.0$/, '')}K` : `₱${v}`;
const esc = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
const fold = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
  .replace(/\b(barangay|brgy|bgy)\b\.?/g, ' ').replace(/\bsto\b\.?/g, 'santo').replace(/\bsta\b\.?/g, 'santa')
  .replace(/[^a-z0-9]+/g, ' ').trim();
const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

// ---- state ----
const state = {
  mode: 'typ' as Mode,
  basis: 'raw' as Basis,
  order: 'top' as 'top' | 'bottom',
  selected: null as string | null,
  hovered: null as string | null,
};
let zonal: Collection<Props>;
let cities: Collection<CityProps>;
let meta: Meta;
const byId = new Map<string, Feature>();
const bboxes = new Map<string, BBox>();

function bboxOf(g: Geometry): BBox {
  const b: BBox = [Infinity, Infinity, -Infinity, -Infinity];
  const rings = g.type === 'Polygon' ? g.coordinates : g.coordinates.flat();
  for (const ring of rings) for (const [x, y] of ring) {
    if (x < b[0]) b[0] = x; if (y < b[1]) b[1] = y; if (x > b[2]) b[2] = x; if (y > b[3]) b[3] = y;
  }
  return b;
}

const key = (m: Mode = state.mode, basis: Basis = state.basis) => (basis === 'adj' ? `${m}_a` : m) as keyof Props;
const valueOf = (p: Props, m: Mode = state.mode, basis: Basis = state.basis) =>
  (p[key(m, basis)] as number | undefined) ?? (p[key('typ', basis)] as number | undefined);

// ---- theme ----
const media = matchMedia('(prefers-color-scheme: dark)');
const theme = (): 'light' | 'dark' => (document.documentElement.dataset.theme as 'light' | 'dark') || (media.matches ? 'dark' : 'light');
const ICONS = {
  // shows the theme you will switch to
  light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
};
function renderThemeButton() {
  const next = theme() === 'dark' ? 'light' : 'dark';
  const b = $('theme');
  b.innerHTML = ICONS[next];
  b.setAttribute('aria-label', `Switch to ${next} theme`);
  b.title = `Switch to ${next} theme`;
}

// ---- map ----
const map = new maplibregl.Map({
  container: 'map',
  style: STYLES[theme()],
  bounds: NCR_BOUNDS,
  fitBoundsOptions: { padding: 12 },
  maxBounds: [[119.6, 13.4], [122.6, 15.8]],
  minZoom: 7.5,
  maxZoom: 17,
  attributionControl: { compact: true },
  dragRotate: false,
  pitchWithRotate: false,
  touchPitch: false,
  // dev only: lets screenshots capture the WebGL canvas
  canvasContextAttributes: import.meta.env.DEV ? { preserveDrawingBuffer: true } : undefined,
});
map.touchZoomRotate.disableRotation();
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: false }, trackUserLocation: false }), 'top-right');

function hatchImage() {
  const r = 2, s = 8 * r;
  const c = document.createElement('canvas');
  c.width = c.height = s;
  const ctx = c.getContext('2d')!;
  ctx.strokeStyle = cssVar('--hatch');
  ctx.globalAlpha = theme() === 'dark' ? 0.45 : 0.6;
  ctx.lineWidth = 1.1 * r;
  ctx.beginPath();
  for (const o of [-s, 0, s]) { ctx.moveTo(o, s); ctx.lineTo(o + s, 0); }
  ctx.stroke();
  return { image: ctx.getImageData(0, 0, s, s), pixelRatio: r };
}

function fillColor(): ExpressionSpecification {
  const v: ExpressionSpecification = ['coalesce', ['get', key()], ['get', key('typ')]];
  return ['case', ['!=', ['typeof', v], 'number'], cssVar('--nodata'),
    ['interpolate-lab', ['linear'], ['ln', v], ...lnStops] as unknown as ExpressionSpecification];
}

function addLayers() {
  if (!zonal || map.getSource('zonal')) return;
  const style = map.getStyle();
  // fills sit above all basemap geometry (dark-style roads are too heavy) and below the labels that follow it
  const lastGeom = style.layers.findLastIndex(l => l.type !== 'symbol');
  const before = style.layers[lastGeom + 1]?.id;
  // borrow a font the basemap's glyph server is known to have
  const fontLayer = style.layers.find(l => l.type === 'symbol' && Array.isArray(l.layout?.['text-font']));
  const labelFont = (fontLayer?.type === 'symbol' ? fontLayer.layout!['text-font'] as string[] : undefined) ?? ['Noto Sans Regular'];
  const dark = theme() === 'dark';

  map.addSource('zonal', { type: 'geojson', data: zonal as never, promoteId: 'id' });
  map.addSource('cities', { type: 'geojson', data: cities as never, promoteId: 'id' });
  if (map.hasImage('hatch')) map.removeImage('hatch');
  const h = hatchImage();
  map.addImage('hatch', h.image, { pixelRatio: h.pixelRatio });

  // the upstream barangay polygons are simplified and leave slivers; city polygons underneath hide them
  map.addLayer({
    id: 'c-fill', type: 'fill', source: 'cities',
    paint: { 'fill-color': cssVar('--nodata'), 'fill-opacity': 0.7 },
  }, before);
  map.addLayer({
    id: 'z-fill', type: 'fill', source: 'zonal',
    paint: {
      'fill-color': fillColor(),
      'fill-opacity': ['match', ['get', 'q'], 'barangay_data', 0.84, 'no_data', 0.7, 0.5],
    },
  }, before);
  map.addLayer({
    id: 'z-hatch', type: 'fill', source: 'zonal',
    filter: ['in', ['get', 'q'], ['literal', ['city_median', 'estimated']]],
    paint: { 'fill-pattern': 'hatch' },
  }, before);
  map.addLayer({
    id: 'z-line', type: 'line', source: 'zonal',
    paint: {
      'line-color': dark ? '#0b100f' : '#ffffff',
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 9, 0.35, 13, 0.8],
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 0.2, 14, 0.9],
    },
  }, before);
  map.addLayer({
    id: 'c-line', type: 'line', source: 'cities',
    paint: {
      'line-color': dark ? '#c3cfcc' : '#26302e',
      'line-opacity': 0.55,
      'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.5, 13, 1.6],
    },
  }, before);
  map.addLayer({
    id: 'z-focus', type: 'line', source: 'zonal',
    paint: {
      'line-color': cssVar('--fg'),
      'line-width': ['case', ['boolean', ['feature-state', 'sel'], false], 2.6, ['boolean', ['feature-state', 'hover'], false], 1.4, 0],
    },
  });
  map.addLayer({
    id: 'z-label', type: 'symbol', source: 'zonal', minzoom: 13.2,
    layout: { 'text-field': ['get', 'n'], 'text-font': labelFont, 'text-size': 11, 'text-max-width': 7, 'text-padding': 4 },
    paint: {
      'text-color': dark ? '#e4ecea' : '#17201f',
      'text-halo-color': dark ? 'rgba(16,22,21,.85)' : 'rgba(255,255,255,.85)',
      'text-halo-width': 1.3,
    },
  });
  if (state.selected) map.setFeatureState({ source: 'zonal', id: state.selected }, { sel: true });
}

function repaint() {
  if (map.getLayer('z-fill')) map.setPaintProperty('z-fill', 'fill-color', fillColor());
  renderLegend();
  renderRank();
  if (state.selected) renderCard(state.selected);
  writeHash();
}

// style.load can fire before or after the data arrives; whichever comes second adds the layers
let styleReady = false;
map.on('style.load', () => { styleReady = true; addLayers(); });
if (import.meta.env.DEV) Object.assign(window, { map });

map.on('mousemove', 'z-fill', (e) => {
  const id = (e.features?.[0] as MapGeoJSONFeature | undefined)?.id as string | undefined;
  if (id === state.hovered) return;
  if (state.hovered) map.setFeatureState({ source: 'zonal', id: state.hovered }, { hover: false });
  state.hovered = id ?? null;
  if (id) map.setFeatureState({ source: 'zonal', id }, { hover: true });
  map.getCanvas().style.cursor = 'pointer';
});
map.on('mouseleave', 'z-fill', () => {
  if (state.hovered) map.setFeatureState({ source: 'zonal', id: state.hovered }, { hover: false });
  state.hovered = null;
  map.getCanvas().style.cursor = '';
});
map.on('click', (e) => {
  const f = map.queryRenderedFeatures(e.point, { layers: ['z-fill'] })[0];
  select(f ? String(f.id) : null);
});
map.on('moveend', () => renderRank());

// ---- selection + card ----
function select(id: string | null, fly = false) {
  if (state.selected && map.getSource('zonal')) map.setFeatureState({ source: 'zonal', id: state.selected }, { sel: false });
  state.selected = id && byId.has(id) ? id : null;
  if (state.selected) {
    if (map.getSource('zonal')) map.setFeatureState({ source: 'zonal', id: state.selected }, { sel: true });
    if (fly) flyTo(bboxes.get(state.selected)!);
    renderCard(state.selected);
  } else {
    $('card').hidden = true;
  }
  writeHash();
}

function flyTo(b: BBox) {
  const mobile = innerWidth < 720;
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], {
    padding: mobile ? { top: 40, bottom: Math.round(map.getContainer().clientHeight * 0.5), left: 30, right: 30 } : { top: 60, bottom: 60, left: 60, right: 360 },
    maxZoom: 15, duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 700,
  });
}

function renderCard(id: string) {
  const p = byId.get(id)!.properties;
  const card = $('card');
  const v = valueOf(p);
  const q = QUALITY[p.q];
  const year = meta?.current_year ?? new Date().getFullYear();
  const own = p.q === 'barangay_data';
  const row = (label: string, val: string | undefined, na: string) =>
    `<dt>${label}</dt>${val ? `<dd>${val}</dd>` : `<dd class="na">${na}</dd>`}`;
  const where = [p.d, p.c, p.p].filter(Boolean).join(', ');
  card.innerHTML = `
    <div class="head"><div class="nm">${esc(p.n)}</div><button class="close" type="button" aria-label="Close">×</button></div>
    <div class="loc">${esc(where)}</div>
    <div class="big">${v != null ? `${fmt(v)}<small> /sqm · ${MODE_LABEL[state.mode].toLowerCase()}${!own && v != null ? ' (city median)' : ''}${state.basis === 'adj' ? `, ${year}` : ''}</small>` : '<small>No value</small>'}</div>
    <span class="pill ${q.cls}">${q.label}</span>
    <dl>
      ${row(own ? 'Typical' : 'City median', fmt(p.typ), 'no data')}
      ${row('Interior lot', fmt(p.lo), own ? 'no data' : 'no barangay detail')}
      ${row('Prime', fmt(p.hi), own ? 'no data' : 'no barangay detail')}
      ${p.typ_a != null ? row(`Typical, adjusted to ${year}`, fmt(p.typ_a), '') : ''}
      ${p.g != null ? row('Growth rate used', `${(p.g * 100).toFixed(1)}% a year`, '') : ''}
      ${row('Schedule effective', p.eff, 'not recorded')}
    </dl>
    <p class="q">${esc(q.text(p))}${own && p.typ_m === 'geomean' ? ' Typical is the geometric mean of interior and prime; street-level rows are not loaded yet.' : ''}${p.typ_a == null ? ` No ${year}-adjusted value or growth rate yet: that needs two or more BIR revisions for this district.` : ''}</p>`;
  card.hidden = false;
  card.querySelector<HTMLButtonElement>('.close')!.onclick = () => select(null);
}

// ---- legend ----
function renderLegend() {
  const grad = STOPS.map((c, i) => `${c} ${(i / (STOPS.length - 1) * 100).toFixed(0)}%`).join(',');
  $('legend').innerHTML = `
    <div>${MODE_LABEL[state.mode]} value, ₱/sqm${state.basis === 'adj' ? `, adjusted to ${meta?.current_year ?? ''}` : ''}</div>
    <div class="bar" style="background:linear-gradient(90deg,${grad});background:linear-gradient(in lab 90deg,${grad})"></div>
    <div class="ticks" style="position:relative;height:14px">${TICKS.map((t, i) => {
      const x = tickPos(t) * 100;
      const align = i === 0 ? 'left:0' : i === TICKS.length - 1 ? 'right:0' : `left:${x}%;transform:translateX(-50%)`;
      return `<span style="position:absolute;${align}">${short(t)}</span>`;
    }).join('')}</div>
    <div class="keys"><span><i class="sw hatch"></i>city median or estimate</span><span><i class="sw" style="background:var(--nodata)"></i>no data</span></div>`;
}

// ---- ranking (barangays with their own data, inside the current map view) ----
function renderRank() {
  if (!zonal) return;
  const b = map.getBounds();
  const k = key();
  const rows = zonal.features.filter(f => {
    const p = f.properties;
    if (p.q !== 'barangay_data' || p[k] == null) return false;
    const bb = bboxes.get(p.id)!;
    return b.contains([(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2]);
  }).sort((a, z) => (z.properties[k] as number) - (a.properties[k] as number));
  const list = state.order === 'top' ? rows.slice(0, 10) : rows.slice(-10).reverse();
  const label = MODE_LABEL[state.mode].toLowerCase();
  $('rank-h').textContent = `${state.order === 'top' ? 'Highest' : 'Lowest'} ${label} values in view`;
  $('rank-note').textContent = rows.length
    ? `Ranking ${rows.length.toLocaleString()} barangays with their own BIR values in the current map view. City-median, estimated and no-data areas are left out. Pan or zoom the map to change the list.`
    : 'No barangays with their own BIR values in the current map view. Zoom out or pan to Metro Manila.';
  const ol = $('rank');
  ol.innerHTML = '';
  list.forEach((f, i) => {
    const p = f.properties;
    const n = state.order === 'top' ? i + 1 : rows.length - i;
    const li = document.createElement('li');
    li.tabIndex = 0;
    li.innerHTML = `<span class="n">${String(n).padStart(2, '0')}</span><span>${esc(p.n)} <span class="c">${esc(p.c)}</span></span><span class="v">${short(p[k] as number)}</span>`;
    const go = () => { select(p.id, true); document.querySelector('.mapbox')!.scrollIntoView({ behavior: 'smooth', block: 'center' }); };
    li.onclick = go;
    li.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
    ol.appendChild(li);
  });
}

// ---- search ----
interface Hit { kind: 'barangay' | 'city'; id: string; label: string; sub: string; text: string; name: string }
let index: Hit[] = [];
let hits: Hit[] = [];
let active = -1;

function buildIndex() {
  index = [
    ...cities.features.map(f => ({ kind: 'city' as const, id: f.properties.id, label: f.properties.n, sub: f.properties.p, name: fold(f.properties.n), text: fold(`${f.properties.n} ${f.properties.p}`) })),
    ...zonal.features.map(f => ({ kind: 'barangay' as const, id: f.properties.id, label: f.properties.n, sub: f.properties.d ? `${f.properties.d}, ${f.properties.c}` : f.properties.c, name: fold(f.properties.n), text: fold(`${f.properties.n} ${f.properties.c} ${f.properties.d ?? ''}`) })),
  ];
}

function search(q: string): Hit[] {
  const tokens = fold(q).split(' ').filter(Boolean);
  if (!tokens.length) return [];
  const words = (s: string) => ` ${s}`;
  const scored: [number, Hit][] = [];
  for (const h of index) {
    const t = words(h.text);
    if (!tokens.every(tok => t.includes(` ${tok}`))) continue;
    let s = h.kind === 'city' ? 0 : 1;
    if (h.name === tokens.join(' ')) s -= 2;
    else if (h.name.startsWith(tokens[0])) s -= 1;
    scored.push([s, h]);
  }
  return scored.sort((a, b) => a[0] - b[0] || a[1].label.localeCompare(b[1].label, 'en', { numeric: true })).slice(0, 8).map(x => x[1]);
}

function renderResults() {
  const ul = $('results');
  const input = $<HTMLInputElement>('q');
  if (!input.value.trim()) { ul.hidden = true; input.setAttribute('aria-expanded', 'false'); return; }
  ul.innerHTML = hits.length ? hits.map((h, i) =>
    `<li role="option" id="r${i}" aria-selected="${i === active}" data-i="${i}"><span>${esc(h.label)}</span><span class="c">${h.kind === 'city' ? 'city · ' : ''}${esc(h.sub)}</span></li>`).join('')
    : '<li class="empty">No barangay or city by that name</li>';
  ul.hidden = false;
  input.setAttribute('aria-expanded', 'true');
  input.setAttribute('aria-activedescendant', active >= 0 ? `r${active}` : '');
}

function choose(h: Hit) {
  const input = $<HTMLInputElement>('q');
  input.value = h.label;
  hits = []; active = -1;
  renderResults();
  $('results').hidden = true;
  input.blur();
  if (h.kind === 'barangay') select(h.id, true);
  else {
    const c = cities.features.find(f => f.properties.id === h.id)!;
    select(null);
    flyTo(bboxOf(c.geometry));
  }
}

function wireSearch() {
  const input = $<HTMLInputElement>('q');
  const ul = $('results');
  input.addEventListener('input', () => { hits = search(input.value); active = hits.length ? 0 : -1; renderResults(); });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!hits.length) return;
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + hits.length) % hits.length;
      renderResults();
    } else if (e.key === 'Enter' && hits[active]) { e.preventDefault(); choose(hits[active]); }
    else if (e.key === 'Escape') { input.value = ''; hits = []; renderResults(); }
  });
  ul.addEventListener('mousedown', e => {
    const li = (e.target as HTMLElement).closest<HTMLElement>('li[data-i]');
    if (li) { e.preventDefault(); choose(hits[Number(li.dataset.i)]); }
  });
  input.addEventListener('blur', () => setTimeout(() => { ul.hidden = true; input.setAttribute('aria-expanded', 'false'); }, 120));
  input.addEventListener('focus', () => { if (input.value.trim()) renderResults(); });
}

// ---- controls + URL hash ----
function wireControls() {
  document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach(b => b.onclick = () => {
    state.mode = b.dataset.mode as Mode; pressed('[data-mode]', b); repaint();
  });
  document.querySelectorAll<HTMLButtonElement>('[data-basis]').forEach(b => b.onclick = () => {
    state.basis = b.dataset.basis as Basis; pressed('[data-basis]', b); repaint();
  });
  document.querySelectorAll<HTMLButtonElement>('[data-order]').forEach(b => b.onclick = () => {
    state.order = b.dataset.order as 'top' | 'bottom'; pressed('[data-order]', b); renderRank();
  });
  $('theme').onclick = () => {
    const next = theme() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch { /* private mode */ }
    applyTheme();
  };
  media.addEventListener('change', () => { if (!document.documentElement.dataset.theme) applyTheme(); });
}

let styleTheme = theme();
function applyTheme() {
  renderThemeButton();
  if (theme() === styleTheme) return;
  styleTheme = theme();
  map.setStyle(STYLES[styleTheme], { diff: false });
  renderLegend();
}

function pressed(sel: string, on: HTMLButtonElement) {
  document.querySelectorAll<HTMLButtonElement>(sel).forEach(x => x.setAttribute('aria-pressed', String(x === on)));
}

function writeHash() {
  const h = new URLSearchParams();
  if (state.selected) h.set('b', state.selected);
  if (state.mode !== 'typ') h.set('v', state.mode);
  if (state.basis !== 'raw') h.set('adj', '1');
  const s = h.toString();
  history.replaceState(null, '', s ? `#${s}` : location.pathname + location.search);
}

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  const v = h.get('v');
  state.mode = v === 'lo' || v === 'hi' ? v : 'typ';
  pressed('[data-mode]', document.querySelector(`[data-mode="${state.mode}"]`)!);
  if (h.get('adj') === '1' && meta.adjusted_available) { state.basis = 'adj'; pressed('[data-basis]', document.querySelector('[data-basis="adj"]')!); }
  return h.get('b');
}

// ---- boot ----
async function boot() {
  renderThemeButton();
  wireControls();
  wireSearch();
  const [z, c, m] = await Promise.all([
    fetch('/data/zonal.geojson').then(r => r.json()),
    fetch('/data/cities.geojson').then(r => r.json()),
    fetch('/data/meta.json').then(r => r.json()),
  ]);
  zonal = z; cities = c; meta = m;
  for (const f of zonal.features as Feature[]) { byId.set(f.properties.id, f); bboxes.set(f.properties.id, bboxOf(f.geometry)); }
  buildIndex();

  if (!meta.adjusted_available) {
    const adj = document.querySelector<HTMLButtonElement>('[data-basis="adj"]')!;
    adj.disabled = true;
    adj.title = meta.adjusted_note ?? 'Not available yet';
    const note = $('basis-note');
    note.textContent = 'Adjusted values arrive once two or more BIR revisions per district are loaded.';
    note.hidden = false;
  }
  const f = meta.coverage.by_flag;
  $('coverage').textContent = `Coverage: ${(f.barangay_data ?? 0).toLocaleString()} barangays with their own values, ` +
    `${(f.city_median ?? 0).toLocaleString()} on the city median, ${(f.estimated ?? 0).toLocaleString()} estimated, ` +
    `${(f.no_data ?? 0).toLocaleString()} with no data. Data built ${meta.built_at.slice(0, 10)}.`;

  const initial = readHash();
  renderLegend();
  if (styleReady) addLayers();
  // don't wait for 'idle': it never fires while the tab is in the background
  if (initial) select(initial, true);
  map.once('idle', () => renderRank());
  addEventListener('hashchange', () => {
    const id = readHash();
    repaint();
    if (id !== state.selected) select(id, true);
  });
  renderRank();
}

boot().catch(err => {
  console.error(err);
  $('legend').textContent = 'Could not load the map data. Try reloading the page.';
});

