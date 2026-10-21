"""Condominium values per building: place BIR's condominium rows on OpenStreetMap's named buildings.

BIR prices condominiums per sqm of unit floor area, as RC (residential condominium) and CC (commercial condominium)
rows naming the building ("BLAKES TOWER"). Each row of a barangay's newest schedule is matched to a named OSM
building inside or touching its barangay, by the same name rules as villages (pipeline/streets.py) with building
words ("tower", "residences", "condominium") ignored. Unmatched buildings are listed, never guessed.

Usage: python -m pipeline.condos   -> public/data/condos/<city>.geojson + index.json, pipeline/review/condos.csv
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .bir_values import resolve_target
from .normalize import ascii_fold
from .osm import fetch_buildings
from .parse_bir import read_rows
from .streets import alternatives, digits

ROOT = Path(__file__).resolve().parent.parent
REVIEW = Path(__file__).resolve().parent / 'review'
OUT = ROOT / 'public' / 'data' / 'condos'
NEAR = 0.0015
BUILDING_WORDS = {'tower', 'towers', 'twr', 'residences', 'residence', 'condominium', 'condominiums', 'condo', 'condos',
                  'building', 'bldg', 'bldgs', 'the', 'at', 'of', 'and', 'suites', 'suite', 'place', 'plaza', 'center',
                  'centre', 'corporate', 'by', 'phase', 'ph', 'i', 'tower1', 'cond', 'res', 'inc', 'corp', 'mansion'}
ROMAN = {'ii': '2', 'iii': '3', 'iv': '4', 'v': '5', 'vi': '6', 'vii': '7', 'viii': '8', 'ix': '9', 'x': '10', 'one': '1',
         'two': '2', 'three': '3', 'four': '4', 'five': '5'}


def bkey(name: str) -> str:
    t = re.sub(r'[^a-z0-9 ]+', ' ', ascii_fold(name).lower().replace("'", ''))
    return ' '.join(ROMAN.get(w, w) for w in t.split() if w not in BUILDING_WORDS)


def bscore(bir: str, osm: str) -> float:
    a, b = bkey(bir), bkey(osm)
    if not a or not b or digits(a) != digits(b):
        return 0
    if a == b or a.replace(' ', '') == b.replace(' ', ''):
        return 100
    ta, tb = a.split(), b.split()
    same = lambda x, y: x == y or (min(len(x), len(y)) >= 6 and x[:6] == y[:6])  # finance ~ financial, not ridge ~ ridgeway
    inside = lambda xs, ys: all(any(same(x, y) for y in ys) for x in xs)
    # BIR name inside the OSM name ("Essensa" -> "Essensa Lawton"), with in-order words, distinctive enough alone
    if inside(ta, tb) and ' '.join(ta) in ' '.join(tb) and (len(ta) >= 2 or len(ta[0]) >= 7):
        return 92
    # OSM name inside the BIR name ("Savya Financial Center" in "Savya Finance Center ..."): needs 2+ words
    if len(tb) >= 2 and inside(tb, ta):
        return 92
    return max(fuzz.ratio(a, b), fuzz.token_sort_ratio(a, b))


def run(city: str) -> dict:
    zonal = json.loads((ROOT / 'public' / 'data' / 'zonal.geojson').read_text(encoding='utf-8'))['features']
    bgys = {f['properties']['id']: f for f in zonal if f['properties']['c'] == city}
    bgeom = {k: shape(f['geometry']) for k, f in bgys.items()}
    psgc_of = {}
    for m in csv.DictReader(open(REVIEW / 'bir_match_report.csv', encoding='utf-8')):
        if m['city'] == city and m['psgc']:
            psgc_of[m['bir_name']] = ({'before': m['valid_before'], 'alias': m['psgc'], 'else': m['otherwise'] or None}
                                      if m['valid_before'] else m['psgc'])
    cand = defaultdict(list)
    for r in read_rows():
        if r['city'] != city:
            continue
        for p in (resolve_target(psgc_of.get(r['barangay']), r['effective_date']) or '').split(';'):
            if p in bgys:
                cand[p].append(r)
    rows = defaultdict(dict)  # psgc -> {(class, building): row}, newest schedule only
    for p, rs in cand.items():
        newest = max((r['effective_date'], r['do_number']) for r in rs)[1]
        for r in rs:
            if r['do_number'] == newest and r['class_code'] in ('RC', 'CC'):
                name = r['street_or_building'].strip('* ')
                if name and not re.match(r'(?i)all other', name):
                    k = (r['class_code'], name)
                    if k not in rows[p] or float(r['value_per_sqm']) > float(rows[p][k]['value_per_sqm']):
                        rows[p][k] = r

    # a condominium is never a church, school, hall or hospital, whatever its name shares
    not_condo = re.compile(r'(?i)\b(church|parish|chapel|cathedral|school|college|university|academy|barangay hall|'
                           r'city hall|municipal|hospital|clinic|mosque|temple|police|fire station|gym(nasium)?|market)\b')
    buildings = [b for b in fetch_buildings(city) if not not_condo.search(b['name'])
                 and b['tags'].get('building') not in ('church', 'chapel', 'school', 'university', 'hospital', 'government',
                                                       'civic', 'public', 'mosque', 'temple')
                 and b['tags'].get('amenity') not in ('place_of_worship', 'school', 'townhall', 'hospital', 'police')]
    pts = [Point(b['lon'], b['lat']) for b in buildings]
    tree = STRtree(pts)
    out, report = [], []
    for p, items in rows.items():
        near = [buildings[i] for i in tree.query(bgeom[p].buffer(NEAR))]
        bp = bgys[p]['properties']
        for (cls, name), r in items.items():
            value = float(r['value_per_sqm'])
            scored = sorted(((max((bscore(a, b['name']) for a in alternatives(name)), default=0), b) for b in near),
                            key=lambda x: -x[0])
            best = scored[0] if scored else (0, None)
            names = {b['name'] for sc, b in scored if sc == best[0]}
            ok = best[0] >= 88 and len(names) == 1
            report.append({'barangay': bp['n'], 'class': cls, 'bir_building': name, 'value': value,
                           'osm_building': best[1]['name'] if best[1] else '', 'score': round(best[0]), 'placed': ok})
            if not ok:
                continue
            pre = 'c_' if cls == 'CC' else ''
            props = {'n': name.title(), 'osm': best[1]['name'], 'v': value, 'u': 'c' if cls == 'CC' else 'r',
                     'b': bp['n'], 'c': city, 'do': r['do_number'], 'eff': bp.get(f'{pre}eff') or bp.get('eff', '')}
            if bp.get(f'{pre}g') is not None and bp.get(f'{pre}yrs') is not None:
                props['g'] = bp[f'{pre}g']
                props['v_a'] = round(value * (1 + bp[f'{pre}g']) ** bp[f'{pre}yrs'])
            out.append({'type': 'Feature', 'properties': props,
                        'geometry': {'type': 'Point', 'coordinates': [round(best[1]['lon'], 5), round(best[1]['lat'], 5)]}})
    # one marker per building and use: the same building can appear in two barangay blocks
    seen, uniq = set(), []
    for f in out:
        k = (f['properties']['osm'], f['properties']['u'], tuple(f['geometry']['coordinates']))
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    OUT.mkdir(parents=True, exist_ok=True)
    slug = city.lower().replace(' ', '-')
    (OUT / f'{slug}.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'features': uniq}, separators=(',', ':'),
                                                     ensure_ascii=False), encoding='utf-8')
    placed = sum(r['placed'] for r in report)
    print(f'{city}: {placed}/{len(report)} condo rows placed, {len(uniq)} buildings')
    w, s_, e, n = unary_union(list(bgeom.values())).bounds
    return {'file': f'{slug}.geojson', 'bbox': [round(w, 4), round(s_, 4), round(e, 4), round(n, 4)],
            'features': len(uniq), 'placed': placed, 'rows': len(report), 'report': report}


def main() -> None:
    zonal = json.loads((ROOT / 'public' / 'data' / 'zonal.geojson').read_text(encoding='utf-8'))['features']
    cities = sorted({f['properties']['c'] for f in zonal if f['properties']['p'] == 'Metro Manila'})
    index, report = {}, []
    for c in cities:
        res = run(c)
        report += [{'city': c, **r} for r in res.pop('report')]
        index[c] = res
    (OUT / 'index.json').write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding='utf-8')
    with open(REVIEW / 'condos.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(report[0]))
        w.writeheader()
        w.writerows(sorted(report, key=lambda r: (r['city'], r['barangay'], not r['placed'], -r['value'])))
    placed = sum(r['placed'] for r in report)
    print(f'Metro Manila: {placed}/{len(report)} condo rows placed ({placed / len(report):.0%}); '
          f'{sum(v["features"] for v in index.values())} buildings; '
          f'{Counter(r["class"] for r in report if r["placed"])}')


if __name__ == '__main__':
    main()
