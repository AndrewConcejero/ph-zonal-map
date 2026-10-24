"""Expressways, trunk roads and rail, current and future, for the map's "Roads & rail" overlay.

From OpenStreetMap (cached by osm.fetch_transport and osm.fetch_stations): highway=motorway/trunk, rail/light
rail/subway lines and stations, plus anything tagged under construction or proposed. Status comes from OSM tags, the
expected opening year from opening_date where mappers recorded it. OSM data © OpenStreetMap contributors, ODbL.

Stations: OSM maps most Metro Manila stations as building outlines (ways or multipolygons tagged railway=station or
public_transport=station) and the platforms' stop positions as railway=stop nodes, often with no station node at
all. All of these are read (ways and relations by their centre), bus terminals dropped, and the pieces of one
station (same name, within MERGE_M of each other) merged into one point, which carries the lines (l) of the route
relations passing through it. Only open stations are written: factors.access measures distance to every station
in the file. A station is taken as future when it is tagged construction/proposed, has a future opening_date, lies
nearer a future rail line than an open one, or (with no route relation through it) has a construction-tagged piece
of the same name nearby. One with no open rail line within LINE_M is left out too (track gone or retagged) and
listed in the run summary.

Usage: python -m pipeline.transport   -> public/data/transport.geojson
"""
from __future__ import annotations

import datetime
import difflib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from .osm import fetch_stations, fetch_transport

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'public' / 'data' / 'transport.geojson'
SIMPLIFY = 0.00005  # ~5 m
MERGE_M = 300       # pieces of one station: same name within this distance
LINE_M = 300        # a station further than this from any rail line is reported
RAIL_MODES = ('train', 'subway', 'light_rail', 'monorail')


def classify(tags: dict) -> tuple[str, str] | None:
    """-> (kind, status): kind is expressway | road | rail; status is open | construction | proposed."""
    hw, rw = tags.get('highway'), tags.get('railway')
    if hw in ('motorway', 'trunk'):
        return ('expressway' if hw == 'motorway' else 'road'), 'open'
    if hw in ('construction', 'proposed'):
        sub = tags.get(hw, '')
        return ('expressway' if sub == 'motorway' else 'road'), hw
    if rw in ('rail', 'light_rail', 'subway', 'monorail'):
        return 'rail', 'construction' if tags.get('construction') else 'open'  # MRT-7: railway=subway + construction=subway
    if rw in ('construction', 'proposed'):
        return 'rail', rw
    return None


def opening(value: str) -> tuple[str, str] | None:
    """ISO opening_date (YYYY, YYYY-MM, YYYY-MM-DD, optionally ~) -> (year, sortable date); None if unparseable."""
    m = re.fullmatch(r'~?((?:18|19|20)\d\d)(?:-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?)?', value.strip())
    return (m[1], f'{m[1]}-{m[2] or "01"}-{m[3] or "01"}') if m else None


# ---------------------------------------------------------------- stations

def is_rail(tags: dict) -> bool:
    if tags.get('railway') in ('station', 'halt', 'stop') or 'construction:railway' in tags:
        return tags.get('station') != 'funicular'
    if tags.get('railway') in ('construction', 'proposed'):
        return any(s in (tags.get(tags['railway']) or tags.get('construction') or '') for s in ('station', 'halt')) \
            or tags.get('station') in RAIL_MODES or tags.get('public_transport') == 'station'
    return tags.get('station') in RAIL_MODES or any(tags.get(m) == 'yes' for m in RAIL_MODES)


def station_status(tags: dict, today: str) -> str | None:
    """open | construction | proposed from the station's own tags; None for disused or razed stations."""
    if any(k.startswith(('disused:', 'abandoned:', 'razed:')) and v in ('station', 'halt') for k, v in tags.items()):
        return None
    rw = tags.get('railway')
    if rw in ('construction', 'proposed'):
        return rw
    if 'construction:railway' in tags or tags.get('construction') in ('yes', 'station'):
        return 'construction'
    if 'proposed:railway' in tags:
        return 'proposed'
    o = opening(tags.get('opening_date', ''))
    if o and o[1] > today:
        return 'construction'
    return 'open'


def norm(name: str) -> str:
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r'\b(station|stn|lrt|mrt|pnr|line \d+|lrt-\d|mrt-\d)\b', ' ', s)
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', s).split())


def same_name(a: str, b: str) -> bool:
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    return bool(ta and tb) and (ta <= tb or tb <= ta)


