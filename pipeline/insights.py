"""Where BIR values look low for what a place offers: a screening of barangays, villages and condominiums.

Method: regress log(residential value, adjusted to today) on flood hazard, surface heat, distance to rail, distance
to expressway and distance to the nearest business district. The residual says how much cheaper (or dearer) a place
is than places with the same factors. Shortlists keep only places that also pass plain filters: low flood hazard,
cooler than the median, near rail or an expressway. Condominiums are compared with condos within 3 km.

These are BIR tax floors, not market prices, and a regression on five factors leaves out most of what sets prices
(schools, security, lot sizes, title issues). The output is a list of places worth a closer look, nothing more.

Usage: python -m pipeline.insights   -> docs/insights.md
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'docs' / 'insights.md'
CBDS = {'Makati CBD': (121.0244, 14.5547), 'BGC': (121.0509, 14.5502), 'Ortigas': (121.0614, 14.5869),
        'Alabang': (121.0410, 14.4186), 'Binondo/Manila': (120.9750, 14.6000), 'Eastwood/Cubao': (121.0560, 14.6190)}
to_m = Transformer.from_crs('EPSG:4326', 'EPSG:32651', always_xy=True).transform
CBD_M = {k: Point(*to_m(*v)) for k, v in CBDS.items()}


def cbd_km(lon: float, lat: float) -> tuple[float, str]:
    p = Point(*to_m(lon, lat))
    name = min(CBD_M, key=lambda k: p.distance(CBD_M[k]))
    return p.distance(CBD_M[name]) / 1000, name


def money(v: float) -> str:
    return f'₱{v:,.0f}'


def fit(rows: list[dict]) -> np.ndarray:
    """Least squares on log value; returns residuals (log points: -0.3 is about 26% cheaper than predicted)."""
    X = np.array([[1, r['flood'], r['heat'], math.log(r['rail'] + 0.3), math.log(r['xway'] + 0.3),
                   math.log(r['cbd'] + 0.5)] for r in rows])
    y = np.array([math.log(r['value']) for r in rows])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return y - X @ beta, beta


def table(rows: list[dict], cols: list[tuple[str, str]], n: int = 10) -> list[str]:
    out = ['| ' + ' | '.join(h for h, _ in cols) + ' |', '|' + '---|' * len(cols)]
    for r in rows[:n]:
        out.append('| ' + ' | '.join(str(r[k]) for _, k in cols) + ' |')
    return out


def main() -> None:
    feats = json.loads((ROOT / 'public' / 'data' / 'zonal.geojson').read_text(encoding='utf-8'))['features']
    bgy = {}
    for f in feats:
        p = f['properties']
        c = shape(f['geometry']).representative_point()
        d, near = cbd_km(c.x, c.y)
        bgy[(p['c'], p['n'])] = {**p, 'lon': c.x, 'lat': c.y, 'cbd': d, 'cbd_name': near}

    # --- barangays with their own residential values and all factors
    rows = [{'name': p['n'], 'city': p['c'], 'prov': p['p'], 'value': p['typ_a'], 'flood': p['f_flood'], 'heat': p['f_heat'],
             'rail': p['f_rail'], 'xway': p['f_xway'], 'cbd': p['cbd'], 'cbd_name': p['cbd_name'], 'elev': p.get('f_elev', 0)}
            for p in bgy.values()
            if p['q'] == 'barangay_data' and p.get('typ_a') and all(p.get(k) is not None for k in ('f_flood', 'f_heat', 'f_rail', 'f_xway'))]
    res, beta = fit(rows)
    heat_med = float(np.median([r['heat'] for r in rows]))
    for r, e in zip(rows, res):
        r['resid'] = e
    def show(r):
        return {**r, 'v': money(r['value']), 'cheaper': f"{(1 - math.exp(r['resid'])) * 100:.0f}%",
                'fl': f"{r['flood'] * 100:.0f}%", 'ht': f"{r['heat']:.0f} °C", 'rl': f"{r['rail']:.1f} km",
                'xw': f"{r['xway']:.1f} km", 'cb': f"{r['cbd']:.0f} km to {r['cbd_name']}"}
    good = lambda r: r['flood'] <= 0.10 and r['heat'] <= heat_med and (r['rail'] <= 2 or r['xway'] <= 2)
    pick = sorted((r for r in rows if good(r) and r['resid'] < 0), key=lambda r: r['resid'])
    ncr = [show(r) for r in pick if r['prov'] == 'Metro Manila']
    south = [show(r) for r in pick if r['prov'] in ('Cavite', 'Laguna')]
    north_east = [show(r) for r in pick if r['prov'] in ('Rizal', 'Bulacan')]
    cols = [('Barangay', 'name'), ('City', 'city'), ('Typical, adjusted', 'v'), ('Cheaper than predicted', 'cheaper'),
            ('Flood', 'fl'), ('Heat', 'ht'), ('Rail', 'rl'), ('Expressway', 'xw'), ('Business district', 'cb')]

    # --- villages (residential street-level polygons) using their barangay's factors
    vill = []
    sidx = json.loads((ROOT / 'public' / 'data' / 'streets' / 'index.json').read_text(encoding='utf-8'))
    for city, meta in sidx.items():
        g = json.loads((ROOT / 'public' / 'data' / 'streets' / meta['file']).read_text(encoding='utf-8'))
        for f in g['features']:
            p = f['properties']
            b = bgy.get((p['c'], p['b']))
            if p['k'] != 'village' or p.get('u') != 'r' or not b or not p.get('v_a'):
                continue
            if any(b.get(k) is None for k in ('f_flood', 'f_heat', 'f_rail', 'f_xway')):
                continue
            c = shape(f['geometry']).representative_point()
            d, near = cbd_km(c.x, c.y)
            vill.append({'name': p['osm'], 'bir': p['n'], 'city': p['c'], 'brgy': p['b'], 'value': p['v_a'], 'flood': b['f_flood'],
                         'heat': b['f_heat'], 'rail': b['f_rail'], 'xway': b['f_xway'], 'cbd': d, 'cbd_name': near})
    vres, _ = fit(vill)
    for r, e in zip(vill, vres):
        r['resid'] = e
    vpick = [show(r) for r in sorted((r for r in vill if good(r) and r['resid'] < 0), key=lambda r: r['resid'])]
    vcols = [('Village', 'name'), ('Barangay, city', 'where'), ('BIR value, adjusted', 'v'), ('Cheaper than predicted', 'cheaper'),
             ('Flood (barangay)', 'fl'), ('Heat', 'ht'), ('Rail', 'rl'), ('Expressway', 'xw'), ('Business district', 'cb')]
    for r in vpick:
        r['where'] = f"{r['brgy']}, {r['city']}"

    # --- condominiums: value against condos within 3 km, plus the same filters
    condos = []
    cidx = json.loads((ROOT / 'public' / 'data' / 'condos' / 'index.json').read_text(encoding='utf-8'))
    for city, meta in cidx.items():
        g = json.loads((ROOT / 'public' / 'data' / 'condos' / meta['file']).read_text(encoding='utf-8'))
        for f in g['features']:
            p = f['properties']
            b = bgy.get((p['c'], p['b']))
            if p.get('u') != 'r' or not b or not p.get('v_a'):
                continue
            lon, lat = f['geometry']['coordinates']
            condos.append({'name': p['osm'], 'city': p['c'], 'brgy': p['b'], 'value': p['v_a'], 'pt': Point(*to_m(lon, lat)),
                           'flood': b.get('f_flood'), 'heat': b.get('f_heat'), 'rail': b.get('f_rail'), 'xway': b.get('f_xway'),
                           'cbd': cbd_km(lon, lat)[0], 'cbd_name': cbd_km(lon, lat)[1]})
    for r in condos:
        nb = [o['value'] for o in condos if o is not r and r['pt'].distance(o['pt']) <= 3000]
        r['local'] = float(np.median(nb)) if len(nb) >= 5 else None
    cpick = [r for r in condos if r['local'] and all(r[k] is not None for k in ('flood', 'heat', 'rail', 'xway'))
             and r['flood'] <= 0.10 and r['rail'] <= 1.5 and r['value'] < r['local']]
    cpick.sort(key=lambda r: r['value'] / r['local'])
    for r in cpick:
        r.update(v=money(r['value']), lv=money(r['local']), below=f"{(1 - r['value'] / r['local']) * 100:.0f}%",
                 fl=f"{r['flood'] * 100:.0f}%", rl=f"{r['rail']:.1f} km", where=f"{r['brgy']}, {r['city']}")
    ccols = [('Condominium', 'name'), ('Barangay, city', 'where'), ('BIR value per sqm floor, adjusted', 'v'),
             ('Median of condos within 3 km', 'lv'), ('Below local median', 'below'), ('Flood (barangay)', 'fl'), ('Rail', 'rl')]

    effects = dict(zip(['flood (0→100% of area)', 'heat (+1 °C)', 'rail distance (×2)', 'expressway distance (×2)',
                        'business-district distance (×2)'], beta[1:]))
    lines = [
        '# Places where BIR values look low for what they offer',
        '',
        'A screening, not advice. Values are BIR zonal values (tax floors), adjusted to today with each district\'s own '
        'revision growth. "Cheaper than predicted" compares a place with places that have the same flood hazard, '
        'surface heat, distance to rail and expressway, and distance to the nearest business district '
        f'({", ".join(CBDS)}). Shortlists also require: flood hazard (medium or high, 100-year rain) on 10% of the '
        f'area or less, surface heat at or below the median ({heat_med:.0f} °C), and a rail station or expressway within 2 km. '
        'Everything else that sets prices (schools, security, lot sizes, title and zoning issues) is not in the model, '
        'so verify on the ground and with the BIR Revenue District Office.',
        '',
        f'Model fit on {len(rows):,} barangays. How values move with each factor, holding the others fixed. These are '
        'associations, not prices of risk: central, low-lying, dense areas are both hotter and dearer, so flood and heat '
        'come out positive. That is why they are used as filters below rather than trusted as discounts.',
        '',
        *[f'- {k}: {(math.exp(v * (math.log(2) if "×2" in k else 1)) - 1) * 100:+.0f}%' for k, v in effects.items()],
        '',
        '## Barangays, Metro Manila', '', *table(ncr, cols), '',
        '## Barangays, south (Cavite and Laguna)', '', *table(south, cols), '',
        '## Barangays, Rizal and Bulacan', '', *table(north_east, cols), '',
        '## Villages and subdivisions', '',
        f'{len(vill):,} villages placed on the map, judged with their barangay\'s flood, heat and access figures.', '',
        *table(vpick, vcols, 12), '',
        '## Condominiums', '',
        f'{len(condos):,} residential condominium buildings placed. Shortlist: within 1.5 km of a rail station, low flood '
        'hazard, and below the median BIR value of condominiums within 3 km.', '',
        *table(cpick, ccols, 12), '',
        '_Generated by `pipeline/insights.py`._', '',
    ]
    OUT.write_text('\n'.join(lines), encoding='utf-8')
    print('\n'.join(lines[:20]))
    print(f'shortlists: {len(ncr)} NCR, {len(south)} south, {len(north_east)} Rizal/Bulacan barangays; '
          f'{len(vpick)} villages; {len(cpick)} condos')


if __name__ == '__main__':
    main()
