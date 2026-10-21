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
from .match import load_aliases
from .normalize import ascii_fold, norm_barangay, norm_city

ALIASES = Path(__file__).resolve().parent / 'aliases.csv'
THRESHOLD = 90
MARGIN = 5
NUMBERED_CITIES = {'Manila', 'Caloocan', 'Pasay'}  # barangays are numbers


def clean_bir_barangay(name: str, city: str) -> str:
    s = name.strip().strip('*').strip()
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
         'uno': '1', 'dos': '2', 'tres': '3', 'kuatro': '4', 'cuatro': '4', 'singko': '5', 'cinco': '5'}
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
    return list(dict.fromkeys(k for k in keys if k.strip('~')))


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


def match_bir_names(names_by_city: dict[tuple[str, str], dict[str, int]],
                    features_by_city: dict[tuple[str, str], dict[str, str]]) -> list[BirMatch]:
    """names_by_city: (province, city) -> {BIR name: row count}; features: (province, city) -> {psgc: name}."""
    aliases = load_aliases(ALIASES)
    index = {k: CityIndex(v, k[1] in NUMBERED_CITIES) for k, v in features_by_city.items()}
    out = []
    for (prov, city), names in names_by_city.items():
        idx = index.get((prov, city)) or CityIndex({})
        feats = idx.feats
        for raw, count in names.items():
            cleaned = clean_bir_barangay(raw, city)
            m = lambda psgc, method, score=100, cands='', name=None: out.append(
                BirMatch(prov, city, raw, cleaned, psgc, name or (feats.get(psgc) if psgc else None), method, score, cands, count))
            alias = aliases.get((norm_city(city), norm_barangay(raw))) or aliases.get((norm_city(city), norm_barangay(cleaned)))
            if alias:
                hit = next((p for p, n in feats.items() if norm_barangay(n) == alias), None)
                if hit:
                    m(hit, 'alias')
                    continue
            hit = idx.exact(cleaned)
            if hit:
                m(hit, 'exact')
                continue
            grp = idx.group(cleaned)
            if grp:
                m(';'.join(grp), 'group', name='; '.join(feats[p] for p in grp))
                continue
            moved = [(c2, index[(prov, c2)].exact(cleaned)) for c2 in CITY_MOVES.get((prov, city), [])
                     if (prov, c2) in index]
            moved = [(c2, h) for c2, h in moved if h]
            if moved:
                c2, h = moved[0]
                m(h, 'moved_city', 100, f'PSGC lists it under {c2}', name=f'{index[(prov, c2)].feats[h]} ({c2})')
                continue
            k = norm_barangay(cleaned)
            choices = {norm_barangay(n): p for p, n in feats.items()}
            top = process.extract(k, list(choices), scorer=fuzz.ratio, limit=3) if k and choices else []
            cands = '; '.join(f'{feats[choices[key]]} ({sc:.0f})' for key, sc, _ in top)
            digits = lambda x: re.findall(r'\d+', x)
            ok = (top and digits(k) == digits(top[0][0]) and top[0][1] >= THRESHOLD
                  and (len(top) == 1 or top[0][1] - top[1][1] >= MARGIN))
            if not ok and k and not digits(k):
                # BIR typos like "SINCAMAS" for Singkamas: compare spelling-normalised forms
                ph = {phonetic(key): p for key, p in choices.items() if not digits(key)}
                ptop = process.extract(phonetic(k), list(ph), scorer=fuzz.ratio, limit=2)
                if ptop and ptop[0][1] >= THRESHOLD and (len(ptop) == 1 or ptop[0][1] - ptop[1][1] >= MARGIN):
                    m(ph[ptop[0][0]], 'fuzzy', ptop[0][1], cands)
                    continue
            if not ok:
                hit = idx.partial(cleaned)
                if hit:
                    m(hit, 'partial', 0, cands)
                    continue
            if ok:
                m(choices[top[0][0]], 'fuzzy', top[0][1], cands)
            else:
                m(None, 'unmatched', top[0][1] if top else 0, cands)
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


def barangay_values(rows: list[dict], psgc_of: dict[tuple[str, str, str], str]) -> dict[str, list[DoValue]]:
    """psgc -> [DoValue per DO], oldest first. psgc_of: (province, city, BIR name) -> psgc, or 'a;b;c'."""
    acc: dict[tuple[str, str], dict] = defaultdict(lambda: {'streets': {}, 'meta': None})
    for r in rows:
        if r['class_code'] not in RESIDENTIAL_LAND:
            continue
        psgcs = psgc_of.get((r['province'], r['city'], r['barangay']))
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
