"""Nationwide layers, one feature per city and municipality: BIR schedule age, LGU SMV status, untitled land.

Schedule age (staleness)
  For each LGU, the newest BIR Department Order (DO) that covers it, per Revenue District Office (RDO), and its
  effectivity date. Where an LGU is split between RDOs (Quezon City, Davao City), the oldest of those current
  schedules is the one coloured, because that is how old part of the city's values are; the card lists all.
  Which DO covers which LGU comes from each workbook's NOTICE sheet (pipeline.inventory.read_file). A DO that
  names no LGUs, or an RDO composed of barangays, is taken to cover the whole RDO. For the RDOs already parsed
  in full (raw/bir/inventory.csv, in_scope=yes) the checked inventory is used instead.

Sources (every one recorded in the outputs)
  data/bir_rdo_index.csv     BIR's zonal values index, https://www.bir.gov.ph/zonal-values: one row per RDO with
                             its current DO and workbook URL. Refresh: node pipeline/fetch_bir_index.cjs
  BIR workbooks              downloaded from those URLs into cache/bir_national/; sha256 in data/bir_workbooks.csv
  PSGC boundaries            faeldon/philippines-json-maps release geojson-lowres.zip (PSGC 2026-3Q), cached
  data/untitled_land.csv     untitled parcels, entered by hand from published sources (see DATA_GAPS.md)
  data/lgu_smv_status.csv    LGU SMV ordinance year and RA 12001 SMV status, entered by hand from BLGF / LGUs

Outputs
  data/bir_national_schedule.csv          audit trail: one row per (RDO, DO, LGU) with how the LGU was matched
  data/bir_workbooks.csv                  which workbook file each date was read from, with its sha256
  public/data/national.geojson            the map layer
  public/data/national_meta.json          as-of dates, sources, coverage counts
  pipeline/review/national_unmatched.csv  LGU names in workbooks that matched no PSGC city or municipality

Usage: python -m pipeline.national
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz, process

from . import inventory as inv
from .normalize import ascii_fold, norm_city

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
CACHE = ROOT / 'cache'
WB_DIR = CACHE / 'bir_national'
OUT = ROOT / 'public' / 'data'
REVIEW = Path(__file__).resolve().parent / 'review'

PSGC_ZIP_URL = 'https://github.com/faeldon/philippines-json-maps/releases/latest/download/geojson-lowres.zip'
PSGC_ZIP_SHA = 'a98f5a29bebac0708526d08d9452bbbe90b4bf72d28cb53e6535664e28e26086'  # PSGC 2026-3Q, release v1.1.1
PSGC_SOURCE = 'PSA / NAMRIA boundaries via OCHA COD-AB, PSGC 3Q 2026, from faeldon/philippines-json-maps (lowres)'
BIR_INDEX_URL = 'https://www.bir.gov.ph/zonal-values'
NCR_PREFIX = 'NCR'

# BIR spellings of LGUs that neither the normaliser nor fuzzy matching resolve (key -> PSGC name, province)
NATIONAL_ALIASES: dict[str, tuple[str, str]] = {
    'polilio': ('Polillo', 'Quezon'),
    'rtromualdez': ('Remedios T. Romualdez', 'Agusan del Norte'),
    'bumbaran': ('Amai Manabilang', 'Lanao del Sur'),  # renamed 2006 (Muslim Mindanao Autonomy Act 205)
}
# fuzzy matches that look close but are different places: (BIR name key, PSGC name)
FUZZY_REJECT = {('sanjose', 'San Jorge')}


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def get(url: str) -> bytes:
    q = urllib.parse.quote(url, safe=':/?=&%')
    with urllib.request.urlopen(q, timeout=180) as r:
        return r.read()


# ---------------------------------------------------------------- PSGC gazetteer

def province_label(name: str) -> str:
    return 'Metro Manila' if name.startswith(NCR_PREFIX) else name


def load_psgc() -> tuple[list[dict], dict[int, str], dict[int, str]]:
    """-> (municipality features, province code -> name, region code -> name)."""
    path = CACHE / 'psgc' / 'geojson-lowres.zip'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        body = get(PSGC_ZIP_URL)
        if sha256(body) != PSGC_ZIP_SHA:
            raise SystemExit(f'{PSGC_ZIP_URL} changed (sha256 mismatch): check the new release, then update PSGC_ZIP_SHA')
        path.write_bytes(body)
    z = zipfile.ZipFile(path)
    provs, regions, munis = {}, {}, []
    for n in z.namelist():
        if not n.endswith('.json'):
            continue
        if '/country/' in n:
            for f in json.loads(z.read(n))['features']:
                regions[f['properties']['adm1_psgc']] = f['properties'].get('adm1_en', '')
        elif '/regions/' in n:
            for f in json.loads(z.read(n))['features']:
                provs[f['properties']['adm2_psgc']] = province_label(f['properties'].get('adm2_en', ''))
        elif '/provdists/' in n:
            munis += json.loads(z.read(n))['features']
    return munis, provs, regions


def lgu_key(name: str) -> str:
    s = re.sub(r'\(.*?\)|\*+', ' ', name)
    s = re.sub(r'^\s*(the\s+)?(city|municipality|mun\.?)\s+of\s+', '', s, flags=re.I)
    s = re.sub(r'\b(capital)\b', '', s, flags=re.I)
    s = re.sub(r'\bpres\b\.?', 'president', s, flags=re.I)
    k = norm_city(s)
    k = re.sub(r'(city|municipality)$', '', k)
    return norm_city(inv.LGU_ALIASES[k]) if k in inv.LGU_ALIASES else k


class Gazetteer:
    def __init__(self, munis: list[dict], provs: dict[int, str]):
        self.entries = []
        for f in munis:
            p = f['properties']
            self.entries.append({'psgc': str(p['adm3_psgc']), 'name': p['adm3_en'], 'prov': provs.get(p['adm2_psgc'], ''),
                                 'prov_code': p['adm2_psgc'], 'level': p.get('geo_level', '')})
        self.by_key: dict[str, list[dict]] = defaultdict(list)
        for e in self.entries:
            self.by_key[lgu_key(e['name'])].append(e)
        self.keys = list(self.by_key)
        self.by_psgc = {e['psgc']: e for e in self.entries}

    def candidates(self, raw: str) -> tuple[list[dict], str]:
        k = lgu_key(raw)
        if not k or len(k) < 3:
            return [], 'empty'
        if k in NATIONAL_ALIASES:
            name, prov = NATIONAL_ALIASES[k]
            return [e for e in self.by_key.get(lgu_key(name), []) if e['prov'] == prov], 'alias'
        if k in self.by_key:
            return self.by_key[k], 'exact'
        hit = process.extractOne(k, self.keys, scorer=fuzz.ratio, score_cutoff=88)
        return (self.by_key[hit[0]], f'fuzzy {hit[1]:.0f}') if hit else ([], 'none')

    def find_in_text(self, s: str, cities_only: bool = True) -> list[dict]:
        """LGUs whose name appears as whole words in s (for RDOs composed of barangays: 'West Davao City')."""
        t = ' ' + re.sub(r'[^a-z0-9]+', ' ', ascii_fold(s).lower()) + ' '
        out = []
        for e in self.entries:
            if cities_only and e['level'] != 'City':
                continue
            n = re.sub(r'^city of ', '', ascii_fold(e['name']).lower())
            n = re.sub(r'[^a-z0-9]+', ' ', n).strip()
            if len(n) >= 4 and f' {n} ' in t:
                out.append(e)
        return out


def resolve(raws: list[str], gaz: Gazetteer, prov_hint: set[str]) -> tuple[list[tuple[str, dict | None, str]], set[str]]:
    """Match LGU names within one RDO. Names shared across provinces ('San Jose') take the RDO's province,
    decided by the names that match only one place. -> ([(raw, entry or None, method)], provinces)."""
    cands = [(r, *gaz.candidates(r)) for r in raws]
    votes = Counter(c[0]['prov'] for _, c, how in cands
                    if how in ('exact', 'alias') and len({e['prov'] for e in c}) == 1)
    provs = {p for p, n in votes.items() if n >= max(2, 0.2 * sum(votes.values()))} or set(votes)
    provs |= {p for p in prov_hint if p}
    in_provs = [e for e in gaz.entries if e['prov'] in provs]
    by_key = defaultdict(list)
    for e in in_provs:
        by_key[lgu_key(e['name'])].append(e)
    says_city = lambda raw: bool(re.search(r'\bcity\b', raw, re.I))
    level = lambda es, raw: [e for e in es if e['level'] == ('City' if says_city(raw) else 'Mun')]
    out = []
    for raw, c, how in cands:
        pick: list[dict] = []
        if how in ('exact', 'alias'):
            if len(c) == 1:
                pick = c  # one place in the country has this name
            else:
                pick = [e for e in c if e['prov'] in provs]
                if len(pick) > 1:
                    pick = level(pick, raw)
                if not pick and says_city(raw):  # 'Isabela City' in Basilan, filed under its own 'province'
                    pick = [e for e in c if e['level'] == 'City']
        elif c:  # fuzzy: only inside the RDO's provinces
            pick = [e for e in c if e['prov'] in provs]
        far = len(pick) == 1 and pick[0]['prov'] not in provs  # a unique name, but outside the RDO's provinces
        if (len(pick) != 1 or far) and by_key:  # BIR typo of a local name: 'Zaragosa', 'Maasim' written 'Maasin'
            hit = process.extractOne(lgu_key(raw), list(by_key), scorer=fuzz.ratio, score_cutoff=80)
            if hit and len(by_key[hit[0]]) == 1 and (lgu_key(raw), by_key[hit[0]][0]['name']) not in FUZZY_REJECT:
                pick, how = by_key[hit[0]], f'fuzzy {hit[1]:.0f} within {"/".join(sorted(provs))}'
        if len(pick) == 1:
            out.append((raw, pick[0], how))
        else:
            out.append((raw, None, f'{how}; {len(pick)} candidates in {sorted(provs)}' if c else how))
    return out, provs


# ---------------------------------------------------------------- BIR

RDO_LABEL_RE = re.compile(r'RDO\s*No\.?\s*(\d{1,3})\s*([A-C])?\b', re.I)


def read_index() -> list[dict]:
    with open(DATA / 'bir_rdo_index.csv', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        m = RDO_LABEL_RE.search(r['rdo_label'])
        r['rdo'] = f"RDO-{inv.rdo_code(m.group(1), m.group(2))}" if m else ''
    return rows


def workbooks(row: dict, log: list[dict]) -> list[Path]:
    """Download (once) the RDO's workbook and return the Excel file(s) in it."""
    url = row['workbook_url'].strip()
    if not url:
        return []
    WB_DIR.mkdir(parents=True, exist_ok=True)
    dest = WB_DIR / Path(urllib.parse.urlparse(url).path).name
    if not dest.exists():
        dest.write_bytes(get(url))
    body = dest.read_bytes()
    log.append({'rdo': row['rdo'], 'workbook_url': url, 'file': dest.name, 'bytes': len(body), 'sha256': sha256(body),
                'downloaded': datetime.fromtimestamp(dest.stat().st_mtime, timezone.utc).date().isoformat()})
    if not zipfile.is_zipfile(dest):
        return [dest]
    out = []
    xdir = WB_DIR / 'x' / dest.stem
    with zipfile.ZipFile(dest) as z:
        for n in z.namelist():
            p = Path(n)
            if p.suffix.lower() in inv.EXCEL_SUFFIXES and not p.name.startswith(('~', '.')) and '__MACOSX' not in n:
                xdir.mkdir(parents=True, exist_ok=True)
                t = xdir / p.name
                if not t.exists():
                    t.write_bytes(z.read(n))
                out.append(t)
    return out


