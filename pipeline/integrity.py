"""Only a barangay's own BIR values reach the map.

Earlier builds filled gaps two ways: a barangay with no schedule row took the median of its neighbours
(flagged estimated) or its city's median (flagged city_median). Both are interpolation, not a value BIR set
for that barangay, so they are removed here and the barangay shows as no_data. The reclassification note
("nores": the newest schedule has no residential row) is kept: it is a fact, not a value.

Called at the end of build.py. Also runs on its own against the published file, so the rule can be applied
without a full rebuild:
    python -m pipeline.integrity            # rewrites public/data/zonal.geojson and meta.json coverage
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'public' / 'data'
FILLED = {'estimated', 'city_median'}
# per-use value fields: residential has no prefix, commercial c_, industrial i_
VALUE_FIELDS = ('lo', 'hi', 'typ', 'typ_m', 'ns', 'eff', 'do', 'rdo', 'src', 'lo_a', 'hi_a', 'typ_a', 'g', 'yrs',
                'since', 'gs', 'nb', 'nbn')


def drop_fills(features: list[dict]) -> Counter:
    """Set every filled value to no_data, for each land use. Returns counts per (use, old flag)."""
    dropped: Counter = Counter()
    for f in features:
        p = f['properties']
        for pre, use in (('', 'residential'), ('c_', 'commercial'), ('i_', 'industrial')):
            q = p.get(f'{pre}q')
            if q not in FILLED:
                continue
            for k in VALUE_FIELDS:
                p.pop(f'{pre}{k}', None)
            p[f'{pre}q'] = 'no_data'
            dropped[(use, q)] += 1
    return dropped


def main() -> None:
    path = OUT / 'zonal.geojson'
    g = json.loads(path.read_text(encoding='utf-8'))
    dropped = drop_fills(g['features'])
    path.write_text(json.dumps(g, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    meta_path = OUT / 'meta.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    cov = meta.get('coverage', {})
    cov['by_flag'] = dict(Counter(f['properties']['q'] for f in g['features']))
    cov['by_source'] = dict(Counter(
        f"{f['properties']['q']}/{f['properties']['src']}" if f['properties'].get('src') else f['properties']['q']
        for f in g['features']))
    by_prov: dict[str, Counter] = {}
    for f in g['features']:
        by_prov.setdefault(f['properties']['p'], Counter())[f['properties']['q']] += 1
    cov['by_province'] = {k: dict(v) for k, v in by_prov.items()}
    meta['coverage'] = cov
    meta['fills_removed'] = {f'{use}/{q}': n for (use, q), n in sorted(dropped.items())}
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('removed:', dict(dropped))


if __name__ == '__main__':
    main()
