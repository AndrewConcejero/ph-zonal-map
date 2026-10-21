"""Build public/data/zonal.geojson, cities.geojson and meta.json.

Value sources, best first, per barangay:
  1. BIR Department Orders parsed from raw/bir/ (cache/bir_rows.csv.gz from `python -m pipeline.parse_bir`):
     the latest DO covering the barangay; typical = median of its residential street values.
  2. The REN.PH-indexed CSVs in data/ (min and max only; typical = geometric mean).
  3. The city median (flag city_median).
Then (staleness.py) every value is also projected to today with its RDO's growth between revisions, and
(gapfill.py) barangays with no value of their own take the median of their neighbours, flagged estimated.
"""
import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .bir_values import CityIndex, barangay_values, match_bir_names, newest_listing, write_matches
from .gapfill import fill as gap_fill
from .staleness import matched_pairs, project, rdo_rates, value_since, years_between
from .fetch_boundaries import MANILA_PSGC, MANILA_SUBMUNS_2019, PROVDISTS, manila_path, municity_path, provdist_path
from .match import load_aliases, match_city
from .normalize import norm_barangay, norm_city
from .parse_bir import ROWS_OUT, read_rows

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
OUT = ROOT / 'public' / 'data'
REVIEW = Path(__file__).resolve().parent / 'review'
CACHE_DIR = ROOT / 'cache'
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
                    no_geom.append({'city': city, 'prov': prov, 'psgc': str(b['properties']['adm4_psgc']),
                                    'name': b['properties']['adm4_en']})
                else:
                    bp = b['properties']
                    bgys.append({'psgc': str(bp['adm4_psgc']), 'name': bp['adm4_en'], 'city': city,
                                 'city_psgc': cpsgc, 'prov': prov, 'geometry': b['geometry']})
    return bgys, cities, no_geom


