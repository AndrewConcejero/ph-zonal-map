"""Turn parsed BIR rows into per-barangay values for every Department Order.

  interior lot = min residential land value (class RR) in the barangay
  prime        = max RR value
  typical      = median RR value across street rows, each street/vicinity counted once

RC (residential condominium) is left out on purpose: it is priced per sqm of unit floor area, not land.
BIR barangay names are cleaned (continuation markers, footnote asterisks, Manila "124/10", Pasay "12 ZONE 4")
and matched to PSGC barangays many-to-one, since BIR often splits one barangay into several blocks.
"""
from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process

from .bir_parser import RESIDENTIAL_LAND
from .normalize import ascii_fold, norm_barangay, norm_city

ALIASES = Path(__file__).resolve().parent / 'aliases.csv'
THRESHOLD = 90
MARGIN = 5
NUMBERED_CITIES = {'Manila', 'Caloocan', 'Pasay'}  # barangays are numbers


def clean_bir_barangay(name: str, city: str) -> str:
    s = name.strip().strip('*').strip()
    s = re.sub(r'^\d{3,4}\s*-\s*(?=[A-Z])', '', s, flags=re.I)  # Paete "0004 - QUINALE (POBLACION)"
    s = re.sub(r'\(?\s*\b(cont|conti|coni|continu)\w*\.?[\'’]?d?\s*\)?\s*$', '', s, flags=re.I).strip(' -–(')
    s = re.sub(r'\s*-\s*$', '', s)
    s = s.strip('*').strip()
    if city in NUMBERED_CITIES:
        # Manila "Zone 46 / Brgy 471", "124/10" (barangay/zone), "74677" (zone 74 + barangay 677, slash lost);
        # Pasay "12 ZONE 4"; Caloocan "12"; sometimes "Brgy. 12-A"
        m = re.search(r'\b(?:BRGY|BARANGAY|BGY)\.?\s*(\d{1,3})\s*-?\s*([A-Z])?\b', s, re.I)
        if not m and re.fullmatch(r'\d{5,6}', s) and int(s[-3:]) <= 905:
            return f'Barangay {int(s[-3:])}'
        m = m or re.match(r'^(\d{1,3})\s*-?\s*([A-Z])?\b', s, re.I)
        if m and 0 < int(m.group(1)) <= 905:
            return f'Barangay {int(m.group(1))}{("-" + m.group(2).upper()) if m.group(2) else ""}'
    return s


def variants(name: str) -> list[str]:
    """Spellings to try for an exact key match, most specific first."""
    out = [name]
    no_paren = re.sub(r'\(.*?\)', ' ', name).strip()
    inside = re.findall(r'\((.*?)\)', name)
    out.append(no_paren)
    out.append(re.sub(r'\b(VILLAGE|SUBDIVISION|SUBD\.?|PROPER)\b', ' ', no_paren, flags=re.I))
    out += re.split(r'\s+-\s+|\s*–\s*', no_paren)[:1]  # "SAN LORENZO - AYALA CENTER" -> "SAN LORENZO"
    out += re.split(r'-(?=NORTH|SOUTH|EAST|WEST)', no_paren, flags=re.I)[:1]  # "PIO DEL PILAR-NORTH"
    out += [re.sub(r'^\s*FORMERLY\s+', '', x, flags=re.I) for x in inside]  # "(FORMERLY TABING BAKOD)"
    out.append(re.sub(r'^POBLACION\s*-\s*', '', no_paren, flags=re.I))  # Angat "POBLACION - SAN ROQUE"
    return [v.strip() for v in dict.fromkeys(out) if v.strip()]


