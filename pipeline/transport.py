"""Expressways, trunk roads and rail, current and future, for the map's "Roads & rail" overlay.

From OpenStreetMap (cached by osm.fetch_transport): highway=motorway/trunk, rail/light rail/subway lines and
stations, plus anything tagged under construction or proposed. Status comes from OSM tags, the expected opening
year from opening_date where mappers recorded it. OSM data © OpenStreetMap contributors, ODbL.

Usage: python -m pipeline.transport   -> public/data/transport.geojson
"""
from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import LineString, mapping, shape
from shapely.ops import unary_union

from .osm import fetch_transport

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'public' / 'data' / 'transport.geojson'
SIMPLIFY = 0.00005  # ~5 m


def classify(tags: dict) -> tuple[str, str] | None:
    """-> (kind, status): kind is expressway | road | rail; status is open | construction | proposed."""
    hw, rw = tags.get('highway'), tags.get('railway')
    if hw in ('motorway', 'trunk'):
        return ('expressway' if hw == 'motorway' else 'road'), 'open'
    if hw in ('construction', 'proposed'):
        sub = tags.get(hw, '')
        return ('expressway' if sub == 'motorway' else 'road'), hw
    if rw in ('rail', 'light_rail', 'subway', 'monorail'):
        return 'rail', 'open'
    if rw in ('construction', 'proposed'):
        return 'rail', rw
    return None


def main() -> None:
    cities = json.loads((ROOT / 'public' / 'data' / 'cities.geojson').read_text(encoding='utf-8'))['features']
    area = unary_union([shape(f['geometry']) for f in cities])
    data = fetch_transport(area.bounds)
    clip = area.buffer(0.02)
    feats = []
    for el in data['elements']:
        tags = el.get('tags', {})
        if el['type'] == 'node':
            if tags.get('railway') == 'station' and tags.get('name'):
                feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [round(el['lon'], 5), round(el['lat'], 5)]},
                              'properties': {'k': 'station', 's': 'open', 'n': tags['name']}})
            continue
        c = classify(tags)
        if not c or 'geometry' not in el or len(el['geometry']) < 2:
            continue
        if tags.get('service') in ('yard', 'siding', 'spur') or tags.get('usage') == 'industrial':
            continue
        line = LineString([(p['lon'], p['lat']) for p in el['geometry']]).simplify(SIMPLIFY)
        if not line.intersects(clip):
            continue
        props = {'k': c[0], 's': c[1], 'n': tags.get('name') or tags.get('ref') or ''}
        if tags.get('ref'):
            props['ref'] = tags['ref']
        if tags.get('opening_date'):
            props['open'] = tags['opening_date'][:4]
        g = mapping(line)
        g['coordinates'] = [[round(x, 5), round(y, 5)] for x, y in g['coordinates']]
        feats.append({'type': 'Feature', 'geometry': g, 'properties': props})
    OUT.write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}, separators=(',', ':'), ensure_ascii=False),
                   encoding='utf-8')
    from collections import Counter
    print(f'transport: {len(feats)} features {dict(Counter((f["properties"]["k"], f["properties"]["s"]) for f in feats))}; '
          f'{OUT.stat().st_size / 1e6:.1f} MB')
    future = sorted({(f['properties']['n'], f['properties'].get('open', '')) for f in feats
                     if f['properties']['s'] != 'open' and f['properties']['n']})
    print('future named:', future[:40])


if __name__ == '__main__':
    main()
