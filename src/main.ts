import maplibregl from 'maplibre-gl';
import type { ExpressionSpecification, GeoJSONSource, MapGeoJSONFeature } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';

// ---- data shapes (see pipeline/build.py) ----
type Quality = 'barangay_data' | 'city_median' | 'estimated' | 'no_data';
interface Props {
  id: string; n: string; c: string; p: string; d?: string; q: Quality;
  lo?: number; hi?: number; typ?: number; typ_m?: 'median' | 'geomean';
  lo_a?: number; hi_a?: number; typ_a?: number; g?: number;
  eff?: string; do?: string; rdo?: string; ns?: number; src?: 'bir' | 'renph';
  yrs?: number; gs?: string; since?: string; nb?: number; nbn?: string; nores?: string;
}
type Geometry = { type: 'Polygon'; coordinates: number[][][] } | { type: 'MultiPolygon'; coordinates: number[][][][] };
interface Feature { type: 'Feature'; properties: Props; geometry: Geometry }
interface CityProps { id: string; n: string; p: string }
interface Collection<P> { type: 'FeatureCollection'; features: { type: 'Feature'; properties: P; geometry: Geometry }[] }
interface Meta {
  built_at: string; current_year: number; adjusted_available: boolean; adjusted_note?: string;
  schedule_range?: [string, string]; coverage: { features: number; by_flag: Partial<Record<Quality, number>> };
  adjusted_to?: string; bir?: { department_orders_used: number; rdos: string[] };
  sources_as_of?: { name: string; as_of: string[]; note: string; url: string }[];
}
type Mode = 'typ' | 'lo' | 'hi';
type Basis = 'raw' | 'adj';
type BBox = [number, number, number, number];

// ---- scale ----
const STOPS = ['#e9f2e1', '#b5dcb4', '#63b39d', '#2d8590', '#265a8a', '#2c2f6e'];
const DOMAIN: [number, number] = [2500, 2000000];  // adjusted commercial values reach ~1.7M
const TICKS = [2500, 10000, 50000, 250000, 2000000];
const STYLES = {
  light: 'https://tiles.openfreemap.org/styles/positron',
  dark: 'https://tiles.openfreemap.org/styles/dark',
};
const NCR_BOUNDS: [[number, number], [number, number]] = [[120.93, 14.38], [121.13, 14.78]];
const MODE_LABEL: Record<Mode, string> = { typ: 'Typical', lo: 'Interior lot', hi: 'Prime' };
const ROUGH_YEARS = 10;
// Satellite imagery: Esri World Imagery when a key is configured (VITE_ESRI_KEY, see README), otherwise the
// openly licensed (CC BY 4.0) Sentinel-2 cloudless 2016 mosaic by EOX, which is 10 m per pixel.
const ESRI_KEY = import.meta.env.VITE_ESRI_KEY as string | undefined;
const SATELLITE = ESRI_KEY
  ? {
    name: 'Esri World Imagery',
    tiles: [`https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?token=${encodeURIComponent(ESRI_KEY)}`],
    maxzoom: 19,
    attribution: 'Imagery powered by <a href="https://www.esri.com" target="_blank" rel="noopener">Esri</a>: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
  }
  : {
    name: 'Sentinel-2 cloudless 2016 (10 m)',
    tiles: ['https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg'],
    maxzoom: 15,
    attribution: '<a href="https://s2maps.eu" target="_blank" rel="noopener">Sentinel-2 cloudless - https://s2maps.eu</a> by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016 &amp; 2017)',
  };  // projections longer than this are marked rough (same as pipeline/report_staleness.py)
const QUALITY: Record<Quality, { label: string; cls: string; text: (p: Props) => string }> = {
  barangay_data: {
    label: 'barangay data', cls: 'good',
    text: p => p.src === 'bir'
      ? `From BIR ${p.do} (${p.rdo?.replace(/^RDO-0*/, 'RDO ')}): ${p.ns} ${useWord()} land ${p.ns === 1 ? 'value' : 'values'} for this barangay. Typical is their median.`
      : "From REN.PH's index of the BIR schedule. Typical is the geometric mean of interior and prime, because street-level rows are not available.",
  },
  city_median: { label: 'city median only', cls: 'warn', text: p => `No BIR schedule matched this barangay. This is the median of ${p.c}'s barangays, not this barangay's own value.` },
  estimated: {
    label: 'estimated', cls: 'warn',
    text: p => `No BIR value for this barangay. These are estimates: the median of the ${p.nb} neighbouring ${p.nb === 1 ? 'barangay' : 'barangays'} with their own values (${p.nbn}).`,
  },
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
  use: 'res' as Use,
  order: 'top' as 'top' | 'bottom',
  satellite: false,
  transport: false,
  condos: false,
  by: 'value' as By,
  selected: null as string | null,
  hovered: null as string | null,
};
let zonal: Collection<Props>;
let cities: Collection<CityProps>;
let meta: Meta;
// Street- and village-level values (pipeline/streets.py), one file per city, loaded when zoomed in
interface StreetProps { n: string; osm: string; v: number; v_a?: number; vmin?: number; vmin_a?: number; rows?: number;
  k: 'village' | 'segment' | 'street'; u: 'r' | 'c' | 'i'; b: string; c: string; do: string; eff?: string; vic?: string; g?: number }