def do_sort_key(eff: str, do: str) -> tuple:
    return (eff or '0000', do)


def national_rows(gaz: Gazetteer) -> tuple[list[dict], list[dict], list[dict], dict]:
    index = read_index()
    checked = defaultdict(list)
    with open(ROOT / 'raw' / 'bir' / 'inventory.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if r['in_scope'] == 'yes':
                checked[r['rdo']].append(r)

    # in-scope city name -> province (names are unique within the 9 provinces already mapped)
    in_scope = {f['properties']['n']: f['properties']['p']
                for f in json.loads((OUT / 'cities.geojson').read_text(encoding='utf-8'))['features']}
    rows, unmatched, files, notes = [], [], [], {}
    for ix in index:
        rdo = ix['rdo']
        prov_hint = {province_label(ix.get('province', '').strip())} - {''}
        base = {'rdo': rdo, 'rdo_label': ix['rdo_label'], 'region': ix['region'], 'workbook_url': ix['workbook_url']}

        if rdo in checked:  # already parsed in full and checked by hand
            names = sorted({c.strip() for r in checked[rdo] for c in r['cities_covered'].split(';') if c.strip()})
            matched, _ = resolve(names, gaz, prov_hint | {in_scope[n] for n in names if n in in_scope})
            m = {raw: e for raw, e, _ in matched}
            for r in checked[rdo]:
                for c in (x.strip() for x in r['cities_covered'].split(';')):
                    e = m.get(c)
                    rows.append({**base, 'department_order': r['department_order'], 'effective_date': r['effective_date'],
                                 'lgu_in_source': c, 'psgc': e['psgc'] if e else '', 'lgu': e['name'] if e else '',
                                 'province': e['prov'] if e else '', 'match': 'checked inventory',
                                 'source_file': f"raw/bir/RDO-{rdo.removeprefix('RDO-')}/{r['file_name']}"})
                    if not e and c:
                        unmatched.append({'rdo': rdo, 'name': c, 'why': 'checked inventory name not in PSGC 2026'})
            continue

        paths = workbooks(ix, files)
        if not paths:
            notes[rdo] = 'BIR publishes no workbook for this RDO'
            continue
        for path in paths:
            try:
                fi = inv.read_file(path, None)
            except Exception as e:  # noqa: BLE001 - a human needs to look; keep going
                notes[rdo] = f'{path.name}: {type(e).__name__}: {e}'
                continue
            if f'RDO-{fi.rdo}' != rdo:
                notes[rdo] = f'{path.name} says RDO {fi.rdo}; filed under {rdo} from BIR index'
            dated = {d.do: min(d.froms).isoformat() for d in fi.dos.values() if d.froms}
            # LGUs of the whole RDO. Some NOTICE sheets list barangays where others list LGUs (Baguio, Davao):
            # when fewer than a third of the names are LGUs, the RDO is read as part of the city in its name
            raw = inv.split_lgus(fi.composition) if fi.composed_of == 'lgus' else []
            for d in fi.dos.values():
                for t in d.lgus_text:
                    raw += inv.split_lgus(t)
            raw = list(dict.fromkeys(raw))
            rdo_lgus, provs = resolve(raw, gaz, prov_hint) if raw else ([], set())
            n_hit = sum(e is not None for _, e, _ in rdo_lgus)
            label_city = gaz.find_in_text(ix['rdo_label'])
            lists_barangays = bool(raw) and (n_hit == 0 or (n_hit < len(raw) / 3 and (len(raw) > 5 or bool(label_city))))
            if lists_barangays:
                notes[rdo] = f'{path.name}: NOTICE names no LGU ({len(raw)} names, {n_hit} LGUs); whole RDO read as the city in its name'
            if fi.composed_of == 'barangays' or not raw or lists_barangays:
                # the RDO's own label first: a NOTICE subject can name a district that shares a city's name
                # (Davao City's Baguio District)
                hits = []
                for text in (ix['rdo_label'], fi.rdo_name, getattr(fi, 'newest_subject', '')):
                    hits = gaz.find_in_text(text)
                    if prov_hint:
                        hits = [e for e in hits if e['prov'] in prov_hint] or hits
                    if hits:
                        break
                rdo_lgus = [(e['name'], e, 'city named in RDO name') for e in hits]
                if not hits:
                    unmatched.append({'rdo': rdo, 'name': fi.rdo_name or ix['rdo_label'], 'why': 'barangay-composed RDO: no city found in its name'})
            else:
                prov_hint = provs
            whole = [(raw, e, how) for raw, e, how in rdo_lgus if e]
            for raw, e, how in rdo_lgus:
                if e is None and not re.search(r'\ball\b|barangay|poblacion|^\d', raw, re.I):
                    unmatched.append({'rdo': rdo, 'name': raw, 'why': how})
            for d in fi.dos.values():
                eff = dated.get(d.do, '')
                listed = [x for t in d.lgus_text for x in inv.split_lgus(t)]
                if listed and not lists_barangays and fi.composed_of != 'barangays' \
                        and not any(re.search(r'\ball\b', t, re.I) for t in d.lgus_text):
                    got, _ = resolve(listed, gaz, prov_hint)
                    covered = [(raw, e, how) for raw, e, how in got if e]
                    for raw, e, how in got:
                        if e is None and not re.search(r'barangay|poblacion|^\d', raw, re.I):
                            unmatched.append({'rdo': rdo, 'name': raw, 'why': f'{d.do}: {how}'})
                else:
                    covered = [(raw, e, 'whole RDO (DO names no LGUs)') for raw, e, _ in whole]
                for raw, e, how in covered:
                    rows.append({**base, 'department_order': d.do, 'effective_date': eff, 'lgu_in_source': raw,
                                 'psgc': e['psgc'], 'lgu': e['name'], 'province': e['prov'], 'match': how,
                                 'source_file': path.name})
            if not dated:
                notes[rdo] = f'{path.name}: no effectivity date found for any DO'
            # BIR's index page also lists the LGUs of the current DO, sometimes more than the NOTICE sheet does
            cur = inv.parse_do(ix.get('current_do', ''))
            listed = re.split(r'\s*\|\s*', ix.get('lgus_listed', ''))
            names = [x for seg in listed if seg and not re.match(r'(department|\*|barangay)', seg, re.I)
                     for x in inv.split_lgus(seg)]
            if cur and cur in dated and names and not lists_barangays and fi.composed_of != 'barangays':
                got, _ = resolve(names, gaz, prov_hint)
                for raw, e, how in got:
                    if e:
                        rows.append({**base, 'department_order': cur, 'effective_date': dated[cur], 'lgu_in_source': raw,
                                     'psgc': e['psgc'], 'lgu': e['name'], 'province': e['prov'],
                                     'match': f'{how}; BIR index page lists this LGU under {cur}',
                                     'source_file': f'{path.name} (date); data/bir_rdo_index.csv (LGU)'})
    return rows, unmatched, files, notes


BRGY_MATCH = 'barangay values (public/data/zonal.geojson)'


def barangay_rows(gaz: Gazetteer) -> list[dict]:
    """One row per (city, RDO): the newest effectivity date among its barangays' own residential values."""
    cities = {(f['properties']['n'], f['properties']['p']): f['properties']['id']
              for f in json.loads((OUT / 'cities.geojson').read_text(encoding='utf-8'))['features']}
    best: dict[tuple[str, str], dict] = {}
    for f in json.loads((OUT / 'zonal.geojson').read_text(encoding='utf-8'))['features']:
        p = f['properties']
        if p.get('q') != 'barangay_data' or not p.get('eff') or not p.get('rdo'):
            continue
        code = cities.get((p['c'], p['p']))
        e = gaz.by_psgc.get(code) if code else None
        if e is None:
            hits = [x for x in gaz.by_key.get(lgu_key(p['c']), []) if x['prov'] == p['p']]
            e = hits[0] if len(hits) == 1 else None
        if e is None:
            continue
        k = (e['psgc'], p['rdo'])
        if k not in best or p['eff'] > best[k]['effective_date']:
            best[k] = {'rdo': p['rdo'], 'rdo_label': '', 'region': '', 'workbook_url': '', 'department_order': p.get('do', ''),
                       'effective_date': p['eff'], 'lgu_in_source': p['c'], 'psgc': e['psgc'], 'lgu': e['name'],
                       'province': e['prov'], 'match': BRGY_MATCH, 'source_file': f"raw/bir/{p['rdo']}/"}
    return list(best.values())


# ---------------------------------------------------------------- valuation records (RA 12001-ready)

# Under RA 12001 (RPVARA, 2024) BLGF-approved LGU Schedules of Market Values replace BIR zonal values as the single
# valuation base. Every value on the map points at one of these records, so LGU_SMV and BLGF_APPROVED_SMV
# schedules can be added next to BIR_ZONAL ones without changing the map files.
VALUATION_SOURCES = ('BIR_ZONAL', 'LGU_SMV', 'BLGF_APPROVED_SMV')


def write_valuation_sources(rows: list[dict], index: list[dict]) -> None:
    """public/data/valuation_sources.json: '<RDO> <DO>' -> {valuation_source, effective_date, source_reference, source_url}."""
    rdo_url = {r['rdo']: r['workbook_url'] for r in index}
    rec: dict[str, dict] = {}

    def add(rdo: str, do: str, eff: str) -> None:
        if not rdo or not do:
            return
        k = f'{rdo} {do}'
        if k in rec and (rec[k]['effective_date'] or not eff):
            return
        rec[k] = {'valuation_source': 'BIR_ZONAL', 'effective_date': eff or '',
                  'source_reference': f"BIR Department Order No. {do.removeprefix('DO ')}, "
                                      f"Revenue District Office {rdo.removeprefix('RDO-').lstrip('0')}",
                  'source_url': rdo_url.get(rdo) or BIR_INDEX_URL}

    for r in rows:
        add(r['rdo'], r['department_order'], r['effective_date'])
    for f in json.loads((OUT / 'zonal.geojson').read_text(encoding='utf-8'))['features']:
        p = f['properties']
        for pre in ('', 'c_', 'i_'):
            add(p.get('rdo', ''), p.get(f'{pre}do', ''), p.get(f'{pre}eff', ''))
    out = {'schema': {'valuation_source': list(VALUATION_SOURCES),
                      'key': '"<RDO> <DO>" as carried by each barangay (rdo + do) and city (s_rdo + s_do)'},
           'records': dict(sorted(rec.items()))}
    (OUT / 'valuation_sources.json').write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


# ---------------------------------------------------------------- hand-entered CSVs

def read_keyed(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding='utf-8') as f:
        return [r for r in csv.DictReader(f) if (r.get('area_code') or '').strip() and not r['area_code'].startswith('#')]


def years_since(d: str, today: date) -> float:
    return round((today - date.fromisoformat(d)).days / 365.25, 1)


def rnd(coords, nd=4):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], nd), round(coords[1], nd)]
    return [rnd(c, nd) for c in coords]


