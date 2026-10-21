"""Street- and village-level zonal values: place BIR schedule rows on OpenStreetMap shapes (pilot, one city).

Each residential land (RR) row of a barangay's current schedule is placed as:
  village   - an OSM named residential area ("ALABANG 400 VILLAGE SUBD" -> OSM "Alabang 400")
  segment   - an OSM street clipped between the two cross streets BIR names in its vicinity ("A - B")
  street    - the whole OSM street inside the barangay, when the vicinity cannot be placed
  background- "all other streets": already the barangay's own interior-lot value, so not drawn
  unmatched - nothing in OSM fits; listed for review
Only OSM shapes inside (or touching) the row's own barangay are considered, so common names like
"Rizal St" cannot match in the wrong place.

Usage: python -m pipeline.streets Muntinlupa
Writes public/data/streets/<city>.geojson and pipeline/review/streets_<city>.csv.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz
from shapely.geometry import mapping, shape
from shapely.ops import linemerge, nearest_points, substring, unary_union
from shapely.strtree import STRtree

from .bir_values import resolve_target
from .normalize import ascii_fold
from .osm import features as osm_features
from .parse_bir import read_rows
from .staleness import norm_street

ROOT = Path(__file__).resolve().parent.parent
REVIEW = Path(__file__).resolve().parent / 'review'
OUT = ROOT / 'public' / 'data' / 'streets'
NEAR = 0.0015  # ~150 m: villages and streets often straddle a barangay line
TOUCH = 0.0006  # ~60 m: a cross street this close to a road meets it (divided roads, junction gaps)
GENERIC = {'subd', 'subdivision', 'village', 'villages', 'vill', 'homes', 'home', 'the', 'of', 'and', 'residences',
           'estate', 'estates', 'compound', 'cpd', 'cmpd', 'townhomes', 'townhouse', 'townhouses', 'phase', 'ph',
           'st', 'street', 'ave', 'avenue', 'rd', 'road', 'dr', 'drive', 'blvd', 'boulevard', 'corp', 'inc',
           'realty', 'dev', 'development', 'formerly'}
ROADLIKE_RE = re.compile(r'\b(ROAD|RD|STREET|ST|AVENUE|AVE|BLVD|BOULEVARD|HIGHWAY|HWY|EXPRESSWAY|DRIVE|DR|LANE)\b\.?', re.I)
BACKGROUND_RE = re.compile(r'\bALL\s+OTHER|^INTERIOR|^ALONG\s+ALL', re.I)
ROMAN = {'i': '1', 'ii': '2', 'iii': '3', 'iv': '4', 'v': '5', 'vi': '6', 'vii': '7', 'viii': '8', 'ix': '9', 'x': '10'}


def words(name: str) -> list[str]:
    t = re.sub(r'[^a-z0-9 ]+', ' ', ascii_fold(name).lower().replace("'", ''))
    out = []
    for w in t.split():
        w = ROMAN.get(w, w)
        if len(w) > 4 and w.endswith('s') and not w.endswith('ss'):
            w = w[:-1]  # Soldiers Hills = Soldier Hill
        if w not in GENERIC:
            out.append(w)
    return out


def key(name: str) -> str:
    return ' '.join(words(name))


def alternatives(name: str) -> list[str]:
    """'SOUTH LUZON EXPRESSWAY (SLEX)' -> itself, without brackets, 'SLEX'; 'A / B', 'A AND B' -> each part."""
    base = re.sub(r'\(.*?\)', ' ', name).strip()
    out = [base] + re.findall(r'\((.*?)\)', name)
    out += [x for x in re.split(r'\s*/\s*|\s+AND\s+', base, flags=re.I) if x != base]
    return [x.strip(' .,') for x in dict.fromkeys(out) if x.strip(' .,')]


def digits(s: str) -> list[str]:
    return re.findall(r'\d+', s)


def name_score(bir: str, osm: str) -> float:
    a, b = key(bir), key(osm)
    if not a or not b or digits(a) != digits(b):
        return 0
    if a == b or a.replace(' ', '') == b.replace(' ', ''):
        return 100
    if set(a.split()) <= set(b.split()) and (len(a.split()) >= 2 or len(a) >= 8):
        return 95  # "Camella 4" inside "Camella Alabang 4"; "Don Juan Bayview" inside a joint OSM name
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


def cross_street_pairs(vicinity: str) -> list[tuple[str, str]]:
    """Possible (from, to) readings of a vicinity. Dashes are ambiguous ("ALABANG - ZAPOTE ROAD- DON JESUS BLVD"
    is Alabang-Zapote Road to Don Jesus Blvd), so every split point is offered; the caller keeps the one where
    both halves are real streets."""
    v = re.sub(r'\(.*?\)', ' ', vicinity).strip(' .;,')
    m = re.split(r'\s+TO\s+', v, flags=re.I)
    if len(m) == 2:
        return [(m[0].strip(), m[1].strip())]
    parts = [p.strip() for p in re.split(r'\s*[-–/]\s*', v) if p.strip()]
    return [('-'.join(parts[:k]), '-'.join(parts[k:])) for k in range(1, len(parts))]


def place_segment(line, cross_lines: list) -> object | None:
    """The part of `line` between where two cross streets meet it. Works per carriageway, so divided roads
    (several parallel OSM ways) are clipped on each side."""
    if any(line.distance(c) > TOUCH for c in cross_lines):
        return None
    ends = [nearest_points(line, c)[0] for c in cross_lines]
    merged = linemerge(line) if line.geom_type == 'MultiLineString' else line
    parts = list(merged.geoms) if hasattr(merged, 'geoms') else [merged]
    segs = []
    for part in parts:
        if all(part.distance(e) <= TOUCH for e in ends):
            a, b = sorted(part.project(e) for e in ends)
            if b - a > 1e-5:
                segs.append(substring(part, a, b))
    return unary_union(segs) if segs else None


def run(city: str) -> None:
    zonal = json.loads((ROOT / 'public' / 'data' / 'zonal.geojson').read_text(encoding='utf-8'))['features']
    bgys = {f['properties']['id']: f for f in zonal if f['properties']['c'] == city}
    bgeom = {k: shape(f['geometry']) for k, f in bgys.items()}
    current_do = {k: f['properties'].get('do') for k, f in bgys.items()}

    # which BIR block names map to which barangay (same mapping the build used)
    psgc_of = {}
    for m in csv.DictReader(open(REVIEW / 'bir_match_report.csv', encoding='utf-8')):
        if m['city'] == city and m['psgc']:
            psgc_of[m['bir_name']] = ({'before': m['valid_before'], 'alias': m['psgc'], 'else': m['otherwise'] or None}
                                      if m['valid_before'] else m['psgc'])

    rows = defaultdict(dict)  # psgc -> {(street, vicinity): row}
    for r in read_rows():
        if r['city'] != city or r['class_code'] != 'RR':
            continue
        for p in (resolve_target(psgc_of.get(r['barangay']), r['effective_date']) or '').split(';'):
            if p in bgys and r['do_number'] == current_do[p]:
                k = (r['street_or_building'].strip('* '), r['vicinity'].strip('* '))
                if k not in rows[p] or float(r['value_per_sqm']) > float(rows[p][k]['value_per_sqm']):
                    rows[p][k] = r

    streets, areas, points = osm_features(city)
    s_tree, a_tree = STRtree([s['geom'] for s in streets]), STRtree([a['geom'] for a in areas])

    out, report = [], []
    for psgc, items in rows.items():
        zone = bgeom[psgc].buffer(NEAR)
        near_streets = [streets[i] for i in s_tree.query(zone, predicate='intersects')]
        near_areas = [areas[i] for i in a_tree.query(zone, predicate='intersects')]
        by_street = defaultdict(list)
        for s in near_streets:
            by_street[norm_street(s['name'])].append(s)
        street_names = list(by_street)
        bname = bgys[psgc]['properties']['n']

        core = defaultdict(set)  # street name without its type word -> OSM names ("Don Manolo" -> Don Manolo Drive)
        for sn in street_names:
            core[key(sn)].add(sn)

        def find_street(name: str):
            best = (None, 0)
            for alt in alternatives(name):
                n = norm_street(alt)
                if n in by_street:
                    return n, 100
                c = core.get(key(alt), set())
                if len(c) == 1 and len(key(alt)) >= 5:
                    return next(iter(c)), 97  # BIR "Don Manolo Blvd" = OSM "Don Manolo Drive"
                cand = max(((sn, fuzz.ratio(n, sn)) for sn in street_names if digits(sn) == digits(n)),
                           key=lambda x: x[1], default=None)
                if cand and cand[1] > best[1]:
                    best = cand
            return best if best[1] >= 90 else (None, best[1])

        for (street, vicinity), r in items.items():
            value = float(r['value_per_sqm'])
            rec = {'barangay': bname, 'bir_street': street, 'bir_vicinity': vicinity, 'value': value, 'do': r['do_number'],
                   'kind': '', 'osm_name': '', 'score': 0}
            if BACKGROUND_RE.search(street):
                rec['kind'] = 'background'
                report.append(rec)
                continue
            def as_village():
                cands = sorted(((max(name_score(alt, a['name']) for alt in alternatives(street)), a) for a in near_areas),
                               key=lambda x: -x[0])
                if not cands:
                    return None, ''
                top_names = {a['name'] for sc, a in cands if sc == cands[0][0]}
                hint = f"{cands[0][1]['name']} ({cands[0][0]:.0f})"
                if cands[0][0] < 85 or len(top_names) > 1:
                    return None, hint
                same = [a for sc, a in cands if sc == cands[0][0]]
                geom = unary_union([a['geom'] for a in same]).intersection(zone)
                rec.update(kind='village', osm_name=cands[0][1]['name'], score=cands[0][0])
                return {'type': 'Feature', 'geometry': mapping(geom), 'properties': {
                    'n': street.title(), 'osm': cands[0][1]['name'], 'v': value, 'k': 'village', 'b': bname,
                    'do': r['do_number']}}, hint

            def as_street():
                sn, score = find_street(street)
                if not sn:
                    return None
                line = unary_union([s['geom'] for s in by_street[sn]]).intersection(zone)
                seg, kind = None, 'street'
                for a, b in cross_street_pairs(vicinity):
                    ca, cb = find_street(a)[0], find_street(b)[0]
                    if ca and cb and ca != sn and cb != sn:
                        seg = place_segment(line, [unary_union([s['geom'] for s in by_street[c]]) for c in (ca, cb)])
                        if seg is not None and not seg.is_empty:
                            break
                if seg is not None and not seg.is_empty:
                    line, kind = seg, 'segment'
                if line.is_empty:
                    return None
                rec.update(kind=kind, osm_name=by_street[sn][0]['name'], score=score)
                return {'type': 'Feature', 'geometry': mapping(line), 'properties': {
                    'n': street.title(), 'osm': by_street[sn][0]['name'], 'vic': vicinity.title(), 'v': value,
                    'k': kind, 'b': bname, 'do': r['do_number']}}

            hint = ''
            if ROADLIKE_RE.search(street):
                feat = as_street()
                if not feat:
                    feat, hint = as_village()
            else:
                feat, hint = as_village()
                if not feat:
                    feat = as_street()
            if feat:
                out.append(feat)
                report.append(rec)
                continue
            rec.update(kind='unmatched', osm_name=hint)
            report.append(rec)
            continue

    # several rows placed on the same whole street (different vicinities BIR could not pin down): one line, value range
    merged, seen = [], {}
    for f in out:
        p = f['properties']
        if p['k'] != 'street':
            merged.append(f)
            continue
        k = (p['b'], p['osm'])
        if k in seen:
            q = seen[k]['properties']
            q['vmin'] = min(q.get('vmin', q['v']), p['v'])
            q['v'] = max(q['v'], p['v'])
            q['rows'] = q.get('rows', 1) + 1
        else:
            seen[k] = f
            merged.append(f)
    out = merged

    # adjusted values: the barangay's own growth rate and projection length (see staleness.py)
    by_name = {f['properties']['n']: f['properties'] for f in bgys.values()}
    for f in out:
        p, bp = f['properties'], by_name[f['properties']['b']]
        if bp.get('g') is not None and bp.get('yrs') is not None:
            p['g'] = bp['g']
            for k in ('v', 'vmin'):
                if k in p:
                    p[f'{k}_a'] = round(p[k] * (1 + bp['g']) ** bp['yrs'])
        p['eff'] = bp.get('eff', '')

    OUT.mkdir(parents=True, exist_ok=True)
    slug = city.lower().replace(' ', '-')
    # round coordinates: ~1 m is plenty
    def rnd(c):
        return [round(c[0], 5), round(c[1], 5)] if isinstance(c[0], (int, float)) else [rnd(x) for x in c]
    for f in out:
        f['geometry'] = {'type': f['geometry']['type'], 'coordinates': rnd(f['geometry']['coordinates'])}
    (OUT / f'{slug}.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': out}, separators=(',', ':')),
                                          encoding='utf-8')
    with open(REVIEW / f'streets_{slug}.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(report[0]))
        w.writeheader()
        w.writerows(sorted(report, key=lambda x: (x['barangay'], x['kind'], -x['value'])))
    kinds = Counter(x['kind'] for x in report)
    placed = sum(n for k, n in kinds.items() if k in ('village', 'segment', 'street'))
    priced = sum(n for k, n in kinds.items() if k != 'background')
    print(f'{city}: {len(report)} RR rows in current schedules; {dict(kinds)}')
    print(f'placed {placed}/{priced} priced rows ({placed / priced:.0%}); '
          f'{(OUT / f"{slug}.geojson").stat().st_size / 1e3:.0f} KB')


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv) > 1 else 'Muntinlupa')
