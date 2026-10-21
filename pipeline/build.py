"""Build public/data/zonal.geojson, cities.geojson and meta.json.

Milestone 2 inputs: the fallback CSVs in data/ (REN.PH-indexed BIR values) plus cached PSGC
boundaries. The BIR Excel parser, staleness adjustment and gap filling plug in later; their
fields are left out of the output until they exist, never filled with placeholders.
"""
import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .fetch_boundaries import MANILA_PSGC, MANILA_SUBMUNS_2019, PROVDISTS, manila_path, municity_path, provdist_path
from .match import load_aliases, match_city
from .normalize import norm_barangay, norm_city

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
OUT = ROOT / 'public' / 'data'
REVIEW = Path(__file__).resolve().parent / 'review'
PRECISION = 5  # ~1 m; plenty for barangay polygons


def rnd(c):
    if isinstance(c[0], (int, float)):
        return [round(c[0], PRECISION), round(c[1], PRECISION)]
    return [rnd(x) for x in c]


def display_city(name: str) -> str:
    name = name.removeprefix('City of ').strip()
    return {'Cavite': 'Cavite City', 'San Jose Del Monte': 'San Jose del Monte', 'Pasay City': 'Pasay'}.get(name, name)


def district_name(s: str) -> str:
    """'TONDO I / II' -> 'Tondo I/II'; 'SAN MIGUEL' -> 'San Miguel'."""
    words = [w if re.fullmatch(r'[IVX]+', w) else w.capitalize() for w in s.replace(' / ', '/').split(' ')]
    return re.sub(r'(?<=/)([a-z]+)', lambda m: m.group(1).upper() if re.fullmatch(r'[ivx]+', m.group(1)) else m.group(1), ' '.join(words))


def load_boundaries():
    """-> (barangays, cities, barangays_without_geometry). Each barangay: psgc, name, city, city_psgc, prov, geometry."""
    bgys, cities, no_geom = [], [], []
    for code, prov in PROVDISTS.items():
        for f in json.loads(provdist_path(code).read_text())['features']:
            if not f.get('geometry'):
                continue
            p = f['properties']
            cpsgc, city = str(p['adm3_psgc']), display_city(p['adm3_en'])
            cities.append({'psgc': cpsgc, 'name': city, 'prov': prov, 'geometry': f['geometry']})
            if cpsgc == MANILA_PSGC:
                for s in MANILA_SUBMUNS_2019:
                    for b in json.loads(manila_path(s).read_text())['features']:
                        if b.get('geometry'):
                            bp = b['properties']
                            # two polygons (Tutuban Mall, North Cemetery) have no barangay code
                            code = bp['ADM4_PCODE'] or f"{bp['ADM3_PCODE']}-{norm_barangay(bp['ADM4_EN'])[:12]}"
                            bgys.append({'psgc': code.removeprefix('PH'), 'name': bp['ADM4_EN'],
                                         'city': city, 'city_psgc': cpsgc, 'prov': prov,
                                         'district': district_name(bp['ADM3_EN']), 'geometry': b['geometry']})
                continue
            for b in json.loads(municity_path(cpsgc).read_text())['features']:
                if not b.get('geometry'):
                    no_geom.append({'city': city, 'psgc': str(b['properties']['adm4_psgc']), 'name': b['properties']['adm4_en']})
                else:
                    bp = b['properties']
                    bgys.append({'psgc': str(bp['adm4_psgc']), 'name': bp['adm4_en'], 'city': city,
                                 'city_psgc': cpsgc, 'prov': prov, 'geometry': b['geometry']})
    return bgys, cities, no_geom