interface StreetIndex { [city: string]: { file: string; bbox: [number, number, number, number]; features: number } }
let streetIndex: StreetIndex = {};
const streetFeatures: Collection<StreetProps>['features'] = [];
const streetLoaded = new Set<string>();
// ---- colour by other factors (pipeline/factors.py; vacancy from data/vacancy_submarkets.csv) ----
type By = 'value' | 'elev' | 'heat' | 'flood' | 'rail' | 'xway' | 'vaco' | 'vacc';
interface Factor { prop: string; label: string; breaks: number[]; colors: string[]; fmt: (v: number) => string; source: string }
const km = (v: number) => `${v < 10 ? v.toFixed(1) : v.toFixed(0)} km`;
const FACTORS: Record<Exclude<By, 'value'>, Factor> = {
  elev: { prop: 'f_elev', label: 'Average elevation', breaks: [2, 5, 10, 20, 50, 150],
    colors: ['#084594', '#4292c6', '#9ecae1', '#d9f0a3', '#78c679', '#31a354', '#8c6d31'], fmt: v => `${v.toFixed(0)} m`,
    source: 'Copernicus GLO-30 surface model (includes buildings and trees)' },
  heat: { prop: 'f_heat', label: 'Surface heat, dry season', breaks: [36, 40, 44, 48, 52],
    colors: ['#ffffb2', '#fed976', '#feb24c', '#fd8d3c', '#f03b20', '#bd0026'], fmt: v => `${v.toFixed(1)} °C`,
    source: 'Landsat 8/9 land surface temperature, March–May 2023–25 (USGS)' },
  flood: { prop: 'f_flood', label: 'Flood hazard, 100-year rain', breaks: [0.05, 0.15, 0.3, 0.5, 0.7],
    colors: ['#f7fbff', '#c6dbef', '#6baed6', '#3182bd', '#08519c', '#08306b'], fmt: v => `${Math.round(v * 100)}% of area`,
    source: 'UP NOAH / DREAM flood hazard maps (share of area in medium or high hazard)' },
  rail: { prop: 'f_rail', label: 'Distance to rail station', breaks: [0.5, 1, 2, 5, 10],
    colors: ['#005a32', '#238443', '#41ab5d', '#addd8e', '#e5e5c4', '#d9d9d9'], fmt: km, source: 'OpenStreetMap stations' },
  xway: { prop: 'f_xway', label: 'Distance to expressway', breaks: [0.5, 1, 2, 5, 10],
    colors: ['#7f2704', '#d94801', '#fd8d3c', '#fdd0a2', '#f0ebd8', '#d9d9d9'], fmt: km, source: 'OpenStreetMap expressways' },
  vaco: { prop: 'f_vaco', label: 'Office vacancy', breaks: [5, 10, 15, 20, 30],
    colors: ['#1a9850', '#91cf60', '#d9ef8b', '#fee08b', '#fc8d59', '#d73027'], fmt: v => `${v}%`,
    source: 'Broker reports by submarket (see data/vacancy_submarkets.csv)' },
  vacc: { prop: 'f_vacc', label: 'Condo vacancy', breaks: [5, 10, 15, 20, 30],
    colors: ['#1a9850', '#91cf60', '#d9ef8b', '#fee08b', '#fc8d59', '#d73027'], fmt: v => `${v}%`,
    source: 'Broker reports by submarket (see data/vacancy_submarkets.csv)' },
};
const factor = () => state.by === 'value' ? null : FACTORS[state.by];

function factorColor(f: Factor): ExpressionSpecification {
  const steps = f.colors.flatMap((c, i) => i === 0 ? [c] : [f.breaks[i - 1], c]);
  return ['case', ['!=', ['typeof', ['get', f.prop]], 'number'], cssVar('--nodata'),
    ['step', ['get', f.prop], ...steps] as unknown as ExpressionSpecification];
}

// ---- condominiums (pipeline/condos.py): one file per city, loaded on zoom when the toggle is on ----
interface CondoProps { n: string; osm: string; v: number; v_a?: number; u: 'r' | 'c'; b: string; c: string; do: string; eff?: string; g?: number }
let condoIndex: StreetIndex = {};
const condoFeatures: Collection<CondoProps>['features'] = [];
const condoLoaded = new Set<string>();
const CONDO_MINZOOM = 12.5;

async function loadCondosInView() {
  if (!state.condos || map.getZoom() < CONDO_MINZOOM - 0.5) return;
  const b = map.getBounds();
  const todo = Object.entries(condoIndex).filter(([c, v]) => !condoLoaded.has(c)
    && v.bbox[0] < b.getEast() && v.bbox[2] > b.getWest() && v.bbox[1] < b.getNorth() && v.bbox[3] > b.getSouth());
  if (!todo.length) return;
  todo.forEach(([c]) => condoLoaded.add(c));
  const got = await Promise.all(todo.map(([, v]) => fetch(`/data/condos/${v.file}`, { cache: 'no-cache' }).then(r => r.json()).catch(() => null)));
  for (const g of got) if (g) condoFeatures.push(...g.features);
  (map.getSource('condos') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: condoFeatures } as never);
}

function addCondoLayers() {
  if (map.getSource('condos')) return;
  const dark = theme() === 'dark';
  const vis = state.condos ? 'visible' : 'none';
  map.addSource('condos', { type: 'geojson', data: { type: 'FeatureCollection', features: condoFeatures } as never });
  map.addLayer({ id: 'cd-dot', type: 'circle', source: 'condos', minzoom: CONDO_MINZOOM, filter: useFilter(['has', 'v']),
    layout: { visibility: vis },
    paint: { 'circle-color': streetColor(), 'circle-radius': ['interpolate', ['linear'], ['zoom'], 12.5, 3, 16, 8],
      'circle-stroke-color': dark ? '#0b100f' : '#ffffff', 'circle-stroke-width': 1.5 } });
  const fontLayer = map.getStyle().layers.find(l => l.type === 'symbol' && Array.isArray(l.layout?.['text-font']));
  const font = (fontLayer?.type === 'symbol' ? fontLayer.layout!['text-font'] as string[] : undefined) ?? ['Noto Sans Regular'];
  map.addLayer({ id: 'cd-label', type: 'symbol', source: 'condos', minzoom: 15.5, filter: useFilter(['has', 'v']),
    layout: { visibility: vis, 'text-field': ['concat', ['get', 'osm'], '\n', streetLabel()], 'text-font': font, 'text-size': 11,
      'text-offset': [0, 1.2], 'text-anchor': 'top', 'text-optional': true },
    paint: { 'text-color': dark ? '#e4ecea' : '#17201f', 'text-halo-color': dark ? 'rgba(16,22,21,.9)' : 'rgba(255,255,255,.9)', 'text-halo-width': 1.4 } });
}

async function setCondos(on: boolean) {
  state.condos = on;
  $('cd').setAttribute('aria-pressed', String(on));
  for (const id of ['cd-dot', 'cd-label']) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
  if (on) await loadCondosInView();
  renderLegend();
  writeHash();
}