def main() -> None:
    today = date.today()
    munis, provs, regions = load_psgc()
    gaz = Gazetteer(munis, provs)
    rows, unmatched, files, notes = national_rows(gaz)

    # newest dated DO per (LGU, RDO); then the oldest of those across RDOs is the LGU's schedule age
    latest: dict[tuple[str, str], dict] = {}
    for r in rows:
        if not r['psgc'] or not r['effective_date']:
            continue
        k = (r['psgc'], r['rdo'])
        if k not in latest or do_sort_key(r['effective_date'], r['department_order']) > do_sort_key(latest[k]['effective_date'], latest[k]['department_order']):
            latest[k] = r
    per_lgu: dict[str, list[dict]] = defaultdict(list)
    for (psgc, _), r in latest.items():
        per_lgu[psgc].append(r)

    # where barangay values are mapped, they say which DO actually priced each barangay: newest per (city, RDO)
    rows += barangay_rows(gaz)
    for psgc in {r['psgc'] for r in rows if r['match'] == BRGY_MATCH}:
        per_lgu[psgc] = []
    for r in rows:
        if r['match'] == BRGY_MATCH:
            per_lgu[r['psgc']].append(r)

    index = read_index()
    write_valuation_sources(rows, index)

    untitled = read_keyed(DATA / 'untitled_land.csv')
    smv = read_keyed(DATA / 'lgu_smv_status.csv')

    def area_rows(table: list[dict], p: dict) -> dict | None:
        """The finest row that covers this municipality: its own code, then province, then region."""
        codes = [str(p['adm3_psgc']), str(p['adm2_psgc']), str(p['adm1_psgc'])]
        by = {r['area_code'].strip(): r for r in table}
        for lvl, c in zip(('municipality', 'province', 'region'), codes):
            if c in by:
                return {**by[c], '_lvl': lvl}
        return None

    feats, counts = [], Counter()
    for f in munis:
        p = f['properties']
        psgc = str(p['adm3_psgc'])
        props = {'id': psgc, 'n': p['adm3_en'], 'p': provs.get(p['adm2_psgc'], ''), 'r': regions.get(p['adm1_psgc'], '')}
        sched = sorted(per_lgu.get(psgc, []), key=lambda r: r['effective_date'])
        if sched:
            old, new = sched[0], sched[-1]
            props.update(s_eff=old['effective_date'], s_do=old['department_order'], s_rdo=old['rdo'],
                         s_yrs=years_since(old['effective_date'], today), s_n=len(sched))
            if len(sched) > 1:
                props.update(s_eff2=new['effective_date'], s_do2=new['department_order'], s_rdo2=new['rdo'])
            counts['staleness'] += 1
        u = area_rows(untitled, p)
        if u:
            try:
                ut, tot = float(u['untitled_parcels']), float(u['total_parcels'])
                props.update(u_share=round(ut / tot, 4) if tot else None, u_n=int(ut), u_tot=int(tot), u_lvl=u['_lvl'],
                             u_area=u['area_name'], u_asof=u['as_of'], u_src=u['source'], u_url=u['source_url'],
                             u_conf=u['confidence'], u_proxy=u.get('proxy', '').strip().lower() in ('yes', 'true', '1'))
                counts['untitled'] += 1
            except (ValueError, KeyError):
                counts['untitled_bad_rows'] += 1
        sm = area_rows(smv, p)
        if sm and sm['_lvl'] == 'municipality':  # an SMV belongs to one LGU; never spread a province's status
            props.update(v_year=sm.get('last_smv_year', ''), v_ord=sm.get('last_smv_ordinance', ''),
                         v_status=sm.get('rpvara_smv_status', ''), v_asof=sm.get('as_of', ''),
                         v_src=sm.get('source', ''), v_url=sm.get('source_url', ''))
            counts['smv'] += 1
        feats.append({'type': 'Feature', 'properties': props,
                      'geometry': {'type': f['geometry']['type'], 'coordinates': rnd(f['geometry']['coordinates'])}})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'national.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': feats},
                                                     ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

    buckets = Counter()
    for f in feats:
        y = f['properties'].get('s_yrs')
        buckets['unknown' if y is None else '<3' if y < 3 else '3-6' if y < 6 else '6-9' if y < 9 else '9+'] += 1
    rdos_with_dates = {r['rdo'] for r in latest.values()}
    rdo_newest = {}
    for r in latest.values():
        if r['rdo'] not in rdo_newest or r['effective_date'] > rdo_newest[r['rdo']]:
            rdo_newest[r['rdo']] = r['effective_date']
    meta = {
        'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'as_of': today.isoformat(),
        'lgus': len(feats),
        'coverage': dict(counts),
        'staleness_buckets': dict(buckets),
        'rdos_in_index': len(index),
        'rdos_with_dates': len(rdos_with_dates),
        'rdos_not_revised_3y': sum(1 for d in rdo_newest.values() if years_since(d, today) >= 3),
        'rdo_notes': notes,
        'sources': {
            'bir_index': {'name': 'BIR zonal values index (current DO and workbook per RDO)', 'url': BIR_INDEX_URL,
                          'file': 'data/bir_rdo_index.csv', 'as_of': _index_date()},
            'bir_workbooks': {'name': 'BIR zonal value workbooks, NOTICE sheet (DOs, effectivity dates, LGUs covered)',
                              'url': BIR_INDEX_URL, 'file': 'data/bir_workbooks.csv'},
            'boundaries': {'name': PSGC_SOURCE, 'url': 'https://github.com/faeldon/philippines-json-maps'},
            'untitled': {'file': 'data/untitled_land.csv', 'rows': len(untitled)},
            'smv': {'file': 'data/lgu_smv_status.csv', 'rows': len(smv)},
        },
    }
    (OUT / 'national_meta.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    write_csv(DATA / 'bir_national_schedule.csv', sorted(rows, key=lambda r: (r['rdo'], r['effective_date'], r['lgu_in_source'])))
    write_csv(DATA / 'bir_workbooks.csv', files)
    write_csv(REVIEW / 'national_unmatched.csv', unmatched or [{'rdo': '', 'name': '', 'why': ''}])
    size = (OUT / 'national.geojson').stat().st_size / 1e6
    print(f'LGUs: {len(feats)}  with schedule date: {counts["staleness"]}  buckets: {dict(buckets)}')
    print(f'RDOs with dates: {len(rdos_with_dates)} of {meta["rdos_in_index"]}; unmatched names: {len(unmatched)}; '
          f'untitled rows applied to {counts["untitled"]} LGUs; SMV rows: {counts["smv"]}; national.geojson {size:.1f} MB')
    for k, v in notes.items():
        print('  note', k, v)


def _index_date() -> str:
    with open(DATA / 'bir_rdo_index.csv', encoding='utf-8') as f:
        return next(csv.DictReader(f), {}).get('fetched', '')


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ['empty'])
        w.writeheader()
        w.writerows(rows)


if __name__ == '__main__':
    main()
