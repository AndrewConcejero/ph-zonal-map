"""Match source barangay names to PSGC boundary features, one city at a time.

Order of attempts: pipeline/aliases.csv, exact normalized key, then fuzzy (rapidfuzz) on the
normalized key. Fuzzy matches are accepted only when the score clears THRESHOLD and beats the
runner-up by MARGIN, and never for purely numeric names ("12" vs "112" are different places).
Every decision is returned so build.py can write a match report.
"""
import csv
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process

from .normalize import norm_barangay, norm_city

THRESHOLD = 90
MARGIN = 5
ALIASES = Path(__file__).resolve().parent / 'aliases.csv'


@dataclass
class Match:
    city: str
    source_name: str
    psgc: str | None
    psgc_name: str | None
    method: str  # alias | exact | fuzzy | unmatched
    score: float
    candidates: str = ''  # for unmatched: top suggestions "name (score); ..."


def load_aliases(path: Path = ALIASES) -> dict[tuple[str, str], str]:
    out = {}
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            out[(norm_city(r['city']), norm_barangay(r['source_name']))] = norm_barangay(r['psgc_name'])
    return out


def match_city(city: str, source_names: list[str], features: dict[str, str],
               aliases: dict[tuple[str, str], str]) -> list[Match]:
    """features: psgc -> boundary name, all within `city`."""
    by_key: dict[str, str] = {}
    for psgc, name in features.items():
        by_key.setdefault(norm_barangay(name), psgc)
    taken: set[str] = set()
    results: list[Match] = []
    pending: list[str] = []
    ck = norm_city(city)

    for src in source_names:
        k = norm_barangay(src)
        target = aliases.get((ck, k))
        if target and target in by_key:
            psgc = by_key[target]
            results.append(Match(city, src, psgc, features[psgc], 'alias', 100))
            taken.add(psgc)
        elif k in by_key and by_key[k] not in taken:
            psgc = by_key[k]
            results.append(Match(city, src, psgc, features[psgc], 'exact', 100))
            taken.add(psgc)
        else:
            pending.append(src)

    for src in pending:
        k = norm_barangay(src)
        choices = {key: psgc for key, psgc in by_key.items() if psgc not in taken}
        top = process.extract(k, list(choices), scorer=fuzz.ratio, limit=3)
        cands = '; '.join(f'{features[choices[key]]} ({score:.0f})' for key, score, _ in top)
        best_ok = (top and not k.isdigit() and top[0][1] >= THRESHOLD
                   and (len(top) == 1 or top[0][1] - top[1][1] >= MARGIN))
        if best_ok:
            psgc = choices[top[0][0]]
            results.append(Match(city, src, psgc, features[psgc], 'fuzzy', top[0][1], cands))
            taken.add(psgc)
        else:
            results.append(Match(city, src, None, None, 'unmatched', top[0][1] if top else 0, cands))
    return results