function renderCondoCard(p: CondoProps) {
  const card = $('card');
  const year = meta?.current_year ?? new Date().getFullYear();
  card.innerHTML = `
    <div class="head"><div class="nm">${esc(p.osm)}</div><button class="close" type="button" aria-label="Close">×</button></div>
    <div class="loc">${esc(p.b)}, ${esc(p.c)}</div>
    <div class="big">${fmt(state.basis === 'adj' && p.v_a != null ? p.v_a : p.v)}<small> /sqm of floor area${state.basis === 'adj' ? `, adjusted to ${year}` : ''}</small></div>
    <span class="pill good">${p.u === 'c' ? 'commercial' : 'residential'} condominium</span>
    <dl>
      <dt></dt><dd class="na">as scheduled${p.v_a != null ? ` → ${year}` : ''}</dd>
      <dt>Per sqm of unit</dt><dd>${fmt(p.v)}${p.v_a != null ? ` → ${fmt(p.v_a)}` : ''}</dd>
      ${p.g != null ? `<dt>Growth rate used</dt><dd>${(p.g * 100).toFixed(1)}% a year</dd>` : ''}
      <dt>Schedule effective</dt><dd>${esc(p.eff || '')}</dd>
      <dt>Department Order</dt><dd>${esc(p.do.replace('DO ', ''))}</dd>
    </dl>
    <p class="q">BIR's zonal value for units in "${esc(p.n)}", per square metre of floor area, not land. Placed on the
    OpenStreetMap building of that name; a tax floor, not a market price.</p>`;
  card.hidden = false;
  card.querySelector<HTMLButtonElement>('.close')!.onclick = () => { card.hidden = true; };
}

// Roads & rail overlay (pipeline/transport.py): loaded on first use
let transport: object | null = null;
const TR_COLORS = { expressway: '#d9480f', road: '#868e96', rail: '#7048e8' };

async function setTransport(on: boolean) {
  state.transport = on;
  $('tr').setAttribute('aria-pressed', String(on));
  if (on && !transport) {
    transport = await fetch('/data/transport.geojson', { cache: 'no-cache' }).then(r => r.json()).catch(() => null);
    if (transport && map.getLayer('z-fill') && !map.getSource('transport')) addTransportLayers();
  }
  for (const id of ['tr-casing', 'tr-line', 'tr-station', 'tr-label', 'tr-label-st']) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
  }
  renderLegend();
  writeHash();
}

function addTransportLayers() {
  if (!transport || map.getSource('transport')) return;
  const dark = theme() === 'dark';
  const vis = state.transport ? 'visible' : 'none';
  const lines: ExpressionSpecification = ['!=', ['get', 'k'], 'station'];
  const width: ExpressionSpecification = ['interpolate', ['linear'], ['zoom'], 9,
    ['match', ['get', 'k'], 'road', 0.6, 1.4], 15, ['match', ['get', 'k'], 'road', 2.5, 5]];
  const before = map.getStyle().layers.find(l => l.id === 'z-focus')?.id;
  map.addSource('transport', { type: 'geojson', data: transport as never });
  map.addLayer({ id: 'tr-casing', type: 'line', source: 'transport', filter: ['all', lines, ['==', ['get', 's'], 'open']],
    layout: { visibility: vis, 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': dark ? '#0b100f' : '#ffffff', 'line-opacity': 0.8,
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 2.5, 15, 8] } }, before);
  map.addLayer({ id: 'tr-line', type: 'line', source: 'transport', filter: lines,
    layout: { visibility: vis, 'line-join': 'round' },
    paint: {
      'line-color': ['match', ['get', 'k'], 'expressway', TR_COLORS.expressway, 'rail', TR_COLORS.rail, TR_COLORS.road],
      'line-opacity': ['match', ['get', 's'], 'open', 0.9, 0.75],
      'line-width': width,
      'line-dasharray': ['match', ['get', 's'], 'construction', ['literal', [2, 1.5]], 'proposed', ['literal', [0.6, 1.6]], ['literal', [1, 0]]],
    } }, before);
  map.addLayer({ id: 'tr-station', type: 'circle', source: 'transport', minzoom: 11, filter: ['==', ['get', 'k'], 'station'],
    layout: { visibility: vis },
    paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 3, 15, 6], 'circle-color': '#ffffff',
      'circle-stroke-color': TR_COLORS.rail, 'circle-stroke-width': 2 } });
  const fontLayer = map.getStyle().layers.find(l => l.type === 'symbol' && Array.isArray(l.layout?.['text-font']));
  const font = (fontLayer?.type === 'symbol' ? fontLayer.layout!['text-font'] as string[] : undefined) ?? ['Noto Sans Regular'];
  const text = { 'text-color': dark ? '#e4ecea' : '#17201f', 'text-halo-color': dark ? 'rgba(16,22,21,.9)' : 'rgba(255,255,255,.9)', 'text-halo-width': 1.4 };
  map.addLayer({ id: 'tr-label', type: 'symbol', source: 'transport', minzoom: 12,
    filter: ['all', ['!=', ['get', 'n'], ''], ['in', ['get', 'k'], ['literal', ['expressway', 'rail']]]],
    layout: { visibility: vis, 'text-field': ['get', 'n'], 'text-font': font, 'text-size': 11, 'symbol-placement': 'line' },
    paint: text });
  map.addLayer({ id: 'tr-label-st', type: 'symbol', source: 'transport', minzoom: 13, filter: ['==', ['get', 'k'], 'station'],
    layout: { visibility: vis, 'text-field': ['get', 'n'], 'text-font': font, 'text-size': 11, 'text-offset': [0, 1.1] },
    paint: text });
}

