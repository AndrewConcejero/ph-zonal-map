"""Other factors per barangay, for the map's "Colour by" selector. Each is free, public data:

  flood   UP NOAH / DREAM 100-year flood hazard (LiPAD GeoNode, ODbL): share of the barangay's area in medium or
          high hazard, and in high hazard alone. Layers are per city; cities without one are left blank.
  elev    Copernicus GLO-30 DEM (30 m; © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided
          under COPERNICUS by the European Union and ESA): mean and lowest elevation in metres.
  heat    Landsat 8/9 Collection 2 surface temperature (USGS, public domain; via Microsoft Planetary Computer):
          median across clear dry-season scenes of the barangay's mean daytime land surface temperature, °C.
  access  OpenStreetMap: km from the barangay's centre to the nearest open rail station and expressway.

Usage: python -m pipeline.factors flood|elevation|heat|access|all [--new]  -> cache/factors.json (merged by build.py)
       --new: only barangays without that factor yet (new areas), so existing values stay as they were computed;
       heat in particular depends on which scenes the search returns for the whole area's bounding box.
Everything downloaded is cached in cache/factors/; flood layers are reduced to per-barangay numbers and deleted.
"""
from __future__ import annotations

import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from .build import load_boundaries

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / 'cache' / 'factors'
OUT = ROOT / 'cache' / 'factors.json'
UA = {'User-Agent': 'ph-zonal-map/0.1 (https://github.com/AndrewConcejero/ph-zonal-map)'}
LIPAD = 'https://lipad-fmc.dream.upd.edu.ph/geoserver/wfs'
# NCR, Cavite, Laguna, Rizal, Bulacan, Pampanga (Angeles is ph035401), Batangas, Zambales (Olongapo is ph037107),
# Quezon (Lucena is ph045624) (2019 codes)
PROVINCES = ('ph13', 'ph0421', 'ph0434', 'ph0458', 'ph0314', 'ph0354', 'ph0410', 'ph0371', 'ph0456')


def barangays() -> dict[str, object]:
    bgys, _, _ = load_boundaries()
    return {b['psgc']: shape(b['geometry']).buffer(0) for b in bgys}


def load() -> dict[str, dict]:
    return json.loads(OUT.read_text()) if OUT.exists() else {}


def save(data: dict) -> None:
    OUT.write_text(json.dumps(data, separators=(',', ':')))