def line_label(tags: dict) -> str:
    """'LRT-1', 'MRT-3', 'MRT-9', 'PNR' ... from a route relation's (or station's) ref, network and name."""
    ref, net, name = tags.get('ref', ''), tags.get('network', ''), tags.get('name', '')
    if re.fullmatch(r'(LRT|MRT)-\d+', ref):
        return ref
    for text in (f'{net} {ref}' if ref.isdigit() else '', net, name, tags.get('line', ''), tags.get('alt_name', '')):
        m = re.search(r'\b(LRT|MRT)\b[- ]?(?:Line )?(\d+)', text)
        if m:
            return f'{m[1]}-{m[2]}'
    if 'PNR' in net or 'PNR' in name or 'Philippine National Railways' in (tags.get('operator', '') + net):
        return 'PNR'
    if 'Subway' in net or 'Subway' in name:
        return 'MRT-9'
    return ref or name.split(':')[0]


def stations(data: dict, rail: list[tuple[object, str]], clip, today: str) -> tuple[list[dict], dict]:
    """-> (open station features, report). rail: (line geometry, status) of the rail ways."""
    els = data['elements']
    routes = [e for e in els if e['type'] == 'relation' and e.get('tags', {}).get('type') == 'route']
    areas = [e for e in els if e['type'] == 'relation' and e.get('tags', {}).get('public_transport') == 'stop_area']
    lines_of: dict[tuple, set] = defaultdict(set)
    for r in routes:
        for m in r.get('members', []):
            lines_of[(m['type'], m['ref'])].add(line_label(r['tags']))
    for a in areas:  # a stop area's station building gets the lines of its stop positions
        ls = set().union(*(lines_of.get((m['type'], m['ref']), set()) for m in a.get('members', [])))
        for m in a.get('members', []):
            lines_of[(m['type'], m['ref'])] |= ls

    to_m = Transformer.from_crs('EPSG:4326', 'EPSG:32651', always_xy=True).transform
    open_tree = STRtree([transform(to_m, g) for g, s in rail if s == 'open'])
    fut_tree = STRtree([transform(to_m, g) for g, s in rail if s != 'open'])

    def nearest(tree, p):
        return p.distance(tree.geometries[tree.nearest(p)]) if len(tree.geometries) else float('inf')

    cands, report = [], {'dropped': Counter(), 'noline': [], 'future': [], 'demoted': [], 'unrouted': []}
    for e in els:
        t = e.get('tags', {})
        if t.get('type') in ('route', 'public_transport') or not t.get('name') or not is_rail(t):
            continue
        if not re.search(r'[A-Za-z]', t['name']):
            report['dropped'][f'name {t["name"]!r}'] += 1
            continue
        c = (e['lon'], e['lat']) if e['type'] == 'node' else (e['center']['lon'], e['center']['lat']) if 'center' in e else None
        if not c or not clip.contains(Point(c)):
            continue
        s = station_status(t, today)
        if s is None:
            report['dropped']['disused'] += 1
            continue
        pm = transform(to_m, Point(c))
        d_open, d_fut = nearest(open_tree, pm), nearest(fut_tree, pm)
        if s == 'open' and d_open > LINE_M:
            s = 'construction' if d_fut <= LINE_M else 'no line'
        elif s == 'open' and d_fut < d_open:
            s = 'construction'
        lines = set(lines_of.get((e['type'], e['id']), set()))
        if not lines and t.get('railway') != 'stop':
            lab = line_label(t)
            if re.fullmatch(r'(LRT|MRT)-\d+|PNR', lab):
                lines.add(lab)
        rank = 0 if t.get('railway') in ('station', 'halt') or t.get('public_transport') == 'station' else 1
        cands.append({'name': t['name'], 'key': norm(t['name']) or t['name'].lower(), 'pm': pm, 'c': c, 's': s,
                      'lines': lines, 'rank': rank, 'type': e['type'], 'd': min(d_open, d_fut)})

    # merge the pieces of one station: same status, same (or contained) name, within MERGE_M of a member
    cands.sort(key=lambda x: (x['rank'], x['type'] != 'node'))
    clusters: list[list[dict]] = []
    for x in cands:
        for cl in clusters:
            if cl[0]['s'] == x['s'] and any(same_name(x['key'], y['key']) and x['pm'].distance(y['pm']) <= MERGE_M for y in cl):
                cl.append(x)
                break
        else:
            clusters.append([x])

    def lines_of_cl(cl):
        return sorted(set().union(*(y['lines'] for y in cl)))

    # 'open' by its tags but with no route through it and a construction-tagged piece of the same name nearby
    # (MRT-7: railway=station nodes beside railway=construction station buildings): future
    fut = [cl for cl in clusters if cl[0]['s'] in ('construction', 'proposed')]
    for cl in clusters:
        if cl[0]['s'] == 'open' and not lines_of_cl(cl):
            twin = next((f for f in fut if any(same_name(x['key'], y['key']) and x['pm'].distance(y['pm']) <= MERGE_M
                                               for x in cl for y in f)), None)
            if twin:
                report['demoted'].append(cl[0]['name'])
                for x in cl:
                    x['s'] = twin[0]['s']

    feats = []
    for cl in clusters:
        head = cl[0]  # a station node first, else a station building's centre, else a stop position
        if head['rank'] == 1 and len(cl) == 1 and not head['lines']:
            report['dropped']['lone stop, no line'] += 1  # a stray stop position with no route: not a station
            continue
        lines = lines_of_cl(cl)
        if head['s'] == 'no line':
            report['noline'].append((head['name'], ';'.join(lines), round(min(y['d'] for y in cl))))
            continue
        if head['s'] != 'open':
            report['future'].append((head['name'], head['s'], ';'.join(lines)))
            continue
        if not lines:
            report['unrouted'].append(head['name'])
        props = {'k': 'station', 's': 'open', 'n': head['name']}
        if lines:
            props['l'] = ';'.join(lines)
        feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [round(head['c'][0], 5), round(head['c'][1], 5)]},
                      'properties': props})
    return feats, report