function renderTransportCard(p: { k: string; s: string; n: string; ref?: string; open?: string }) {
  const card = $('card');
  const what = { expressway: 'Expressway', road: 'Major road', rail: 'Rail line', station: 'Rail station' }[p.k] ?? p.k;
  const status = { open: 'open', construction: 'under construction', proposed: 'proposed' }[p.s] ?? p.s;
  card.innerHTML = `
    <div class="head"><div class="nm">${esc(p.n || p.ref || what)}</div><button class="close" type="button" aria-label="Close">×</button></div>
    <div class="loc">${what}${p.ref && p.n ? ` · ${esc(p.ref)}` : ''}</div>
    <span class="pill ${p.s === 'open' ? 'good' : 'warn'}">${status}${p.open ? `, opening ${esc(p.open)}` : ''}</span>
    <p class="q">From OpenStreetMap. Future alignments are mappers' best information and can change; check the
    implementing agency (DPWH, DOTr or the concessionaire) for dates.</p>`;
  card.hidden = false;
  card.querySelector<HTMLButtonElement>('.close')!.onclick = () => { card.hidden = true; };
}

const STREET_MINZOOM = 11.5;           // street/village layers exist from here, fully transparent...
const STREET_FADE: [number, number] = [12.5, 14];  // ...and cross-fade with the barangay colours over this range
const STREET_PREFETCH = 11;           // city files load a little before they show, so nothing pops in
const fadeIn = (full: number): ExpressionSpecification => ['interpolate', ['linear'], ['zoom'], STREET_MINZOOM, 0, STREET_FADE[0] + 0.5, full];
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

// commercial and industrial values sit next to residential ones as c_lo, i_typ_a, ... (pipeline/build.py)
type Use = 'res' | 'com' | 'ind';
const USE_PREFIX: Record<Use, string> = { res: '', com: 'c_', ind: 'i_' };
const USE_CODE: Record<Use, string> = { res: 'r', com: 'c', ind: 'i' };
const USE_WORD: Record<Use, string> = { res: 'residential', com: 'commercial', ind: 'industrial' };
const pre = () => USE_PREFIX[state.use];
const key = (m: Mode = state.mode, basis: Basis = state.basis) => `${pre()}${basis === 'adj' ? `${m}_a` : m}` as keyof Props;
const qKey = () => `${pre()}q`;
const useWord = () => USE_WORD[state.use];
/** The feature as the card and ranking should see it for the current use. */
function view(p: Props): Props {
  if (state.use === 'res') return p;
  const c = p as unknown as Record<string, unknown>;
  const x = (k: string) => c[`${pre()}${k}`];
  return { ...p, q: (x('q') as Quality | undefined) ?? 'no_data', lo: x('lo') as number, hi: x('hi') as number, typ: x('typ') as number,
    lo_a: x('lo_a') as number, hi_a: x('hi_a') as number, typ_a: x('typ_a') as number, g: x('g') as number, yrs: x('yrs') as number,
    eff: x('eff') as string, do: x('do') as string, ns: x('ns') as number, nb: x('nb') as number, src: x('q') === 'barangay_data' ? 'bir' : undefined,
    typ_m: 'median', since: undefined, gs: undefined, nores: undefined, nbn: x('q') === 'estimated' ? 'see map' : p.nbn };
}
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
  maxBounds: [[119.4, 13.0], [122.6, 16.0]],  // Batangas's southern tip (Verde Island ~13.5) to north of Angeles
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

function colorOf(k: string): ExpressionSpecification {
  const v: ExpressionSpecification = ['coalesce', ['get', k], ['get', key('typ')]];
  return ['case', ['!=', ['typeof', v], 'number'], cssVar('--nodata'),
    ['interpolate-lab', ['linear'], ['ln', v], ...lnStops] as unknown as ExpressionSpecification];
}

// Up close, a barangay in "typical" view shifts to its interior-lot value: what BIR charges on streets it does not
// list, so the village and street values drawn on top read against the right background.
function fillColor(): ExpressionSpecification {
  const f = factor();
  if (f) return factorColor(f);
  if (state.mode !== 'typ') return colorOf(key());
  return ['interpolate', ['linear'], ['zoom'], STREET_FADE[0], colorOf(key()), STREET_FADE[1], colorOf(key('lo'))];
}

const hatchFilter = (): ExpressionSpecification =>
  ['in', ['coalesce', ['get', qKey()], 'none'], ['literal', ['city_median', 'estimated']]];
const useFilter = (k: ExpressionSpecification): ExpressionSpecification =>
  ['all', k, ['==', ['get', 'u'], USE_CODE[state.use]]];

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

  // imagery sits above the basemap's geometry and below the zonal colours and the basemap's labels
  map.addSource('sat', { type: 'raster', tiles: SATELLITE.tiles, tileSize: 256, maxzoom: SATELLITE.maxzoom,
    attribution: SATELLITE.attribution });
  map.addLayer({ id: 'sat', type: 'raster', source: 'sat', layout: { visibility: state.satellite ? 'visible' : 'none' } }, before);
  // the upstream barangay polygons are simplified and leave slivers; city polygons underneath hide them
  map.addLayer({
    id: 'c-fill', type: 'fill', source: 'cities',
    paint: { 'fill-color': cssVar('--nodata'), 'fill-opacity': state.satellite ? 0 : 0.7 },
  }, before);
  map.addLayer({
    id: 'z-fill', type: 'fill', source: 'zonal',
    paint: { 'fill-color': fillColor(), 'fill-opacity': fillOpacity() },
  }, before);
  map.addLayer({
    id: 'z-hatch', type: 'fill', source: 'zonal',
    filter: hatchFilter(),
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
  {
    map.addSource('streets', { type: 'geojson', data: { type: 'FeatureCollection', features: streetFeatures } as never });
    map.addLayer({
      id: 'st-fill', type: 'fill', source: 'streets', minzoom: STREET_MINZOOM, filter: useFilter(['==', ['get', 'k'], 'village']),
      paint: { 'fill-color': streetColor(), 'fill-opacity': fadeIn(state.satellite ? 0.6 : 0.9) },
    }, before);
    map.addLayer({
      id: 'st-outline', type: 'line', source: 'streets', minzoom: STREET_MINZOOM, filter: useFilter(['==', ['get', 'k'], 'village']),
      paint: { 'line-color': dark ? '#e4ecea' : '#17201f', 'line-opacity': fadeIn(0.85), 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 1, 16, 2] },
    }, before);
    map.addLayer({  // dark edge under street lines so pale (cheap) streets still read against the map
      id: 'st-casing', type: 'line', source: 'streets', minzoom: STREET_MINZOOM, filter: useFilter(['!=', ['get', 'k'], 'village']),
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': dark ? '#0b100f' : '#17201f', 'line-opacity': fadeIn(0.8),
        'line-width': ['interpolate', ['linear'], ['zoom'], 13, 5, 16, 10] },
    }, before);
    map.addLayer({
      id: 'st-line', type: 'line', source: 'streets', minzoom: STREET_MINZOOM, filter: useFilter(['!=', ['get', 'k'], 'village']),
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-opacity': fadeIn(1),
        'line-color': streetColor(),
        'line-width': ['interpolate', ['linear'], ['zoom'], 13, 3, 16, 7],
        'line-dasharray': ['case', ['==', ['get', 'k'], 'street'], ['literal', [2, 1]], ['literal', [1, 0]]],
      },
    }, before);
    map.addLayer({  // "₱60K" on villages once they are big enough to read
      id: 'st-label', type: 'symbol', source: 'streets', minzoom: 14.5, filter: useFilter(['==', ['get', 'k'], 'village']),
      layout: { 'text-field': streetLabel(), 'text-font': labelFont, 'text-size': 12, 'text-padding': 6 },
      paint: { 'text-color': dark ? '#e4ecea' : '#17201f', 'text-halo-color': dark ? 'rgba(16,22,21,.9)' : 'rgba(255,255,255,.9)',
        'text-halo-width': 1.5 },
    });
  }
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
  addTransportLayers();
  addCondoLayers();
}