def get(url: str, dest: Path, timeout: int = 600) -> Path:
    """Download once into the cache; a dropped connection (LiPAD's large layers) is retried, never cached half-read."""
    for attempt in range(4):
        if dest.exists():
            break
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                body = r.read()
            dest.write_bytes(body)
        except urllib.error.HTTPError as err:
            if err.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(30 * (attempt + 1))
        except (http.client.IncompleteRead, TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt == 3:
                raise
            time.sleep(30 * (attempt + 1))
    return dest


# ---------------------------------------------------------------- flood

def flood_layers() -> list[str]:
    caps = get(f'{LIPAD}?service=WFS&version=1.0.0&request=GetCapabilities', CACHE / 'lipad-caps.xml')
    import re
    names = sorted(set(re.findall(r'geonode:(ph\d+_fh100yr(?:_30m)?_10m)', caps.read_text(errors='ignore'))))
    return [n for n in names if n.startswith(PROVINCES)]


def flood(geoms: dict, data: dict) -> None:
    to_utm = Transformer.from_crs('EPSG:4326', 'EPSG:32651', always_xy=True).transform
    utm = {p: transform(to_utm, g) for p, g in geoms.items()}
    ids = list(utm)
    tree = STRtree([utm[p] for p in ids])
    # psgc -> [area covered, medium+high, high] from the ONE layer covering most of it: neighbouring cities' layers
    # overlap at their edges, so adding them up counts the same ground twice
    acc: dict[str, list[float]] = {}
    for name in flood_layers():
        stats_path = CACHE / f'{name}.stats.json'
        if not stats_path.exists():
            raw = CACHE / f'{name}.json'
            get(f'{LIPAD}?service=WFS&version=1.0.0&request=GetFeature&typeName=geonode:{name}'
                f'&outputFormat=application/json', raw)
            feats = json.loads(raw.read_text())['features']
            by_level = {f['properties']['Var']: shape(f['geometry']).buffer(0) for f in feats if f.get('geometry')}
            covered = unary_union(list(by_level.values()))
            mid_high = unary_union([g for v, g in by_level.items() if v in (2, 3)])
            high = by_level.get(3)
            stats = {}
            for i in tree.query(covered):
                p = ids[i]
                b = utm[p]
                c = b.intersection(covered).area
                if c <= 0:
                    continue
                stats[p] = [c, b.intersection(mid_high).area if not mid_high.is_empty else 0.0,
                            b.intersection(high).area if high is not None else 0.0]
            stats_path.write_text(json.dumps(stats))
            raw.unlink()  # the per-barangay numbers are all we keep
            print(f'  flood {name}: {len(stats)} barangays', flush=True)
            time.sleep(3)  # be polite to the DREAM server
        for p, (c, mh, h) in json.loads(stats_path.read_text()).items():
            if p not in acc or c > acc[p][0]:
                acc[p] = [c, mh, h]
    for p, (c, mh, h) in acc.items():
        if c > 0.5 * utm[p].area:  # only where the layers cover most of the barangay
            area = utm[p].area
            data.setdefault(p, {}).update(flood=round(min(mh / area, 1), 3), flood_high=round(min(h / area, 1), 3))
    print(f'flood: {sum("flood" in v for v in data.values())} barangays')


# ---------------------------------------------------------------- rasters (elevation, heat)

def zonal_mean(geoms: dict, src, band: np.ndarray, valid: np.ndarray, to_src) -> dict[str, tuple[float, float, int]]:
    """Mean, min and pixel count per barangay over one raster array already read from `src`."""
    from rasterio import features
    ids = list(geoms)
    shapes = [(transform(to_src, geoms[p]), i + 1) for i, p in enumerate(ids)]
    lab = features.rasterize(shapes, out_shape=band.shape, transform=src.transform, fill=0, dtype='int32')
    m = (lab > 0) & valid
    lab, vals = lab[m], band[m].astype('float64')
    n = np.bincount(lab, minlength=len(ids) + 1)
    s = np.bincount(lab, weights=vals, minlength=len(ids) + 1)
    mins = np.full(len(ids) + 1, np.inf)
    np.minimum.at(mins, lab, vals)
    return {ids[i - 1]: (s[i] / n[i], mins[i], int(n[i])) for i in range(1, len(ids) + 1) if n[i] > 0}


def elevation(geoms: dict, data: dict) -> None:
    import rasterio
    w, s, e, n = unary_union(list(geoms.values())).bounds
    acc = defaultdict(lambda: [0.0, np.inf, 0])
    for lat in range(int(np.floor(s)), int(np.floor(n)) + 1):
        for lon in range(int(np.floor(w)), int(np.floor(e)) + 1):
            name = f'Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM'
            try:
                path = get(f'https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif', CACHE / f'{name}.tif')
            except urllib.error.HTTPError as err:
                if err.code == 404:  # all-sea tiles are not published
                    continue
                raise
            with rasterio.open(path) as src:
                band = src.read(1)
                for p, (mean, mn, cnt) in zonal_mean(geoms, src, band, band > -100, lambda x, y, z=None: (x, y)).items():
                    a = acc[p]
                    a[0] += mean * cnt
                    a[1] = min(a[1], mn)
                    a[2] += cnt
            print(f'  elevation tile {name}', flush=True)
    for p, (tot, mn, cnt) in acc.items():
        data.setdefault(p, {}).update(elev=round(tot / cnt, 1), elev_min=round(float(mn), 1))
    print(f'elevation: {len(acc)} barangays')


def heat(geoms: dict, data: dict) -> None:
    """Median over clear dry-season (March-May) Landsat 8/9 scenes of each barangay's mean surface temperature."""
    import rasterio
    w, s, e, n = unary_union(list(geoms.values())).bounds
    body = json.dumps({'collections': ['landsat-c2-l2'], 'bbox': [w, s, e, n], 'limit': 100,
                       'query': {'eo:cloud_cover': {'lt': 20}, 'platform': {'in': ['landsat-8', 'landsat-9']}},
                       'datetime': '2023-03-01T00:00:00Z/2025-05-31T23:59:59Z'}).encode()
    req = urllib.request.Request('https://planetarycomputer.microsoft.com/api/stac/v1/search', data=body,
                                 headers={**UA, 'Content-Type': 'application/json'})
    items = [i for i in json.loads(urllib.request.urlopen(req, timeout=120).read())['features']
             if i['properties']['datetime'][5:7] in ('03', '04', '05')]
    items.sort(key=lambda i: i['properties']['eo:cloud_cover'])
    tok = {'value': '', 'at': 0.0}

    def sas_token() -> str:
        """The read token expires; a long run renews it every 15 minutes, backing off if the endpoint says 429."""
        if time.time() - tok['at'] > 900:
            for attempt in range(4):
                try:
                    tok['value'] = json.loads(urllib.request.urlopen(urllib.request.Request(
                        'https://planetarycomputer.microsoft.com/api/sas/v1/token/landsateuwest/landsat-c2', headers=UA),
                        timeout=60).read())['token']
                    break
                except urllib.error.HTTPError as err:
                    if err.code != 429 or attempt == 3:
                        raise
                    time.sleep(60 * (attempt + 1))
            tok['at'] = time.time()
        return tok['value']
    per = defaultdict(list)
    used = 0
    by_tile = defaultdict(int)
    for it in items:
        tile = (it['properties'].get('landsat:wrs_path'), it['properties'].get('landsat:wrs_row'))
        if by_tile[tile] >= 3:  # the three clearest scenes per path/row are plenty
            continue
        st, qa = it['assets']['lwir11']['href'], it['assets']['qa_pixel']['href']
        token = sas_token()
        try:
            got = _heat_scene(geoms, (w, s, e, n), f'/vsicurl/{st}?{token}', f'/vsicurl/{qa}?{token}')
        except rasterio.errors.RasterioIOError as err:  # a dropped read: skip the scene, the next clearest one fills in
            print(f"  heat scene {it['id']} skipped: {err}", flush=True)
            tok['at'] = 0.0  # perhaps an expired token: renew before the next scene
            continue
        by_tile[tile] += 1
        for p, mean in got.items():
            per[p].append(mean)
        used += 1
        print(f"  heat scene {it['id']} ({it['properties']['eo:cloud_cover']:.0f}% cloud)", flush=True)
    for p, vals in per.items():
        data.setdefault(p, {}).update(heat=round(float(np.median(vals)), 1))
    print(f'heat: {len(per)} barangays from {used} scenes')


def _heat_scene(geoms: dict, bbox, st_url: str, qa_url: str) -> dict[str, float]:
    """Mean clear-pixel surface temperature (°C) per barangay in one Landsat scene (barangays with 5+ clear pixels)."""
    import rasterio
    from rasterio.windows import from_bounds
    w, s, e, n = bbox
    with rasterio.open(st_url) as src, rasterio.open(qa_url) as qsrc:
        to_src = Transformer.from_crs('EPSG:4326', src.crs, always_xy=True).transform
        l, b_, r, t = transform(to_src, shape({'type': 'Polygon', 'coordinates': [[(w, s), (e, s), (e, n), (w, n), (w, s)]]})).bounds
        win = from_bounds(l, b_, r, t, src.transform).round_offsets().round_lengths()
        band = src.read(1, window=win, boundless=True, fill_value=0)
        q = qsrc.read(1, window=win, boundless=True, fill_value=0)
        clear = ((q >> 6) & 1).astype(bool) & (band > 0)
        celsius = band * 0.00341802 + 149.0 - 273.15
        sub = type('W', (), {'transform': src.window_transform(win)})()
        return {p: mean for p, (mean, _mn, cnt) in zonal_mean(geoms, sub, celsius, clear, to_src).items() if cnt >= 5}


# ---------------------------------------------------------------- access

def access(geoms: dict, data: dict) -> None:
    tr = json.loads((ROOT / 'public' / 'data' / 'transport.geojson').read_text())['features']
    stations = [shape(f['geometry']) for f in tr if f['properties']['k'] == 'station']
    xways = [shape(f['geometry']) for f in tr if f['properties']['k'] == 'expressway' and f['properties']['s'] == 'open']
    to_m = Transformer.from_crs('EPSG:4326', 'EPSG:32651', always_xy=True).transform
    st = STRtree([transform(to_m, g) for g in stations])
    xw = STRtree([transform(to_m, g) for g in xways])
    for p, g in geoms.items():
        c = transform(to_m, g.representative_point())
        d = data.setdefault(p, {})
        d['rail_km'] = round(c.distance(st.geometries[st.nearest(c)]) / 1000, 2)
        d['xway_km'] = round(c.distance(xw.geometries[xw.nearest(c)]) / 1000, 2)
    print(f'access: {len(geoms)} barangays')


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    what = args[0] if args else 'all'
    only_new = '--new' in sys.argv
    CACHE.mkdir(parents=True, exist_ok=True)
    geoms = barangays()
    data = load()
    steps = {'access': (access, 'rail_km'), 'elevation': (elevation, 'elev'), 'heat': (heat, 'heat'), 'flood': (flood, None)}
    for name, (fn, key) in steps.items():
        if what in (name, 'all'):
            todo = {p: g for p, g in geoms.items() if key not in data.get(p, {})} if only_new and key else geoms
            print(f'{name}: {len(todo)} barangays', flush=True)
            if todo:
                fn(todo, data)
            save(data)


if __name__ == '__main__':
    main()
