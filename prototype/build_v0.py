"""Build merged GeoJSON of zonal values: NCR at barangay level, outskirts at city level."""
import csv, json, math, re, sys, unicodedata
from pathlib import Path

GEO = Path('/root/pjm/2023/geojson')
D = Path('/home/claude/zv/data')
OUT = Path('/home/claude/zv/out'); OUT.mkdir(exist_ok=True)

def norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'^(city of|municipality of)\s+', '', s)
    s = s.replace('sto.', 'santo').replace('sta.', 'santa').replace('gen.', 'general')
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

def rnd(c):
    if isinstance(c[0], (int, float)): return [round(c[0], 4), round(c[1], 4)]
    return [rnd(x) for x in c]

NCR_FILES = {  # psgc -> city name used in CSV
    '1380100000': 'Caloocan', '1380200000': 'Las Piñas', '1380300000': 'Makati', '1380400000': 'Malabon',
    '1380500000': 'Mandaluyong', '1380700000': 'Marikina', '1380800000': 'Muntinlupa', '1380900000': 'Navotas',
    '1381000000': 'Parañaque', '1381100000': 'Pasay', '1381200000': 'Pasig', '1381300000': 'Quezon City',
    '1381400000': 'San Juan', '1381500000': 'Taguig', '1381600000': 'Valenzuela', '1381701000': 'Pateros'}

med = {}; eff = {}
for r in csv.DictReader(open(D / 'city_medians.csv', encoding='utf-8')):
    med[norm(r['city'])] = int(r['median_res']); eff[norm(r['city'])] = r['schedule_effective']

bgy = {}
for r in csv.DictReader(open(D / 'ncr_barangays.csv', encoding='utf-8')):
    bgy[(r['city'], norm(r['barangay']))] = (int(r['res_min']), int(r['res_max']))
for line in open(D / 'pasay_barangays.txt'):
    n, a, b = line.strip().split(',')
    bgy[('Pasay', norm('Barangay ' + n))] = (int(a), int(b))

ALIAS = {'pilar': 'pilarvillage', 'nbbskaunlaran': 'northbayblvdsouthkaunlaran', 'nbbsdagatdagatan': 'northbayblvdsouthdagatdagatan'}
feats = []; stats = {'bgy_matched': 0, 'bgy_city_fallback': 0, 'city': 0, 'nodata': 0}
unmatched = {}
for psgc, city in NCR_FILES.items():
    g = json.load(open(GEO / f'municities/medres/bgysubmuns-municity-{psgc}.0.01.json'))
    for f in [x for x in g["features"] if x.get("geometry")]:
        name = f['properties']['adm4_en']
        key = (city, ALIAS.get(norm(name), norm(name)))
        p = {'name': name, 'city': city, 'prov': 'Metro Manila', 'level': 'barangay', 'eff': eff.get(norm(city), '')}
        if key in bgy:
            lo, hi = bgy[key]
            p.update(lo=lo, hi=hi, typ=round(math.sqrt(lo * hi)), q='barangay')
            stats['bgy_matched'] += 1
        else:
            m = med[norm(city)]
            p.update(lo=m, hi=m, typ=m, q='city median')
            stats['bgy_city_fallback'] += 1
            unmatched.setdefault(city, []).append(name)
        feats.append({'type': 'Feature', 'properties': p, 'geometry': {'type': f['geometry']['type'], 'coordinates': rnd(f['geometry']['coordinates'])}})

# Manila (single polygon) + outskirts at city level
for prov_file, prov in [('1303900000', 'Metro Manila'), ('402100000', 'Cavite'), ('403400000', 'Laguna'),
                        ('405800000', 'Rizal'), ('301400000', 'Bulacan')]:
    g = json.load(open(GEO / f'provdists/medres/municities-provdist-{prov_file}.0.01.json'))
    for f in [x for x in g["features"] if x.get("geometry")]:
        name = f['properties']['adm3_en']; k = norm(name)
        if k == 'cavite': k = 'cavitecity'
        disp = re.sub(r'^City of ', '', name)
        p = {'name': disp, 'city': disp, 'prov': prov, 'level': 'city', 'eff': eff.get(k, '')}
        if k in med:
            m = med[k]; p.update(lo=m, hi=m, typ=m, q='city median'); stats['city'] += 1
        else:
            p.update(lo=None, hi=None, typ=None, q='no data'); stats['nodata'] += 1
        feats.append({'type': 'Feature', 'properties': p, 'geometry': {'type': f['geometry']['type'], 'coordinates': rnd(f['geometry']['coordinates'])}})

json.dump({'type': 'FeatureCollection', 'features': feats}, open(OUT / 'zonal.geojson', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print(stats)
for c, v in unmatched.items(): print(c, len(v), v[:12])
used = {(c, b) for f in feats for c, b in [(f['properties']['city'], norm(f['properties']['name']))]}
missing = [k for k in bgy if k not in used]
print('csv rows not on map:', len(missing), missing[:30])