# ---------------------------------------------------------------- main

def similar_names(names: list[str]) -> list[tuple[str, str]]:
    """Pairs of names that look like the same line written two ways ('X Connector' vs 'X Connector Road')."""
    keys = {n: ' '.join(re.sub(r'[^a-z0-9]+', ' ', n.lower()).split()) for n in names}
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ka, kb = keys[a], keys[b]
            if ka == kb or ka.startswith(kb + ' ') or kb.startswith(ka + ' ') or difflib.SequenceMatcher(None, ka, kb).ratio() > 0.9:
                out.append((a, b))
    return out


def main() -> None:
    cities = json.loads((ROOT / 'public' / 'data' / 'cities.geojson').read_text(encoding='utf-8'))['features']
    area = unary_union([shape(f['geometry']) for f in cities])
    data = fetch_transport(area.bounds)
    clip = area.buffer(0.02)
    today = datetime.date.today().isoformat()
    feats, rail, bad_dates = [], [], []
    for el in data['elements']:
        tags = el.get('tags', {})
        if el['type'] == 'node':
            continue  # stations come from fetch_stations
        c = classify(tags)
        if not c or 'geometry' not in el or len(el['geometry']) < 2:
            continue
        if tags.get('service') in ('yard', 'siding', 'spur') or tags.get('usage') == 'industrial':
            continue
        line = LineString([(p['lon'], p['lat']) for p in el['geometry']]).simplify(SIMPLIFY)
        if not line.intersects(clip):
            continue
        if c[0] == 'rail':
            rail.append((line, c[1]))
        props = {'k': c[0], 's': c[1], 'n': tags.get('name') or tags.get('ref') or ''}
        if tags.get('ref'):
            props['ref'] = tags['ref']
        if tags.get('opening_date'):
            o = opening(tags['opening_date'])
            if o:
                props['open'] = o[0]
            else:
                bad_dates.append((props['n'], tags['opening_date']))
        g = mapping(line)
        g['coordinates'] = [[round(x, 5), round(y, 5)] for x, y in g['coordinates']]
        feats.append({'type': 'Feature', 'geometry': g, 'properties': props})
    st, rep = stations(fetch_stations(area.bounds), rail, clip, today)
    feats += st
    OUT.write_text(json.dumps({'type': 'FeatureCollection', 'features': feats}, separators=(',', ':'), ensure_ascii=False),
                   encoding='utf-8')
    print(f'transport: {len(feats)} features {dict(Counter((f["properties"]["k"], f["properties"]["s"]) for f in feats))}; '
          f'{OUT.stat().st_size / 1e6:.1f} MB')
    print(f'open stations by line: {dict(sorted(Counter(l for f in st for l in (f["properties"].get("l") or "-").split(";")).items()))}')
    fut = sorted(set(rep['future']))
    print(f'future stations left out ({len(fut)}):', fut)
    if rep['dropped']:
        print('stations dropped:', dict(rep['dropped']))
    if rep['demoted']:
        print('tagged open, but a construction-tagged station of the same name is beside it (left out as future):',
              sorted(rep['demoted']))
    if rep['noline']:
        print(f'CHECK stations left out, no open rail line within {LINE_M} m (name, line, m to nearest rail way):',
              sorted(rep['noline']))
    if rep['unrouted']:
        print('CHECK open stations no route relation passes through (no line written):', sorted(rep['unrouted']))
    if bad_dates:
        print(f'CHECK unparseable opening_date (no open year written) on {len(bad_dates)} ways:', dict(Counter(bad_dates)))
    future = sorted({(f['properties']['n'], f['properties'].get('open', '')) for f in feats
                     if f['properties']['s'] != 'open' and f['properties']['n']})
    print('future named:', future[:40])
    dup = similar_names(sorted({n for n, _ in future}))
    if dup:
        print('CHECK future names that look like one line written two ways (not merged):', dup)


if __name__ == '__main__':
    main()
