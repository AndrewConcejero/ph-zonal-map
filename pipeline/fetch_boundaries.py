"""Download PSGC boundary files from faeldon/philippines-json-maps into cache/boundaries/.

Files already in the cache are skipped, and requests are spaced out, so re-running is cheap
and polite. Delete cache/ to force a fresh download.

Highly urbanised cities (Angeles, Olongapo, Lucena) have no entry in their province's 2023 file; their barangays
are fetched directly (HUCS) and each outline is the union of its barangays.

Uses the 2023 release (PSGC Dec 2023) everywhere except Manila. In that release Manila's
barangay files are empty GeometryCollections, so Manila comes from the 2019 release, which
splits it into 14 sub-municipalities (Tondo I/II, Binondo, ...).
"""
import json
import time
import urllib.request
from pathlib import Path

BASE = 'https://raw.githubusercontent.com/faeldon/philippines-json-maps/master'
CACHE = Path(__file__).resolve().parent.parent / 'cache' / 'boundaries'
DELAY_S = 0.4

# province / NCR district code -> display province
PROVDISTS = {
    '1303900000': 'Metro Manila',  # Manila
    '1307400000': 'Metro Manila',  # 2nd district
    '1307500000': 'Metro Manila',  # 3rd district
    '1307600000': 'Metro Manila',  # 4th district
    '301400000': 'Bulacan',
    '402100000': 'Cavite',
    '403400000': 'Laguna',
    '405800000': 'Rizal',
    '305400000': 'Pampanga',
    '401000000': 'Batangas',
    '307100000': 'Zambales',
    '405600000': 'Quezon',
}
# highly urbanised cities left out of their province's file in the 2023 release: code -> (name, display province)
HUCS = {'330100000': ('Angeles', 'Pampanga'), '331400000': ('Olongapo', 'Zambales'), '431200000': ('Lucena', 'Quezon')}
MANILA_PSGC = '1380600000'
MANILA_SUBMUNS_2019 = [f'ph1339{n:02d}000' for n in range(1, 15)]


def provdist_path(code: str) -> Path:
    return CACHE / f'municities-provdist-{code}.json'


def municity_path(code: str) -> Path:
    return CACHE / f'bgysubmuns-municity-{code}.json'


def manila_path(code: str) -> Path:
    return CACHE / f'manila-2019-{code}.json'


def fetch(url: str, dest: Path) -> None:
    if dest.exists():
        return
    print('  fetching', url.removeprefix(BASE))
    with urllib.request.urlopen(url, timeout=60) as r:
        body = r.read()
    json.loads(body)  # fail loudly on a bad download instead of caching it
    dest.write_bytes(body)
    time.sleep(DELAY_S)


def municity_codes(provdist: str) -> list[str]:
    g = json.loads(provdist_path(provdist).read_text())
    return [str(f['properties']['adm3_psgc']) for f in g['features']]


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for code in PROVDISTS:
        fetch(f'{BASE}/2023/geojson/provdists/medres/municities-provdist-{code}.0.01.json', provdist_path(code))
    for code in PROVDISTS:
        for m in municity_codes(code):
            if m == MANILA_PSGC:
                continue
            fetch(f'{BASE}/2023/geojson/municities/medres/bgysubmuns-municity-{m}.0.01.json', municity_path(m))
    for m in HUCS:
        fetch(f'{BASE}/2023/geojson/municities/medres/bgysubmuns-municity-{m}.0.01.json', municity_path(m))
    for s in MANILA_SUBMUNS_2019:
        fetch(f'{BASE}/2019/geojson/barangays/medres/barangays-municity-{s}.0.01.json', manila_path(s))
    print(f'boundaries cached in {CACHE}: {len(list(CACHE.glob("*.json")))} files')


if __name__ == '__main__':
    main()
