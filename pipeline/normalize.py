"""Barangay and city name normalization, shared by the BIR parser and the PSGC matcher.

The goal is a key that is equal for spellings people treat as the same place:
"Sto. Niño", "Santo Nino", "STO NIÑO" -> "santonino"; "Brgy. 001", "Barangay 1", "Bgy. No. 1" -> "1".
"""
import re
import unicodedata

_PREFIX = re.compile(r'^(city of|municipality of)\s+')
_BRGY = re.compile(r'\b(barangay|brgy|bgy|brg)\b\.?\s*(no\.?\s*)?', re.I)
_ABBREV = [
    (re.compile(r'\bsto\b\.?'), 'santo'),
    (re.compile(r'\bsta\b\.?'), 'santa'),
    (re.compile(r'\bsn\b\.?'), 'san'),
    (re.compile(r'\bgen\b\.?'), 'general'),
    (re.compile(r'\bpob\b\.?'), 'poblacion'),
    (re.compile(r'\bnorth\b'), 'n'),
    (re.compile(r'\bsouth\b'), 's'),
    (re.compile(r'\beast\b'), 'e'),
    (re.compile(r'\bwest\b'), 'w'),
]
_ROMAN = {'i': '1', 'ii': '2', 'iii': '3', 'iv': '4', 'v': '5', 'vi': '6', 'vii': '7', 'viii': '8', 'ix': '9', 'x': '10'}


def ascii_fold(s: str) -> str:
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()


def norm_city(s: str) -> str:
    s = ascii_fold(s).lower().strip()
    s = _PREFIX.sub('', s)
    s = re.sub(r'\s+city$', '', s)
    return re.sub(r'[^a-z0-9]', '', s)


def norm_barangay(s: str) -> str:
    s = ascii_fold(s).lower().strip()
    s = re.sub(r'\(.*?\)', ' ', s)  # "Poblacion (Bgy. 1)" -> "Poblacion"
    s = _BRGY.sub(' ', s)
    for pat, rep in _ABBREV:
        s = pat.sub(rep, s)
    # trailing roman numerals: "Bagong Silang II" -> "bagong silang 2"
    s = re.sub(r'\b(i|ii|iii|iv|v|vi|vii|viii|ix|x)\s*$', lambda m: _ROMAN[m.group(1)], s)
    s = re.sub(r'\b0+(\d)', r'\1', s)  # "001" -> "1"
    return re.sub(r'[^a-z0-9]', '', s)