// with imagery on, the colours fade so roofs and greenery show through
// and at street zoom the barangay colours fade further, so village and street values stand out
const fillOpacity = (): ExpressionSpecification => {
  if (factor()) return ['literal', state.satellite ? 0.55 : 0.85] as unknown as ExpressionSpecification;
  const q: ExpressionSpecification = ['coalesce', ['get', qKey()], 'no_data'];
  const base: ExpressionSpecification = state.satellite
    ? ['match', q, 'barangay_data', 0.5, 'no_data', 0.25, 0.3]
    : ['match', q, 'barangay_data', 0.84, 'no_data', 0.7, 0.5];
  return ['interpolate', ['linear'], ['zoom'], STREET_FADE[0], base, STREET_FADE[1], state.satellite ? 0.2 : 0.4];
};

function applySatellite() {
  $('sat').setAttribute('aria-pressed', String(state.satellite));
  if (map.getLayer('sat')) {
    map.setLayoutProperty('sat', 'visibility', state.satellite ? 'visible' : 'none');
    map.setPaintProperty('c-fill', 'fill-opacity', state.satellite ? 0 : 0.7);
    map.setPaintProperty('z-fill', 'fill-opacity', fillOpacity());
    if (map.getLayer('st-fill')) map.setPaintProperty('st-fill', 'fill-opacity', fadeIn(state.satellite ? 0.6 : 0.9));
  }
  renderLegend();
  writeHash();
}

async function loadStreetsInView() {
  if (map.getZoom() < STREET_PREFETCH) return;
  const b = map.getBounds();
  const todo = Object.entries(streetIndex).filter(([c, v]) => !streetLoaded.has(c)
    && v.bbox[0] < b.getEast() && v.bbox[2] > b.getWest() && v.bbox[1] < b.getNorth() && v.bbox[3] > b.getSouth());
  if (!todo.length) return;
  todo.forEach(([c]) => streetLoaded.add(c));
  const got = await Promise.all(todo.map(([, v]) => fetch(`/data/streets/${v.file}`, { cache: 'no-cache' })
    .then(r => r.json()).catch(() => null)));
  for (const g of got) if (g) streetFeatures.push(...g.features);
  (map.getSource('streets') as GeoJSONSource | undefined)?.setData({ type: 'FeatureCollection', features: streetFeatures } as never);
}

function streetLabel(): ExpressionSpecification {
  const v: ExpressionSpecification = ['coalesce', ['get', state.basis === 'adj' ? 'v_a' : 'v'], ['get', 'v']];
  return ['concat', '₱', ['to-string', ['round', ['/', v, 1000]]], 'K'];
}

/** Re-apply everything that depends on residential/commercial or as-scheduled/adjusted. */
function applyUseAndBasis() {
  if (!map.getLayer('z-fill')) return;
  const showValues = state.by === 'value' ? 'visible' : 'none';
  for (const id of ['z-hatch', 'st-fill', 'st-outline', 'st-casing', 'st-line', 'st-label']) {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', showValues);
  }
  map.setFilter('z-hatch', hatchFilter());
  map.setPaintProperty('z-fill', 'fill-opacity', fillOpacity());
  if (map.getLayer('st-fill')) {
    map.setFilter('st-fill', useFilter(['==', ['get', 'k'], 'village']));
    map.setFilter('st-outline', useFilter(['==', ['get', 'k'], 'village']));
    map.setFilter('st-casing', useFilter(['!=', ['get', 'k'], 'village']));
    map.setFilter('st-line', useFilter(['!=', ['get', 'k'], 'village']));
    map.setFilter('st-label', useFilter(['==', ['get', 'k'], 'village']));
    map.setLayoutProperty('st-label', 'text-field', streetLabel());
  }
  if (map.getLayer('cd-dot')) {
    map.setFilter('cd-dot', useFilter(['has', 'v']));
    map.setFilter('cd-label', useFilter(['has', 'v']));
    map.setPaintProperty('cd-dot', 'circle-color', streetColor());
    map.setLayoutProperty('cd-label', 'text-field', ['concat', ['get', 'osm'], '\n', streetLabel()]);
  }
}

function streetColor(): ExpressionSpecification {
  const v: ExpressionSpecification = ['coalesce', ['get', state.basis === 'adj' ? 'v_a' : 'v'], ['get', 'v']];
  return ['interpolate-lab', ['linear'], ['ln', v], ...lnStops] as unknown as ExpressionSpecification;
}