def load_fallback():
    """-> (city medians keyed by (province, norm_city), barangay rows keyed by (province, norm_city) -> [(name, lo, hi)])."""
    medians = {}
    with open(DATA / 'city_medians.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            medians[(r['province'], norm_city(r['city']))] = {
                'city': r['city'], 'psgc': r['psgc'], 'median': int(r['median_res']), 'eff': r['schedule_effective'] or None}
    rows = defaultdict(list)
    with open(DATA / 'ncr_barangays.csv', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            rows[('Metro Manila', norm_city(r['city']))].append((r['barangay'], int(r['res_min']), int(r['res_max'])))
    for line in open(DATA / 'pasay_barangays.txt', encoding='utf-8'):
        if line.strip():
            n, lo, hi = line.strip().split(',')
            rows[('Metro Manila', 'pasay')].append((f'Barangay {n}', int(lo), int(hi)))
    return medians, rows


def adjust(p: dict, rate, base_date: str, today: date) -> None:
    """Add today's projected values (lo_a, hi_a, typ_a) and the growth rate used."""
    if rate is None or rate.rate is None or not base_date:
        return
    for k in ('lo', 'hi', 'typ'):
        p[f'{k}_a'] = round(project(p[k], base_date, rate.rate, today))
    p['g'] = round(rate.rate, 4)
    p['yrs'] = round(years_between(base_date, today.isoformat()), 1)  # projection horizon; long ones are rough
    if rate.source != 'rdo':
        p['gs'] = rate.source


def resolve_blank_cities(rows: list[dict], index: dict) -> int:
    """Blocks with no city label (multi-city RDOs): take the one city, among those the RDO's other rows name,
    that has a barangay of that name. Returns rows still unresolved."""
    cities_of_rdo = defaultdict(set)
    for r in rows:
        if r['city']:
            cities_of_rdo[r['rdo']].add((r['province'], r['city']))
    cache, left = {}, 0
    for r in rows:
        if r['city']:
            continue
        k = (r['rdo'], r['barangay'])
        if k not in cache:
            hits = [(p, c) for p, c in cities_of_rdo[r['rdo']] if (p, c) in index and index[(p, c)].exact(r['barangay'])]
            cache[k] = hits[0] if len(hits) == 1 else None
        if cache[k]:
            r['province'], r['city'] = cache[k]
        else:
            left += 1
    return left


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        if not rows:
            f.write('')
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def plausibility(feats: list[dict], medians: dict[tuple[str, str], float]) -> list[dict]:
    """Flag values that look wrong. Flags are for a human to check; nothing is changed."""
    out = []
    own = [f['properties'] for f in feats if f['properties']['q'] == 'barangay_data']
    newest_in_city = defaultdict(str)
    for p in own:
        if p.get('src') == 'bir':
            newest_in_city[(p['p'], p['c'])] = max(newest_in_city[(p['p'], p['c'])], p['eff'])
    bgc = next((p['typ'] for p in own if p['n'] == 'Fort Bonifacio' and p['c'] == 'Taguig'), None)
    ncr = sorted(p['typ'] for p in own if p['p'] == 'Metro Manila')
    ncr_median = ncr[len(ncr) // 2] if ncr else None
    for p in own:
        med = medians.get((p['p'], p['c']))
        reasons = []
        if p['hi'] / p['lo'] >= 15:
            reasons.append(f"max is {p['hi'] / p['lo']:.0f}x min; prime value is probably one road or development")
        if med and p['typ'] > 4 * med:
            reasons.append(f"typical is {p['typ'] / med:.1f}x the city median")
        if med and p['typ'] < med / 4:
            reasons.append(f"typical is 1/{med / p['typ']:.0f} of the city median")
        if bgc and p['typ'] > bgc and p['c'] not in ('Makati', 'Taguig'):
            reasons.append(f'typical exceeds Fort Bonifacio (BGC, {bgc:,})')
        newest = newest_in_city.get((p['p'], p['c']), '')
        if p.get('src') == 'bir' and newest and p['eff'] < newest and int(newest[:4]) - int(p['eff'][:4]) >= 3:
            reasons.append(f"value is from {p['do']} ({p['eff'][:4]}) but {p['c']} has a {newest[:4]} schedule; "
                           f"its block in the newer DO did not match (renamed barangay?)")
        if ncr_median and p['p'] != 'Metro Manila' and p['typ'] > 1.5 * ncr_median:
            reasons.append(f'outskirts barangay above 1.5x the Metro Manila median ({ncr_median:,})')
        if reasons:
            out.append({'province': p['p'], 'city': p['c'], 'barangay': p['n'], 'lo': p['lo'], 'hi': p['hi'],
                        'typ': p['typ'], 'city_median': med or '', 'source': p.get('src', ''), 'do': p.get('do', ''),
                        'reasons': '; '.join(reasons)})
    return out


def main() -> None:
    bgys, cities, no_geom = load_boundaries()
    medians_csv, fallback_rows = load_fallback()
    aliases = load_aliases()

    # cities: match the medians CSV by name; its psgc column has known errors, so report mismatches
    psgc_issues = []
    city_by_key = {(c['prov'], norm_city(c['name'])): c for c in cities}
    for k, m in medians_csv.items():
        c = city_by_key.get(k)
        if c is None:
            psgc_issues.append({'city': m['city'], 'csv_psgc': m['psgc'], 'boundary_psgc': '', 'issue': 'no boundary with this name'})
        elif c['psgc'] != m['psgc']:
            psgc_issues.append({'city': m['city'], 'csv_psgc': m['psgc'], 'boundary_psgc': c['psgc'], 'issue': 'psgc differs; matched by name'})

    feats_by_city = defaultdict(dict)  # (province, city) -> {psgc: name}, polygon-less barangays included
    for b in bgys + no_geom:
        feats_by_city[(b['prov'], b['city'])][b['psgc']] = b['name']
    no_geom_ids = {b['psgc'] for b in no_geom}
    has_geom = {b['psgc'] for b in bgys}

    # 1. BIR Department Orders
    bir_rows = read_rows() if ROWS_OUT.exists() else []
    if not bir_rows:
        print('no parsed BIR rows; run `python -m pipeline.parse_bir` (make data does)')
    index = {k: CityIndex(v) for k, v in feats_by_city.items()}
    blank_left = resolve_blank_cities(bir_rows, index)
    names = defaultdict(Counter)
    for r in bir_rows:
        if r['city']:
            names[(r['province'], r['city'])][r['barangay']] += 1
    bir_matches = match_bir_names(names, feats_by_city)
    psgc_of = {}
    drawable = lambda codes: ';'.join(c for c in (codes or '').split(';') if c in has_geom) or None
    for m in bir_matches:
        if m.psgc:
            codes = drawable(m.psgc)
            if not codes and any(c in no_geom_ids for c in m.psgc.split(';')):
                m.method += ' (no geometry)'
            if codes and m.valid_before:  # e.g. Bacoor's pre-merger names, only for schedules before the merger
                psgc_of[(m.province, m.city, m.bir_name)] = {'before': m.valid_before, 'alias': codes,
                                                             'else': drawable(m.otherwise)}
            elif codes:
                psgc_of[(m.province, m.city, m.bir_name)] = codes
    bir_vals = barangay_values(bir_rows, psgc_of)  # psgc -> [DoValue], oldest first

    # reclassified: the newest schedule listing a barangay gives it no residential land row (BIR now classes it
    # commercial/industrial only). An older residential value would pose as current, so it is not used.
    listed = newest_listing(bir_rows, psgc_of)
    reclassified = {}
    for psgc, vs in list(bir_vals.items()):
        newest = listed.get(psgc)
        if newest and newest[0] > vs[-1].effective_date:
            reclassified[psgc] = newest[1]
            del bir_vals[psgc]
    with open(CACHE_DIR / 'barangay_do_values.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['psgc', 'rdo', 'do_number', 'effective_date', 'lo', 'hi', 'typ', 'n_streets'])
        for psgc, vs in sorted(bir_vals.items()):
            for v in vs:
                w.writerow([psgc, v.rdo, v.do_number, v.effective_date, v.lo, v.hi, v.typ, v.n_streets])

    # 2. REN.PH fallback, one-to-one
    by_city_norm = {(p, norm_city(c)): (c, v) for (p, c), v in feats_by_city.items()}
    matches, fallback = [], {}
    for key, src in fallback_rows.items():
        city, feats_here = by_city_norm[key]
        lookup = {name: (lo, hi) for name, lo, hi in src}
        for m in match_city(city, [n for n, _, _ in src], feats_here, aliases):
            if m.psgc in no_geom_ids:
                m.method = 'no_geometry'
            matches.append(m)
            if m.psgc:
                fallback[m.psgc] = lookup[m.source_name]

    # cross-check: where both sources cover a barangay, the REN.PH numbers should equal some BIR revision
    cross = []
    for psgc, (lo, hi) in fallback.items():
        if psgc in bir_vals:
            latest = bir_vals[psgc][-1]
            any_rev = next((v for v in reversed(bir_vals[psgc]) if v.lo == lo and v.hi == hi), None)
            cross.append({'psgc': psgc, 'renph_lo': lo, 'renph_hi': hi, 'bir_lo': latest.lo, 'bir_hi': latest.hi,
                          'bir_do': latest.do_number, 'bir_eff': latest.effective_date,
                          'same_as_latest': latest.lo == lo and latest.hi == hi,
                          'same_as_do': any_rev.do_number if any_rev else ''})

    # growth between revisions, per RDO, from like-for-like street pairs
    today = date.today()
    bmeta = {b['psgc']: b for b in bgys}
    city_of = {p: b['city'] for p, b in bmeta.items()}
    province_of_rdo = {vs[-1].rdo: bmeta[p]['prov'] for p, vs in bir_vals.items() if p in bmeta}
    pairs = matched_pairs(bir_vals, city_of)
    rates = rdo_rates(pairs, province_of_rdo)
    write_csv(REVIEW / 'growth_by_rdo.csv', [
        {'rdo': r.rdo, 'annual_rate': '' if r.rate is None else round(r.rate, 4), 'source': r.source,
         'pairs': r.pairs, 'share_rising': round(r.share_up, 3), 'transitions': r.transitions, 'flags': r.flags}
        for r in sorted(rates.values(), key=lambda r: r.rdo)])
    rdo_of_city = defaultdict(Counter)
    for p, vs in bir_vals.items():
        if p in bmeta:
            rdo_of_city[(bmeta[p]['prov'], bmeta[p]['city'])][vs[-1].rdo] += 1

    # city medians: from BIR barangay values where at least 3 barangays have them, else the CSV
    by_city_typ = defaultdict(list)
    city_eff = defaultdict(list)
    bmeta = {b['psgc']: b for b in bgys}
    for psgc, vs in bir_vals.items():
        if psgc in bmeta:
            by_city_typ[(bmeta[psgc]['prov'], bmeta[psgc]['city'])].append(vs[-1].typ)
            city_eff[(bmeta[psgc]['prov'], bmeta[psgc]['city'])].append(vs[-1].effective_date)
    city_median = {}
    for (prov, city), _ in feats_by_city.items():
        typs = by_city_typ.get((prov, city), [])
        csvm = medians_csv.get((prov, norm_city(city)))
        if len(typs) >= 3:
            city_median[(prov, city)] = (round(statistics.median(typs)), max(city_eff[(prov, city)]), 'bir')
        elif csvm:
            city_median[(prov, city)] = (csvm['median'], csvm['eff'], 'renph')

    feats = []
    for b in bgys:
        key = (b['prov'], b['city'])
        p = {'id': b['psgc'], 'n': b['name'], 'c': b['city'], 'p': b['prov']}
        if b.get('district'):
            p['d'] = b['district']
        if b['psgc'] in bir_vals:
            v = bir_vals[b['psgc']][-1]
            p.update(q='barangay_data', lo=round(v.lo), hi=round(v.hi), typ=round(v.typ), typ_m='median',
                     ns=v.n_streets, eff=v.effective_date, do=v.do_number, rdo=v.rdo, src='bir')
            base = value_since(bir_vals[b['psgc']])
            if base.do_number != v.do_number:
                p['since'] = f'{base.do_number}, {base.effective_date}'  # values carried over unchanged
            adjust(p, rates.get(v.rdo), base.effective_date, today)
        elif b['psgc'] in fallback and b['psgc'] not in reclassified:
            lo, hi = fallback[b['psgc']]
            p.update(q='barangay_data', lo=lo, hi=hi, typ=round(math.sqrt(lo * hi)), typ_m='geomean', src='renph')
            cm = medians_csv.get((b['prov'], norm_city(b['city'])))
            if cm and cm['eff']:
                p['eff'] = cm['eff']
                main_rdo = rdo_of_city[key].most_common(1)
                if main_rdo:
                    p['rdo'] = main_rdo[0][0]
                    adjust(p, rates.get(main_rdo[0][0]), cm['eff'], today)
        elif key in city_median:
            med, eff, src = city_median[key]
            if b['psgc'] in reclassified:
                p['nores'] = reclassified[b['psgc']]
            p.update(q='city_median', typ=med, src=src)
            if eff:
                p['eff'] = eff
        else:
            p['q'] = 'no_data'
        feats.append({'type': 'Feature', 'properties': p,
                      'geometry': {'type': b['geometry']['type'], 'coordinates': rnd(b['geometry']['coordinates'])}})

    # city-median areas: adjusted value is the median of the city's own adjusted barangay values
    adj_by_city = defaultdict(list)
    for f in feats:
        p = f['properties']
        if p['q'] == 'barangay_data' and p.get('typ_a'):
            adj_by_city[(p['p'], p['c'])].append(p['typ_a'])
    for f in feats:
        p = f['properties']
        if p['q'] == 'city_median' and len(adj_by_city[(p['p'], p['c'])]) >= 3:
            p['typ_a'] = round(statistics.median(adj_by_city[(p['p'], p['c'])]))
    n_estimated = gap_fill(feats)

    city_feats = [{'type': 'Feature', 'properties': {'id': c['psgc'], 'n': c['name'], 'p': c['prov']},
                   'geometry': {'type': c['geometry']['type'], 'coordinates': rnd(c['geometry']['coordinates'])}}
                  for c in cities]

    OUT.mkdir(parents=True, exist_ok=True)
    dump = lambda obj, path: path.write_text(json.dumps(obj, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    dump({'type': 'FeatureCollection', 'features': feats}, OUT / 'zonal.geojson')
    dump({'type': 'FeatureCollection', 'features': city_feats}, OUT / 'cities.geojson')

    flags = Counter(f['properties']['q'] for f in feats)
    srcs = Counter((f['properties']['q'], f['properties'].get('src', '')) for f in feats)
    by_prov = defaultdict(Counter)
    for f in feats:
        by_prov[f['properties']['p']][f['properties']['q']] += 1
    effs = sorted(f['properties']['eff'] for f in feats if f['properties'].get('eff'))
    dos_used = sorted({(f['properties']['rdo'], f['properties']['do']) for f in feats if f['properties'].get('do')})
    meta = {
        'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'current_year': date.today().year,
        'value_basis': 'BIR residential zonal value (class RR, land), PHP per sqm',
        'adjusted_available': True,
        'adjusted_to': today.isoformat(),
        'adjusted_note': "Projected from each schedule's effectivity date with its RDO's median annual growth "
                         'between consecutive revisions, measured on the same streets.',
        'growth': {r.rdo: {'rate': None if r.rate is None else round(r.rate, 4), 'source': r.source, 'pairs': r.pairs}
                   for r in sorted(rates.values(), key=lambda r: r.rdo)},
        'schedule_range': [effs[0], effs[-1]] if effs else None,
        'coverage': {'features': len(feats), 'by_flag': dict(flags),
                     'by_source': {f'{q}/{s}' if s else q: n for (q, s), n in srcs.items()},
                     'by_province': {k: dict(v) for k, v in by_prov.items()}},
        'bir': {'department_orders_used': len(dos_used), 'rdos': sorted({r for r, _ in dos_used}),
                'rows_parsed': len(bir_rows)},
        'sources': [
            {'name': 'Bureau of Internal Revenue zonal value schedules (Department Orders), per Revenue District Office',
             'url': 'https://www.bir.gov.ph', 'files': ['raw/bir/']},
            {'name': 'BIR zonal value schedules as indexed by REN.PH (fallback where no raw schedule matched)',
             'url': 'https://ren.ph/tools/zonal-value', 'files': ['data/ncr_barangays.csv', 'data/pasay_barangays.txt', 'data/city_medians.csv']},
            {'name': 'PSA/NAMRIA boundaries (PSGC Dec 2023; Manila from 2019 release) via faeldon/philippines-json-maps', 'url': 'https://github.com/faeldon/philippines-json-maps'},
        ],
    }
    (OUT / 'meta.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    write_csv(REVIEW / 'match_report.csv', [vars(m) for m in matches])
    write_matches(REVIEW / 'bir_match_report.csv', bir_matches)
    unmatched = [{'source': 'renph', 'province': 'Metro Manila', 'city': m.city, 'name': m.source_name, 'rows': 1,
                  'best_candidates': m.candidates} for m in matches if m.method == 'unmatched']
    unmatched += [{'source': 'bir', 'province': m.province, 'city': m.city, 'name': m.bir_name, 'rows': m.rows,
                   'best_candidates': m.candidates} for m in bir_matches if m.method == 'unmatched']
    write_csv(REVIEW / 'unmatched.csv', sorted(unmatched, key=lambda r: (-int(r['rows']), r['city'])))
    write_csv(REVIEW / 'no_geometry.csv', [{**b, 'has_value': b['psgc'] in fallback or any(
        b['psgc'] in (m.psgc or '').split(';') for m in bir_matches)} for b in no_geom])
    write_csv(REVIEW / 'city_psgc_issues.csv', psgc_issues)
    write_csv(REVIEW / 'bir_vs_renph.csv', cross)
    flagged = plausibility(feats, {k: v[0] for k, v in city_median.items()})
    write_csv(REVIEW / 'plausibility.csv', flagged)

    size = (OUT / 'zonal.geojson').stat().st_size / 1e6
    bm = Counter()
    for m in bir_matches:
        bm[m.method.split(' ')[0]] += m.rows
    print(f'features: {len(feats)}  {dict(srcs)}')
    print(f'BIR rows by match method: {dict(bm)}; blank-city rows unresolved: {blank_left}; '
          f'reclassified (no residential row in newest schedule): {len(reclassified)}')
    print(f'REN.PH matches: {Counter(m.method for m in matches)}')
    same = sum(c['same_as_latest'] for c in cross)
    some = sum(bool(c['same_as_do']) for c in cross)
    print(f'REN.PH vs BIR on {len(cross)} barangays: {same} equal latest DO, {some} equal some DO')
    print(f'growth: {len(pairs):,} street pairs; RDO rates {min(r.rate for r in rates.values() if r.rate is not None):.1%}'
          f' to {max(r.rate for r in rates.values() if r.rate is not None):.1%}; estimated barangays: {n_estimated}')
    print(f'zonal.geojson: {size:.1f} MB; city psgc issues: {len(psgc_issues)}; plausibility flags: {len(flagged)}; '
          f'unmatched names: {len(unmatched)}')


if __name__ == '__main__':
    main()
