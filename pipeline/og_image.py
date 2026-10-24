"""Render public/og.png (1200x630), the social preview, from the built GeoJSON.

Run after build.py so the preview always matches the data on the site.
"""
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, LogNorm  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402
from matplotlib.collections import PatchCollection  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'public' / 'data' / 'zonal.geojson'
OUT = ROOT / 'public' / 'og.png'
STOPS = ['#e9f2e1', '#b5dcb4', '#63b39d', '#2d8590', '#265a8a', '#2c2f6e']  # same as src/main.ts
BG, FG, MUTED, NODATA, HATCH = '#f4f6f4', '#17201f', '#5c6a68', '#dfe3e1', '#7c8a87'
VIEW = (120.84, 121.22, 14.36, 14.80)  # lon0, lon1, lat0, lat1: Metro Manila, sized to fill the map panel


def rings(geom):
    polys = [geom['coordinates']] if geom['type'] == 'Polygon' else geom['coordinates']
    for poly in polys:
        yield poly[0]


def main() -> None:
    feats = json.loads(SRC.read_text())['features']
    cmap = LinearSegmentedColormap.from_list('zonal', STOPS)
    norm = LogNorm(2500, 2000000, clip=True)  # same domain as src/main.ts

    fig = plt.figure(figsize=(12, 6.3), dpi=100, facecolor=BG)
    # map on the right; keep lon/lat aspect roughly true at 14.5 N
    ax = fig.add_axes((0.56, 0.0, 0.44, 1.0))
    ax.set_facecolor('#e4e9e7')
    lon0, lon1, lat0, lat1 = VIEW
    ax.set_xlim(lon0, lon1)
    ax.set_ylim(lat0, lat1)
    ax.set_aspect(1 / math.cos(math.radians(14.58)), adjustable='datalim')
    ax.axis('off')

    solid, hatched = [], []
    for f in feats:
        p = f['properties']
        v = p.get('typ')
        color = NODATA if v is None else cmap(norm(v))
        alpha = 1.0 if p['q'] == 'barangay_data' else 0.6
        for ring in rings(f['geometry']):
            patch = Polygon(ring, closed=True)
            solid.append((patch, color, alpha))
            if p['q'] in ('city_median', 'estimated'):
                hatched.append(Polygon(ring, closed=True))
    for patch, color, alpha in solid:
        patch.set_facecolor(color)
        patch.set_alpha(alpha)
        patch.set_edgecolor('white')
        patch.set_linewidth(0.25)
        ax.add_patch(patch)
    ax.add_collection(PatchCollection(hatched, facecolor='none', edgecolor=HATCH, hatch='////', linewidth=0, alpha=0.5))

    t = fig.text
    t(0.05, 0.86, 'BIR ZONAL VALUES · ₱ PER SQM OF LAND', fontsize=13, color=MUTED, family='monospace')
    t(0.05, 0.80, 'What the taxman\nthinks Metro Manila\nland is worth', fontsize=36, weight='bold', color=FG, va='top', linespacing=1.05)
    t(0.05, 0.36, 'Barangay by barangay from Zambales and Pampanga\nthrough Metro Manila to Batangas and Quezon.\nTap any area for its values.', fontsize=16, color=MUTED, va='top', linespacing=1.4)
    t(0.05, 0.08, 'ph-zonal-map.vercel.app', fontsize=15, color='#0f6b6b', weight='bold')

    # legend bar
    lax = fig.add_axes((0.05, 0.16, 0.36, 0.025))
    lax.imshow([list(range(256))], aspect='auto', cmap=cmap, extent=(0, 1, 0, 1))
    lax.set_xticks([])
    lax.set_yticks([])
    for s in lax.spines.values():
        s.set_visible(False)
    for v, label in [(2500, '₱2.5K'), (2000000, '₱2M')]:
        x = 0.05 + 0.36 * (math.log(v / 2500) / math.log(2000000 / 2500))
        t(x, 0.125, label, fontsize=11, color=MUTED, family='monospace', ha='left' if v == 2500 else 'right')

    fig.savefig(OUT, dpi=100, facecolor=BG)
    print(f'wrote {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