function repaint() {
  applyUseAndBasis();
  if (map.getLayer('z-fill')) map.setPaintProperty('z-fill', 'fill-color', fillColor());
  if (map.getLayer('st-fill')) {
    map.setPaintProperty('st-fill', 'fill-color', streetColor());
    map.setPaintProperty('st-line', 'line-color', streetColor());
  }
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
  if (state.condos && map.getLayer('cd-dot')) {
    const box: [[number, number], [number, number]] = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
    const cf = map.queryRenderedFeatures(box, { layers: ['cd-dot'] })[0];
    if (cf) { renderCondoCard(cf.properties as unknown as CondoProps); return; }
  }
  if (state.transport && map.getLayer('tr-line')) {
    const box: [[number, number], [number, number]] = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
    const tf = map.queryRenderedFeatures(box, { layers: ['tr-station', 'tr-line'] })[0];
    if (tf) { renderTransportCard(tf.properties as never); return; }
  }
  if (map.getLayer('st-line')) {
    const box: [[number, number], [number, number]] = [[e.point.x - 6, e.point.y - 6], [e.point.x + 6, e.point.y + 6]];
    const sf = map.queryRenderedFeatures(box, { layers: ['st-line'] })[0] ?? map.queryRenderedFeatures(e.point, { layers: ['st-fill'] })[0];
    if (sf) { renderStreetCard(sf.properties as unknown as StreetProps); return; }
  }
  const f = map.queryRenderedFeatures(e.point, { layers: ['z-fill'] })[0];
  select(f ? String(f.id) : null);
});
map.on('moveend', () => { renderRank(); void loadStreetsInView(); void loadCondosInView(); renderLegend(); });

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

function renderStreetCard(p: StreetProps) {
  const card = $('card');
  const year = meta?.current_year ?? new Date().getFullYear();
  const adj = state.basis === 'adj';
  const v = adj && p.v_a != null ? p.v_a : p.v;
  const range = (lo?: number, hi?: number) => lo != null && lo !== hi ? `${fmt(lo)} – ${fmt(hi)}` : fmt(hi);
  const kind = { village: 'village / subdivision', segment: 'street, placed stretch', street: 'street, whole length shown' }[p.k];
  const where = { village: `BIR's value for lots in this subdivision, placed on the OpenStreetMap outline named "${p.osm}".`,
    segment: `BIR's value for lots along this stretch of ${p.osm} (${p.vic}).`,
    street: `BIR's value for lots along ${p.osm}${p.vic ? ` (BIR: "${p.vic}")` : ''}. BIR's stretch could not be pinned down, so the whole street inside the barangay is drawn, dashed.` }[p.k];
  card.innerHTML = `
    <div class="head"><div class="nm">${esc(p.n)}</div><button class="close" type="button" aria-label="Close">×</button></div>
    <div class="loc">${esc(p.b)}, ${esc(p.c)}</div>
    <div class="big">${fmt(v)}<small> /sqm${adj ? `, adjusted to ${year}` : ''}</small></div>
    <span class="pill good">${kind}</span>
    <dl>
      <dt></dt><dd class="na">as scheduled${p.v_a != null ? ` → ${year}` : ''}</dd>
      <dt>${p.u === 'c' ? 'Commercial' : p.u === 'i' ? 'Industrial' : 'Residential'} land</dt><dd>${range(p.vmin, p.v)}${p.v_a != null ? ` → ${range(p.vmin_a, p.v_a)}` : ''}</dd>
      ${p.g != null ? `<dt>Growth rate used</dt><dd>${(p.g * 100).toFixed(1)}% a year</dd>` : ''}
      <dt>Schedule effective</dt><dd>${esc(p.eff || '')}</dd>
      <dt>Department Order</dt><dd>${esc(p.do.replace('DO ', ''))}</dd>
    </dl>
    <p class="q">${esc(where)}${p.rows ? ` BIR lists ${p.rows} values for this street with different vicinities; the range is shown.` : ''} Placed by matching BIR's names to OpenStreetMap, so it can be wrong where names differ; the barangay value stays the reference.</p>`;
  card.hidden = false;
  card.querySelector<HTMLButtonElement>('.close')!.onclick = () => { card.hidden = true; };
}