def load_fallback():
    """-> (city_medians keyed by norm_city, barangay rows keyed by norm_city -> [(name, lo, hi)])."""
    medians = {}
    with open(DATA / 'city_medians.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            medians[norm_city(r['city'])] = {'city': r['city'], 'psgc': r['psgc'], 'median': int(r['median_res']),
                                            'eff': r['schedule_effective'] or None}
    rows = defaultdict(list)
    with open(DATA / 'ncr_barangays.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            rows[norm_city(r['city'])].append((r['barangay'], int(r['res_min']), int(r['res_max'])))
    for line in open(DATA / 'pasay_barangays.txt', encoding='utf-8'):
        if line.strip():
            n, lo, hi = line.strip().split(',')
            rows['pasay'].append((f'Barangay {n}', int(lo), int(hi)))
    return medians, rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        if not rows:
            f.write('')
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def plausibility(feats: list[dict], medians: dict) -> list[dict]:
    """Flag values that look wrong. Flags are for a human to check; nothing is changed."""
    out = []
    bgc = max((f['properties']['typ'] for f in feats
               if f['properties'].get('q') == 'barangay_data' and f['properties']['n'] == 'Fort Bonifacio'), default=None)
    for f in feats:
        p = f['properties']
        if p['q'] != 'barangay_data':
            continue
        med = medians.get(norm_city(p['c']), {}).get('median')
        reasons = []
        if p['lo'] == p['hi']:
            reasons.append('min equals max')
        if p['hi'] / p['lo'] >= 15:
            reasons.append(f"max is {p['hi'] / p['lo']:.0f}x min; prime value probably one road or condo")
        if med and p['typ'] > 4 * med:
            reasons.append(f"typical is {p['typ'] / med:.1f}x the city median")
        if med and p['typ'] < med / 4:
            reasons.append(f"typical is 1/{med / p['typ']:.0f} of the city median")
        if bgc and p['typ'] > bgc and p['c'] not in ('Makati', 'Taguig'):
            reasons.append('typical exceeds Fort Bonifacio')
        if reasons:
            out.append({'city': p['c'], 'barangay': p['n'], 'lo': p['lo'], 'hi': p['hi'], 'typ': p['typ'],
                        'city_median': med or '', 'reasons': '; '.join(reasons)})
    return out


def main() -> None:
    bgys, cities, no_geom = load_boundaries()
    medians, rows = load_fallback()
    aliases = load_aliases()

    # cities: match the medians CSV by name; its psgc column has known errors, so report mismatches
    psgc_issues = []
    city_by_key = {norm_city(c['name']): c for c in cities}
    for k, m in medians.items():
        c = city_by_key.get(k)
        if c is None:
            psgc_issues.append({'city': m['city'], 'csv_psgc': m['psgc'], 'boundary_psgc': '', 'issue': 'no boundary with this name'})
        elif c['psgc'] != m['psgc']:
            psgc_issues.append({'city': m['city'], 'csv_psgc': m['psgc'], 'boundary_psgc': c['psgc'], 'issue': 'psgc differs; matched by name'})

    by_city = defaultdict(dict)
    for b in bgys + no_geom:  # match against polygon-less barangays too, so they are reported as such
        by_city[norm_city(b['city'])][b['psgc']] = b['name']
    no_geom_ids = {b['psgc'] for b in no_geom}

    matches = []
    values = {}  # psgc -> (lo, hi)
    for ck, src in rows.items():
        city = next(b['city'] for b in bgys if norm_city(b['city']) == ck)
        lookup = {name: (lo, hi) for name, lo, hi in src}
        for m in match_city(city, [n for n, _, _ in src], by_city[ck], aliases):
            if m.psgc in no_geom_ids:
                m.method = 'no_geometry'
            matches.append(m)
            if m.psgc:
                values[m.psgc] = lookup[m.source_name]

    feats = []
    for b in bgys:
        ck = norm_city(b['city'])
        med = medians.get(ck)
        p = {'id': b['psgc'], 'n': b['name'], 'c': b['city'], 'p': b['prov']}
        if b.get('district'):
            p['d'] = b['district']
        if b['psgc'] in values:
            lo, hi = values[b['psgc']]
            p.update(q='barangay_data', lo=lo, hi=hi, typ=round(math.sqrt(lo * hi)), typ_m='geomean')
        elif med:
            p.update(q='city_median', typ=med['median'])
        else:
            p['q'] = 'no_data'
        if med and med['eff']:
            p['eff'] = med['eff']
        feats.append({'type': 'Feature', 'properties': p,
                      'geometry': {'type': b['geometry']['type'], 'coordinates': rnd(b['geometry']['coordinates'])}})

    city_feats = [{'type': 'Feature', 'properties': {'id': c['psgc'], 'n': c['name'], 'p': c['prov']},
                   'geometry': {'type': c['geometry']['type'], 'coordinates': rnd(c['geometry']['coordinates'])}}
                  for c in cities]

    OUT.mkdir(parents=True, exist_ok=True)
    dump = lambda obj, path: path.write_text(json.dumps(obj, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    dump({'type': 'FeatureCollection', 'features': feats}, OUT / 'zonal.geojson')
    dump({'type': 'FeatureCollection', 'features': city_feats}, OUT / 'cities.geojson')

    flags = Counter(f['properties']['q'] for f in feats)
    by_prov = defaultdict(Counter)
    for f in feats:
        by_prov[f['properties']['p']][f['properties']['q']] += 1
    effs = sorted({m['eff'] for m in medians.values() if m['eff']})
    meta = {
        'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'current_year': date.today().year,
        'value_basis': 'BIR residential zonal value, PHP per sqm of land',
        'adjusted_available': False,
        'adjusted_note': 'Staleness adjustment needs two or more schedule revisions per RDO; none ingested yet.',
        'schedule_range': [effs[0], effs[-1]] if effs else None,
        'coverage': {'features': len(feats), 'by_flag': dict(flags), 'by_province': {k: dict(v) for k, v in by_prov.items()}},
        'sources': [
            {'name': 'BIR zonal value schedules as indexed by REN.PH', 'url': 'https://ren.ph/tools/zonal-value', 'files': ['data/ncr_barangays.csv', 'data/pasay_barangays.txt', 'data/city_medians.csv']},
            {'name': 'Bureau of Internal Revenue zonal values', 'url': 'https://www.bir.gov.ph'},
            {'name': 'PSA/NAMRIA boundaries (PSGC Dec 2023; Manila from 2019 release) via faeldon/philippines-json-maps', 'url': 'https://github.com/faeldon/philippines-json-maps'},
        ],
    }
    (OUT / 'meta.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    write_csv(REVIEW / 'match_report.csv', [vars(m) for m in matches])
    write_csv(REVIEW / 'unmatched.csv', [vars(m) for m in matches if m.method == 'unmatched'])
    write_csv(REVIEW / 'no_geometry.csv', [{**b, 'has_value': b['psgc'] in values} for b in no_geom])
    write_csv(REVIEW / 'city_psgc_issues.csv', psgc_issues)
    flagged = plausibility(feats, medians)
    write_csv(REVIEW / 'plausibility.csv', flagged)

    size = (OUT / 'zonal.geojson').stat().st_size / 1e6
    print(f'features: {len(feats)}  {dict(flags)}')
    print(f'matches: {Counter(m.method for m in matches)}')
    print(f'zonal.geojson: {size:.1f} MB; city psgc issues: {len(psgc_issues)}; plausibility flags: {len(flagged)}')


if __name__ == '__main__':
    main()
