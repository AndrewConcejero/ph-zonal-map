"""Fetch named streets and villages from OpenStreetMap for one city, cached in cache/osm/.

One Overpass API request per city, made only when the cache is missing (delete the file to refresh).
OSM data is © OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import functools
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import LineString, Polygon, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'cache' / 'osm'
OVERPASS = 'https://overpass-api.de/api/interpreter'
UA = 'ph-zonal-map/0.1 (https://github.com/AndrewConcejero/ph-zonal-map)'

QUERY = """[out:json][timeout:180];
(
  way["highway"]["name"]({bbox});
  way["landuse"="residential"]["name"]({bbox});
  relation["landuse"="residential"]["name"]({bbox});
  way["place"~"^(neighbourhood|quarter|suburb|village|hamlet|isolated_dwelling)$"]["name"]({bbox});
  relation["place"]["name"]({bbox});
  node["place"~"^(neighbourhood|quarter|suburb|village|hamlet)$"]["name"]({bbox});
  way["residential"]["name"]({bbox});
  way["barrier"="wall"]["name"]({bbox});
);
out geom;"""


@functools.cache
def _cities() -> list[dict]:
    return json.loads((ROOT / 'public' / 'data' / 'cities.geojson').read_text(encoding='utf-8'))['features']


def city_polygon(city: str, province: str):
    return shape(next(f for f in _cities() if f['properties']['n'] == city and f['properties']['p'] == province)['geometry'])


def place_slug(city: str, province: str) -> str:
    """File name for a city: 'imus'; with the province when the name is used in several ('rosario-batangas')."""
    base = city.lower().replace(' ', '-')
    if sum(f['properties']['n'] == city for f in _cities()) > 1:
        return f"{base}-{province.lower().replace(' ', '-')}"
    return base


def place_label(city: str, province: str) -> str:
    """Index key for a city: 'Imus'; 'Rosario (Batangas)' when the name is used in several provinces."""
    return city if place_slug(city, province) == city.lower().replace(' ', '-') else f'{city} ({province})'


def fetch(city: str, province: str) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f'{place_slug(city, province)}.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    w, s, e, n = city_polygon(city, province).buffer(0.005).bounds
    q = QUERY.format(bbox=f'{s:.5f},{w:.5f},{n:.5f},{e:.5f}')
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({'data': q}).encode(), headers={'User-Agent': UA})
    t = time.time()
    for attempt in range(4):  # Overpass answers 429/504 when busy: back off and retry
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(30 * (attempt + 1))
    time.sleep(5)  # be polite between cities
    data = json.loads(body)
    path.write_bytes(body)
    print(f'fetched {len(body) / 1e6:.1f} MB for {city} in {time.time() - t:.0f}s')
    return data


def features(city: str, province: str) -> tuple[list[dict], list[dict], list[dict]]:
    """-> (streets, areas, points). Each: name, osm id, tags, shapely geometry (in lon/lat)."""
    data = fetch(city, province)
    streets, areas, points = [], [], []
    for el in data['elements']:
        tags = el.get('tags', {})
        name = tags.get('name')
        if not name:
            continue
        rec = {'name': name, 'id': f"{el['type']}/{el['id']}", 'tags': tags}
        if el['type'] == 'node':
            points.append({**rec, 'geom': shape({'type': 'Point', 'coordinates': [el['lon'], el['lat']]})})
        elif el['type'] == 'way' and 'geometry' in el:
            coords = [(p['lon'], p['lat']) for p in el['geometry']]
            if 'highway' in tags:
                if len(coords) >= 2:
                    streets.append({**rec, 'geom': LineString(coords)})
            elif len(coords) >= 4 and coords[0] == coords[-1]:
                areas.append({**rec, 'geom': Polygon(coords).buffer(0)})
        elif el['type'] == 'relation':
            outers = [m for m in el.get('members', []) if m.get('role') == 'outer' and 'geometry' in m]
            rings = _rings([[(p['lon'], p['lat']) for p in m['geometry']] for m in outers])
            if rings:
                areas.append({**rec, 'geom': unary_union([Polygon(r).buffer(0) for r in rings])})
    return streets, areas, points


def _rings(parts: list[list[tuple]]) -> list[list[tuple]]:
    """Join relation member ways end to end into closed rings."""
    parts = [p for p in parts if len(p) >= 2]
    rings = []
    while parts:
        ring = parts.pop(0)
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for i, p in enumerate(parts):
                if p[0] == ring[-1]:
                    ring += p[1:]
                elif p[-1] == ring[-1]:
                    ring += p[::-1][1:]
                elif p[-1] == ring[0]:
                    ring = p + ring[1:]
                elif p[0] == ring[0]:
                    ring = p[::-1] + ring[1:]
                else:
                    continue
                parts.pop(i)
                changed = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


BUILDINGS_QUERY = """[out:json][timeout:180];
nwr["building"]["name"]({bbox});
out center tags;"""


def fetch_buildings(city: str, province: str) -> list[dict]:
    """Named buildings (condominiums, towers) in a city as points: name, osm id, tags, lon, lat. Cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f'{place_slug(city, province)}-buildings.json'
    if not path.exists():
        w, s, e, n = city_polygon(city, province).buffer(0.005).bounds
        _overpass(BUILDINGS_QUERY.format(bbox=f'{s:.5f},{w:.5f},{n:.5f},{e:.5f}'), path)
    out = []
    for el in json.loads(path.read_text(encoding='utf-8'))['elements']:
        c = el.get('center') or ({'lon': el['lon'], 'lat': el['lat']} if 'lon' in el else None)
        if c and el.get('tags', {}).get('name'):
            out.append({'name': el['tags']['name'], 'id': f"{el['type']}/{el['id']}", 'tags': el['tags'],
                        'lon': c['lon'], 'lat': c['lat']})
    return out


def _overpass(query: str, path: Path) -> None:
    req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({'data': query}).encode(), headers={'User-Agent': UA})
    for attempt in range(4):  # Overpass answers 429/504 when busy: back off and retry
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(30 * (attempt + 1))
    json.loads(body)  # fail loudly rather than cache a broken response
    path.write_bytes(body)
    time.sleep(5)  # be polite between requests


TRANSPORT_QUERY = """[out:json][timeout:300];
(
  way["highway"~"^(motorway|trunk)$"]({bbox});
  way["highway"="construction"]["construction"~"^(motorway|trunk)$"]({bbox});
  way["highway"="proposed"]["proposed"~"^(motorway|trunk)$"]({bbox});
  way["railway"~"^(rail|light_rail|subway|monorail)$"]({bbox});
  way["railway"~"^(construction|proposed)$"]({bbox});
  node["railway"="station"]({bbox});
);
out geom tags;"""


def fetch_transport(bbox: tuple[float, float, float, float]) -> dict:
    """Expressways, trunk roads, rail lines and stations, current and under construction or proposed. Cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / 'transport.json'
    if not path.exists():
        w, s, e, n = bbox
        _overpass(TRANSPORT_QUERY.format(bbox=f'{s:.4f},{w:.4f},{n:.4f},{e:.4f}'), path)
    return json.loads(path.read_text(encoding='utf-8'))