function renderCard(id: string) {
  const raw = byId.get(id)!.properties;
  const p = view(raw);
  const other = state.use === 'res' ? (raw as unknown as { c_typ?: number }).c_typ : raw.typ;
  const card = $('card');
  const v = valueOf(p);
  const q = QUALITY[p.q];
  const year = meta?.current_year ?? new Date().getFullYear();
  const own = p.q === 'barangay_data';
  const est = p.q === 'estimated';
  const adj = state.basis === 'adj';
  const row = (label: string, val: string | undefined, na: string) =>
    `<dt>${label}</dt>${val ? `<dd>${val}</dd>` : `<dd class="na">${na}</dd>`}`;
  const pair = (a?: number, b?: number) => a == null ? undefined : `${fmt(a)}${b != null ? ` → ${fmt(b)}` : ''}`;
  const where = [p.d, p.c, p.p].filter(Boolean).join(', ');
  const kind = p.q === 'city_median' ? ' (city median)' : est ? ' (estimate)' : '';
  const rough = p.yrs != null && p.yrs > ROUGH_YEARS;
  const rdo = p.rdo?.replace(/^RDO-0*/, 'RDO ');
  const growth = p.g != null
    ? `${(p.g * 100).toFixed(1)}% a year${p.gs ? ` (${p.gs})` : rdo ? ` (${rdo})` : ''}`
    : undefined;
  card.innerHTML = `
    <div class="head"><div class="nm">${esc(p.n)}</div><button class="close" type="button" aria-label="Close">×</button></div>
    <div class="loc">${esc(where)}</div>
    <div class="big">${v != null ? `${fmt(v)}<small> /sqm · ${MODE_LABEL[state.mode].toLowerCase()}${kind}${adj ? `, adjusted to ${year}` : ''}</small>` : '<small>No value</small>'}</div>
    <span class="pill ${q.cls}">${q.label}</span>${rough ? ` <span class="pill warn">rough adjustment</span>` : ''}
    <dl>
      <dt></dt><dd class="na">as scheduled${p.typ_a != null ? ` → ${year}` : ''}</dd>
      ${row(p.q === 'city_median' ? 'City median' : 'Typical', pair(p.typ, p.typ_a), 'no data')}
      ${row('Interior lot', pair(p.lo, p.lo_a), own || est ? 'no data' : 'no barangay detail')}
      ${row('Prime', pair(p.hi, p.hi_a), own || est ? 'no data' : 'no barangay detail')}
      ${growth ? row('Growth rate used', growth, '') : ''}
      ${!est ? row('Schedule effective', p.eff, 'not recorded') : ''}
      ${other != null ? row(`${state.use === 'res' ? 'Commercial' : 'Residential'} typical`, fmt(other), '') : ''}
      ${p.since ? row('Values set in', p.since, '') : ''}
      ${p.do ? row('Department Order', `${p.do.replace('DO ', '')}${rdo ? ` · ${rdo}` : ''}`, '') : ''}
      ${Object.values(FACTORS).map(f => {
        const v = (raw as unknown as Record<string, unknown>)[f.prop];
        return typeof v === 'number' ? row(f.label, f.fmt(v), '') : '';
      }).join('')}
    </dl>
    <p class="q">${p.nores ? `BIR's current schedule (${esc(p.nores)}) lists no residential land here, only commercial or industrial values, so older residential values are not used. ` : ''}${esc(q.text(p))}${p.since ? ` The ${p.do} schedule repeats the values of ${p.since.split(',')[0]}, so the adjustment starts from ${p.since.split(', ')[1]}.` : ''}${
      p.yrs != null ? ` Adjusted values grow the schedule ${p.yrs.toFixed(0)} ${p.yrs < 1.5 ? 'year' : 'years'} at the rate BIR raised the same streets between its last two revisions${rough ? '. That is a long projection; treat it as rough' : ''}.` : ''}</p>`;
  card.hidden = false;
  card.querySelector<HTMLButtonElement>('.close')!.onclick = () => select(null);
}

// ---- legend ----
function renderLegend() {
  const f = factor();
  if (f) {
    const labels = f.colors.map((_, i) => i === 0 ? `under ${f.fmt(f.breaks[0])}` : i === f.colors.length - 1
      ? `${f.fmt(f.breaks[i - 1])} +` : `${f.fmt(f.breaks[i - 1])} – ${f.fmt(f.breaks[i])}`);
    $('legend').innerHTML = `
      <div>${f.label}</div>
      <div class="keys" style="display:grid;gap:3px">${f.colors.map((c, i) => `<span><i class="sw" style="background:${c}"></i>${labels[i]}</span>`).join('')}
        <span><i class="sw" style="background:var(--nodata)"></i>no data</span></div>
      <div class="imagery">${f.source}</div>
      ${state.transport ? `<div class="imagery"><span style="color:${TR_COLORS.expressway}">━</span> expressway <span style="color:${TR_COLORS.rail}">━</span> rail</div>` : ''}`;
    return;
  }
  const grad = STOPS.map((c, i) => `${c} ${(i / (STOPS.length - 1) * 100).toFixed(0)}%`).join(',');
  $('legend').innerHTML = `
    <div>${MODE_LABEL[state.mode]} ${useWord()} value, ₱/sqm${state.basis === 'adj' ? `, adjusted to ${meta?.current_year ?? ''}` : ''}</div>
    <div class="bar" style="background:linear-gradient(90deg,${grad});background:linear-gradient(in lab 90deg,${grad})"></div>
    <div class="ticks" style="position:relative;height:14px">${TICKS.map((t, i) => {
      const x = tickPos(t) * 100;
      const align = i === 0 ? 'left:0' : i === TICKS.length - 1 ? 'right:0' : `left:${x}%;transform:translateX(-50%)`;
      return `<span style="position:absolute;${align}">${short(t)}</span>`;
    }).join('')}</div>
    <div class="keys"><span><i class="sw hatch"></i>city median or estimate</span><span><i class="sw" style="background:var(--nodata)"></i>no data</span></div>
    ${Object.keys(streetIndex).length ? `<div class="imagery">${map.getZoom() < STREET_FADE[0] ? 'Zoom in on Metro Manila, southern Cavite and Laguna, or Batangas for village, street and condo values' : `Villages filled, streets as lines (dashed: whole street shown)${state.mode === 'typ' ? '; barangays show their interior-lot value' : ''}`}</div>` : ''}
    ${state.condos ? `<div class="imagery">● condominiums: BIR value per sqm of floor area${map.getZoom() < CONDO_MINZOOM ? ' (zoom in on Metro Manila, Cavite, Laguna or Batangas)' : ''}</div>` : ''}
    ${state.transport ? `<div class="imagery"><span style="color:${TR_COLORS.expressway}">━</span> expressway <span style="color:${TR_COLORS.rail}">━</span> rail · dashed: under construction · dotted: proposed</div>` : ''}
    ${state.satellite ? `<div class="imagery">Imagery: ${SATELLITE.name} (credit at bottom right)</div>` : ''}`;
}

// ---- ranking (barangays with their own data, inside the current map view) ----
function renderRank() {
  if (!zonal) return;
  const b = map.getBounds();
  const fac = factor();
  if (fac) {
    const inView = zonal.features.filter(f => {
      const v = (f.properties as unknown as Record<string, unknown>)[fac.prop];
      const bb = bboxes.get(f.properties.id)!;
      return typeof v === 'number' && b.contains([(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2]);
    });
    const val = (f: Feature) => (f.properties as unknown as Record<string, number>)[fac.prop];
    inView.sort((a, z) => val(z) - val(a));
    const pick = state.order === 'top' ? inView.slice(0, 10) : inView.slice(-10).reverse();
    $('rank-h').textContent = `${state.order === 'top' ? 'Highest' : 'Lowest'} ${fac.label.toLowerCase()} in view`;
    $('rank-note').textContent = `${inView.length.toLocaleString()} barangays with data in the current map view. Source: ${fac.source}.`;
    const ol = $('rank');
    ol.innerHTML = '';
    pick.forEach((f, i) => {
      const li = document.createElement('li');
      li.tabIndex = 0;
      li.innerHTML = `<span class="n">${String(state.order === 'top' ? i + 1 : inView.length - i).padStart(2, '0')}</span><span>${esc(f.properties.n)} <span class="c">${esc(f.properties.c)}</span></span><span class="v">${fac.fmt(val(f))}</span>`;
      li.onclick = () => select(f.properties.id, true);
      ol.appendChild(li);
    });
    return;
  }
  const k = key();
  const rows = zonal.features.filter(f => {
    const p = view(f.properties);
    if (p.q !== 'barangay_data' || f.properties[k] == null) return false;
    const bb = bboxes.get(p.id)!;
    return b.contains([(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2]);
  }).sort((a, z) => (z.properties[k] as number) - (a.properties[k] as number));
  const list = state.order === 'top' ? rows.slice(0, 10) : rows.slice(-10).reverse();
  const label = `${MODE_LABEL[state.mode].toLowerCase()}${state.use === 'res' ? '' : ` ${useWord()}`}`;
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
  document.querySelectorAll<HTMLButtonElement>('[data-use]').forEach(b => b.onclick = () => {
    state.use = b.dataset.use as Use; pressed('[data-use]', b); repaint();
  });
  document.querySelectorAll<HTMLButtonElement>('[data-basis]').forEach(b => b.onclick = () => {
    state.basis = b.dataset.basis as Basis; pressed('[data-basis]', b); repaint();
  });
  document.querySelectorAll<HTMLButtonElement>('[data-order]').forEach(b => b.onclick = () => {
    state.order = b.dataset.order as 'top' | 'bottom'; pressed('[data-order]', b); renderRank();
  });
  $('sat').onclick = () => { state.satellite = !state.satellite; applySatellite(); };
  $('tr').onclick = () => { void setTransport(!state.transport); };
  $('cd').onclick = () => { void setCondos(!state.condos); };
  $<HTMLSelectElement>('by').onchange = e => { state.by = (e.target as HTMLSelectElement).value as By; repaint(); };
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
  if (state.satellite) h.set('sat', '1');
  if (state.use !== 'res') h.set('use', state.use);
  if (!state.transport) h.set('tr', '0');  // on by default: only 'off' is recorded
  if (!state.condos) h.set('cd', '0');
  if (state.by !== 'value') h.set('by', state.by);
  const s = h.toString();
  history.replaceState(null, '', s ? `#${s}` : location.pathname + location.search);
}

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  const v = h.get('v');
  state.mode = v === 'lo' || v === 'hi' ? v : 'typ';
  pressed('[data-mode]', document.querySelector(`[data-mode="${state.mode}"]`)!);
  state.satellite = h.get('sat') === '1';
  const u = h.get('use');
  state.use = u === 'com' || u === 'ind' ? u : 'res';
  const by = h.get('by') as By | null;
  state.by = by && by in FACTORS ? by : 'value';
  $<HTMLSelectElement>('by').value = state.by;
  if ((h.get('tr') !== '0') !== state.transport) void setTransport(h.get('tr') !== '0');
  if ((h.get('cd') !== '0') !== state.condos) void setCondos(h.get('cd') !== '0');
  pressed('[data-use]', document.querySelector(`[data-use="${state.use}"]`)!);
  $('sat').setAttribute('aria-pressed', String(state.satellite));
  if (h.get('adj') === '1' && meta.adjusted_available) { state.basis = 'adj'; pressed('[data-basis]', document.querySelector('[data-basis="adj"]')!); }
  return h.get('b');
}