_ROMAN = {'i': '1', 'ii': '2', 'iii': '3', 'iv': '4', 'v': '5', 'vi': '6', 'vii': '7', 'viii': '8', 'ix': '9', 'x': '10'}
_WORD = {'sto': 'santo', 'sta': 'santa', 'st': 'saint', 'sn': 'san', 'gen': 'general', 'pob': 'poblacion',
         'brgy': '', 'bgy': '', 'barangay': '', 'n': 'north', 's': 'south', 'e': 'east', 'w': 'west',
         'uno': '1', 'dos': '2', 'tres': '3', 'kuatro': '4', 'cuatro': '4', 'singko': '5', 'cinco': '5',
         '1st': '1', '2nd': '2', '3rd': '3', '4th': '4', '5th': '5', '6th': '6', '7th': '7', '8th': '8'}
# barangays PSGC moved to another city: the 2023 Supreme Court ruling put Makati's EMBO barangays in Taguig
CITY_MOVES = {('Metro Manila', 'Makati'): ['Taguig']}


def phonetic(word: str) -> str:
    """Filipino spelling variants: Pantok/Pantoc, Guitnang/Gitnang, Quiapo/Kiapo."""
    w = word.replace('qu', 'k').replace('gu', 'g').replace('c', 'k').replace('ll', 'ly').replace('ph', 'f')
    return re.sub(r'(.)\1+', r'\1', w)
SUFFIX_RE = re.compile(r'^(\d{1,2}|[ivx]{1,4}|[a-h]|[ivx]{1,4}[a-h]|\d[a-h]|north|south|east|west|proper|ext|extension)$')


def tokens(name: str, drop=('village', 'subdivision', 'subd', 'homes', 'proper')) -> list[str]:
    t = ascii_fold(name).lower().replace("'", '').replace('’', '')
    out = []
    for w in re.split(r'[^a-z0-9]+', t):
        w = _WORD.get(w, w)
        w = _ROMAN.get(w, w)
        if w and w not in drop:
            out.append(w)
    return out


def keys_for(name: str) -> list[str]:
    """Exact-match keys for a name: as written, without filler words, in any word order, and phonetic."""
    full = tokens(name, drop=())
    lean = tokens(name)
    keys = [''.join(full), ''.join(lean), ' '.join(sorted(full)), ' '.join(sorted(lean))]
    keys.append('~' + ' '.join(sorted(phonetic(w) for w in lean)))
    if re.search(r'\bPob\.?\s*$', name, re.I) and len(lean) > 1:
        keys.append(''.join(lean[:-1]))  # General Trias "Arnaldo Pob." is BIR's "ARNALDO"
    return list(dict.fromkeys(k for k in keys if k.strip('~')))


_RANGE_RE = re.compile(r'^(?P<stem>.*?\b)?(?P<a>\d{1,3}|[IVX]{1,4})\s*(?:TO|-)\s*(?P<b>\d{1,3}|[IVX]{1,4})\b(?P<rest>.*)$', re.I)
_ROMAN_N = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10}


def expand_range(name: str) -> list[str]:
    """'ESCOPA I TO IV' -> Escopa I..IV; 'LIGTONG 1 TO 4'; Pasay '195 TO 198 ZONE 20' -> 195..198. [] if no range."""
    m = _RANGE_RE.match(name.strip().strip('*'))
    if not m:
        return []
    a, b = m.group('a').upper(), m.group('b').upper()
    roman = a in _ROMAN_N and b in _ROMAN_N
    if not roman and not (a.isdigit() and b.isdigit()):
        return []
    lo, hi = (_ROMAN_N[a], _ROMAN_N[b]) if roman else (int(a), int(b))
    if not 0 < hi - lo <= 10:
        return []
    inv = {v: k for k, v in _ROMAN_N.items()}
    stem = (m.group('stem') or '').strip()
    return [f'{stem} {inv[n] if roman else n}{m.group("rest") if not stem else ""}'.strip() for n in range(lo, hi + 1)]


@dataclass
class BirMatch:
    province: str
    city: str
    bir_name: str
    cleaned: str
    psgc: str | None  # several PSGC codes joined by ';' for a group match
    psgc_name: str | None
    method: str
    score: float
    candidates: str
    rows: int
    valid_before: str = ''      # dated alias: applies to schedules effective before this date...
    otherwise: str | None = None  # ...and later schedules use this automatic match instead


