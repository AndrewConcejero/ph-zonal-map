"""Gap filling: a barangay with no value of its own gets the median of its neighbours that have one.

Neighbours are barangays sharing a boundary. The boundary files are simplified, so shared edges do not line
up exactly; polygons within TOUCH_DEG of each other count as touching. Only barangays with their own BIR
values are used, never other estimates, so an estimate is always one step from real data.
"""
from __future__ import annotations

import statistics

from shapely.geometry import shape
from shapely.strtree import STRtree

TOUCH_DEG = 0.0003  # ~30 m at Manila's latitude
FIELDS = ('lo', 'hi', 'typ', 'lo_a', 'hi_a', 'typ_a')


def neighbours(features: list[dict]) -> dict[str, list[str]]:
    """psgc -> psgc codes of barangays touching it."""
    ids = [f['properties']['id'] for f in features]
    geoms = [shape(f['geometry']).buffer(0) for f in features]
    tree = STRtree(geoms)
    out = {}
    for i, g in enumerate(geoms):
        hits = tree.query(g.buffer(TOUCH_DEG), predicate='intersects')
        out[ids[i]] = [ids[j] for j in hits if j != i]
    return out


def fill(features: list[dict]) -> int:
    """Turn features flagged city_median or no_data into estimates where real neighbours exist. Returns count."""
    by_id = {f['properties']['id']: f['properties'] for f in features}
    nb = neighbours(features)
    filled = 0
    for p in by_id.values():
        if p['q'] not in ('city_median', 'no_data'):
            continue
        real = [by_id[n] for n in nb.get(p['id'], []) if by_id[n]['q'] == 'barangay_data']
        if not real:
            continue
        for k in ('lo', 'hi', 'typ', 'lo_a', 'hi_a', 'typ_a', 'eff', 'src', 'g', 'yrs'):
            p.pop(k, None)
        for k in FIELDS:
            vals = [r[k] for r in real if r.get(k) is not None]
            if vals:
                p[k] = round(statistics.median(vals))
        p['q'] = 'estimated'
        p['nb'] = len(real)
        p['nbn'] = ', '.join(sorted(r['n'] for r in real))[:200]
        filled += 1
    return filled


def fill_prefixed(features: list[dict], prefix: str) -> int:
    """The same estimate for a second set of values stored as <prefix>lo, <prefix>typ, ... with <prefix>q
    (commercial values use 'c_'). Barangays without them get their real neighbours' medians, flagged estimated."""
    by_id = {f['properties']['id']: f['properties'] for f in features}
    nb = neighbours(features)
    filled = 0
    for p in by_id.values():
        if p.get(f'{prefix}q') == 'barangay_data':
            continue
        real = [by_id[n] for n in nb.get(p['id'], []) if by_id[n].get(f'{prefix}q') == 'barangay_data']
        if not real:
            continue
        for k in FIELDS:
            vals = [r[f'{prefix}{k}'] for r in real if r.get(f'{prefix}{k}') is not None]
            if vals:
                p[f'{prefix}{k}'] = round(statistics.median(vals))
        p[f'{prefix}q'] = 'estimated'
        p[f'{prefix}nb'] = len(real)
        filled += 1
    return filled