// ---- boot ----
// ---- footnotes: dates and sources from meta.json, so they follow every build ----
const longDate = (d: string) => new Date(`${d.slice(0, 10)}T00:00:00Z`).toLocaleDateString('en-PH', { year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC' });
const span = (r: string[]) => !r.length ? '' : r[0] === r[r.length - 1] ? longDate(r[0]) : `${longDate(r[0])} to ${longDate(r[r.length - 1])}`;

function renderFootnotes() {
  $('fn-built').innerHTML = `Data built ${esc(longDate(meta.built_at))}` +
    (meta.adjusted_to ? `; "adjusted to today" values are projected to ${esc(longDate(meta.adjusted_to))}.` : '.');
  $('fn-schedules').innerHTML = meta.schedule_range
    ? `BIR schedules in use took effect between ${esc(span(meta.schedule_range))}` +
      (meta.bir ? `: ${meta.bir.department_orders_used} Department Orders from ${meta.bir.rdos.length} Revenue District Offices.` : '.') +
      ' Each barangay shows the newest schedule that covers it; older ones are flagged on its card.'
    : '';
  $('fn-sources').innerHTML = (meta.sources_as_of ?? []).map(s => {
    const when = s.as_of.length && /^\d{4}-\d{2}-\d{2}$/.test(s.as_of[0]) ? span(s.as_of) : s.as_of.join(', ');
    return `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>${when ? `: ${esc(when)}` : ''} <span class="c">(${esc(s.note)})</span></li>`;
  }).join('');
}

async function boot() {
  renderThemeButton();
  wireControls();
  wireSearch();
  // revalidate on every load (a cheap 304 when unchanged) so a deploy never pairs new code with cached old data
  const get = (u: string) => fetch(u, { cache: 'no-cache' }).then(r => r.json());
  const [z, c, m, si] = await Promise.all([get('/data/zonal.geojson'), get('/data/cities.geojson'), get('/data/meta.json'),
    get('/data/streets/index.json').catch(() => ({}))]);
  streetIndex = si;
  condoIndex = await get('/data/condos/index.json').catch(() => ({}));
  for (const k of ['vaco', 'vacc'] as const) {
    if (!(z as Collection<Props>).features.some(f => typeof (f.properties as unknown as Record<string, unknown>)[FACTORS[k].prop] === 'number')) {
      document.querySelector(`#by option[value="${k}"]`)?.remove();
    }
  }
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

  renderFootnotes();

  const initial = readHash();
  renderLegend();
  if (styleReady) addLayers();
  // don't wait for 'idle': it never fires while the tab is in the background
  if (initial) select(initial, true);
  map.once('idle', () => renderRank());
  addEventListener('hashchange', () => {
    const id = readHash();
    applySatellite();
    repaint();
    if (id !== state.selected) select(id, true);
  });
  renderRank();
}

boot().catch(err => {
  console.error(err);
  $('legend').textContent = 'Could not load the map data. Try reloading the page.';
});