@dataclass
class Alias:
    names: list[str]       # PSGC names; several = the BIR block covers all of them
    city: str              # PSGC city of those names (another city when BIR filed it elsewhere)
    valid_before: str
    ignore: bool


def load_bir_aliases(path: Path = ALIASES) -> dict[tuple[str, str], Alias]:
    """aliases.csv: city, source_name, psgc_name ('a; b' for several, '(ignore)' for non-barangay sections),
    psgc_city (if not the same city), valid_before (ISO date: only for schedules effective before it), note."""
    out = {}
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            out[(norm_city(r['city']), norm_barangay(r['source_name']))] = Alias(
                [n.strip() for n in r['psgc_name'].split(';') if n.strip()], (r.get('psgc_city') or '').strip() or r['city'],
                (r.get('valid_before') or '').strip(), r['psgc_name'].strip() == '(ignore)')
    return out


class CityIndex:
    def __init__(self, feats: dict[str, str], numbered: bool = False):
        self.feats = feats
        self.numbered = numbered
        self.by_key: dict[str, set[str]] = defaultdict(set)
        for psgc, n in feats.items():
            for k in keys_for(n):
                self.by_key[k].add(psgc)
        self.lean = {psgc: tokens(n) for psgc, n in feats.items()}

    def exact(self, name: str) -> str | None:
        for v in variants(name):
            for k in keys_for(v):
                hits = self.by_key.get(k)
                if hits and len(hits) == 1:
                    return next(iter(hits))
        return None

    def group(self, name: str) -> list[str]:
        """PSGC barangays that split from, or are listed in, this BIR name:
        'Poblacion' -> Poblacion I, II, III; 'Galicia I, II & III' -> the three; 'Putol / Magdalo' -> Magdalo."""
        base = tokens(re.sub(r'\(.*?\)', ' ', name))
        if not base:
            return []
        out = [p for p, t in self.lean.items()
               if len(t) > len(base) and t[:len(base)] == base and all(SUFFIX_RE.match(x) for x in t[len(base):])]
        if len(out) >= 2:
            return sorted(out)
        # "Poblacion" blocks: the town-centre barangays, which these PSGC files name "Barangay I".."Barangay V"
        # (the "(Pob.)" suffix is dropped) or "Poblacion I".. A number picks one; no number means all of them.
        if base[0] == 'poblacion' and all(x.isdigit() for x in base[1:]) and not self.numbered:
            pob = [p for p, t in self.lean.items() if 'poblacion' in t or (t and all(x.isdigit() for x in t))]
            nums = base[1:]
            if nums:
                one = [p for p in pob if [x for x in self.lean[p] if x.isdigit()] == nums]
                return one if len(one) == 1 else []
            if pob:
                return sorted(pob)
        # lists: "GALICIA I, II & III", "PUTOL / MAGDALO", "CLARO/Quirino 3-B"
        if re.search(r'[,&/]|\band\b', name, re.I):
            parts = [x.strip() for x in re.split(r'\s*(?:,|&|/|\band\b)\s*', name, flags=re.I) if x.strip()]
            stem = re.sub(r'\s+\S+$', '', parts[0]) if len(parts[0].split()) > 1 else ''
            hits = set()
            for i, part in enumerate(parts):
                h = self.exact(part) or (self.exact(f'{stem} {part}') if stem and i else None)
                if h:
                    hits.add(h)
            if hits:
                return sorted(hits)
        return []

    def partial(self, name: str) -> str | None:
        """The BIR name's words are the tail of a PSGC name: 'F. de Castro' -> Francisco De Castro,
        'Maderan' -> Gavino Maderan, 'Batasan (M)' -> Batasan Matanda. Flagged for review."""
        m = re.match(r'^(.*?)\s*\(([A-Z])\)\s*$', name.strip(), re.I)
        if m:
            base, initial = tokens(m.group(1)), m.group(2).lower()
            hits = [p for p, t in self.lean.items()
                    if len(t) == len(base) + 1 and t[:len(base)] == base and t[-1].startswith(initial)]
            return hits[0] if len(hits) == 1 else None
        bt = [w for w in tokens(name) if len(w) > 1]
        if not bt or sum(len(w) for w in bt) < 5:
            return None
        # only the BIR name may be the shorter one: the reverse ("Ayala Alabang" -> Alabang) guesses wrong
        hits = [p for p, t in self.lean.items() if len(t) > len(bt) and t[-len(bt):] == bt]
        return hits[0] if len(hits) == 1 else None


def _auto_match(idx: 'CityIndex', index: dict, prov: str, city: str, raw: str, cleaned: str):
    """-> (psgc or 'a;b', method, score, candidates, display name) without aliases."""
    feats = idx.feats
    # ranges first: "195 TO 198 ZONE 20" would otherwise match Barangay 195 alone
    rng = expand_range(raw) or expand_range(cleaned)
    if rng:
        hits = sorted({h for h in (idx.exact(clean_bir_barangay(x, city)) for x in rng) if h})
        if len(hits) >= 2:
            return ';'.join(hits), 'range', 100, '', '; '.join(feats[h] for h in hits)
    hit = idx.exact(cleaned)
    if hit:
        return hit, 'exact', 100, '', feats[hit]
    grp = idx.group(cleaned)
    if grp:
        return ';'.join(grp), 'group', 100, '', '; '.join(feats[p] for p in grp)
    for c2 in CITY_MOVES.get((prov, city), []):
        h = index[(prov, c2)].exact(cleaned) if (prov, c2) in index else None
        if h:
            return h, 'moved_city', 100, f'PSGC lists it under {c2}', f'{index[(prov, c2)].feats[h]} ({c2})'
    k = norm_barangay(cleaned)
    choices = {norm_barangay(n): p for p, n in feats.items()}
    top = process.extract(k, list(choices), scorer=fuzz.ratio, limit=3) if k and choices else []
    cands = '; '.join(f'{feats[choices[key]]} ({sc:.0f})' for key, sc, _ in top)
    digits = lambda x: re.findall(r'\d+', x)
    if top and digits(k) == digits(top[0][0]) and top[0][1] >= THRESHOLD and (len(top) == 1 or top[0][1] - top[1][1] >= MARGIN):
        p = choices[top[0][0]]
        return p, 'fuzzy', top[0][1], cands, feats[p]
    if k and not digits(k):
        # BIR typos like "SINCAMAS" for Singkamas: compare spelling-normalised forms
        ph = {phonetic(key): p for key, p in choices.items() if not digits(key)}
        ptop = process.extract(phonetic(k), list(ph), scorer=fuzz.ratio, limit=2)
        if ptop and ptop[0][1] >= THRESHOLD and (len(ptop) == 1 or ptop[0][1] - ptop[1][1] >= MARGIN):
            p = ph[ptop[0][0]]
            return p, 'fuzzy', ptop[0][1], cands, feats[p]
    hit = idx.partial(cleaned)
    if hit:
        return hit, 'partial', 0, cands, feats[hit]
    return None, 'unmatched', top[0][1] if top else 0, cands, None


def match_bir_names(names_by_city: dict[tuple[str, str], dict[str, int]],
                    features_by_city: dict[tuple[str, str], dict[str, str]]) -> list[BirMatch]:
    """names_by_city: (province, city) -> {BIR name: row count}; features: (province, city) -> {psgc: name}."""
    aliases = load_bir_aliases(ALIASES)
    index = {k: CityIndex(v, k[1] in NUMBERED_CITIES) for k, v in features_by_city.items()}
    out = []
    for (prov, city), names in names_by_city.items():
        idx = index.get((prov, city)) or CityIndex({})
        for raw, count in names.items():
            cleaned = clean_bir_barangay(raw, city)
            auto = _auto_match(idx, index, prov, city, raw, cleaned)
            al = aliases.get((norm_city(city), norm_barangay(raw))) or aliases.get((norm_city(city), norm_barangay(cleaned)))
            if al and al.ignore:
                out.append(BirMatch(prov, city, raw, cleaned, None, None, 'ignored', 100, 'aliases.csv: not a barangay', count))
                continue
            if al:
                target = index.get((prov, al.city)) or CityIndex({})
                by_norm = {norm_barangay(n): p for p, n in target.feats.items()}
                codes = [by_norm.get(norm_barangay(n)) for n in al.names]
                if codes and all(codes):
                    shown = '; '.join(al.names) + (f' ({al.city})' if al.city != city else '')
                    out.append(BirMatch(prov, city, raw, cleaned, ';'.join(codes), shown, 'alias', 100, '', count,
                                        al.valid_before, auto[0] if al.valid_before else None))
                    continue
                out.append(BirMatch(prov, city, raw, cleaned, None, None, 'alias target missing', 0,
                                    f'aliases.csv names {"; ".join(al.names)} in {al.city}, not found', count))
                continue
            psgc, method, score, cands, name = auto
            out.append(BirMatch(prov, city, raw, cleaned, psgc, name, method, score, cands, count))
    return out


@dataclass
class DoValue:
    psgc: str
    rdo: str
    do_number: str
    effective_date: str
    lo: float
    hi: float
    typ: float
    n_streets: int
    streets: dict  # (street, vicinity) -> value, for matching across revisions


def resolve_target(target, effective_date: str) -> str | None:
    """psgc_of values are 'a;b', or for dated aliases {'before': date, 'alias': 'a;b', 'else': 'c' or None}."""
    if isinstance(target, dict):
        return target['alias'] if effective_date and effective_date < target['before'] else target['else']
    return target


def newest_listing(rows: list[dict], psgc_of: dict) -> dict[str, tuple[str, str]]:
    """psgc -> (effective date, DO) of the newest schedule listing the barangay at all, any class."""
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        t = resolve_target(psgc_of.get((r['province'], r['city'], r['barangay'])), r['effective_date'])
        for p in (t or '').split(';'):
            if p and (r['effective_date'], r['do_number']) > out.get(p, ('', '')):
                out[p] = (r['effective_date'], r['do_number'])
    return out


def barangay_values(rows: list[dict], psgc_of: dict[tuple[str, str, str], str],
                    classes: set[str] = RESIDENTIAL_LAND) -> dict[str, list[DoValue]]:
    """psgc -> [DoValue per DO], oldest first. psgc_of: (province, city, BIR name) -> see resolve_target."""
    acc: dict[tuple[str, str], dict] = defaultdict(lambda: {'streets': {}, 'meta': None})
    for r in rows:
        if r['class_code'] not in classes:
            continue
        psgcs = resolve_target(psgc_of.get((r['province'], r['city'], r['barangay'])), r['effective_date'])
        if not psgcs:
            continue
        street = (re.sub(r'\W+', ' ', r['street_or_building'].upper()).strip(),
                  re.sub(r'\W+', ' ', r['vicinity'].upper()).strip())
        for psgc in psgcs.split(';'):  # a group match applies the block to every barangay split from it
            a = acc[(psgc, r['do_number'])]
            a['meta'] = (r['rdo'], r['effective_date'])
            # a street listed twice (e.g. on a continuation page) counts once, at its higher value
            a['streets'][street] = max(a['streets'].get(street, 0), float(r['value_per_sqm']))
    out: dict[str, list[DoValue]] = defaultdict(list)
    for (psgc, do), a in acc.items():
        vals = list(a['streets'].values())
        rdo, eff = a['meta']
        out[psgc].append(DoValue(psgc, rdo, do, eff, min(vals), max(vals), statistics.median(vals), len(vals), a['streets']))
    for v in out.values():
        v.sort(key=lambda d: d.effective_date)
    return out


def write_matches(path: Path, matches: list[BirMatch]) -> None:
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(BirMatch.__dataclass_fields__))
        w.writeheader()
        for m in sorted(matches, key=lambda m: (m.method != 'unmatched', m.province, m.city, m.bir_name)):
            w.writerow(vars(m))
