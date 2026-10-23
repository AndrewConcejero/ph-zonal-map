"""Parse BIR zonal value Department Order sheets into tidy rows.

A DO sheet is a memo (subject, definitions, legend) followed by many blocks like:

    PROVINCE          : NCR
    CITY/MUNICIPALITY : MAKATI CITY            D.O. NO.        037-2021
    BARANGAY          : SAN LORENZO            Effectivity Date Dec. 22, 2021
    STREET/SUBDIVISION | VICINITY | CLASSIFICATION | 7th REV. ZV / SQM
    EAST DRIVE         | AYALA CTR| CR             | 750000
                       |          | RR             | 300000      <- street carried down from the row above

Layouts differ between RDOs and years: labels split across cells or not, columns in different positions,
several value columns (previous / recommended / proposed / final) side by side, headers spanning two rows,
Manila's "Barangay/Zone 124/10". So nothing is positional: each block's own header row decides the columns.

Every row that is not a label, header or value row is logged with a reason, never silently dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .inventory import Book, parse_date, parse_do, text

# class codes BIR uses; trailing asterisks are footnote markers
CLASS_RE = re.compile(r'^(RR|CR|RC|CC|CL|GL|GP|PS|APD|TH|I|X|DA|A\d{0,2}|R\d?|C\d?)(?:\s*-?\s*\d)?\**$')
ZONE_RE = re.compile(r'^\d{1,3}\s*-?\s*[A-Z]?\s*/\s*\d{1,3}$', re.I)  # Manila "Barangay/Zone" value, e.g. 236/22
RESIDENTIAL_LAND = {'RR'}  # RC is per sqm of condo unit floor area, not land, so it is kept out of land values
ORDINAL_RE = re.compile(r'(\d{1,2})\s*(?:ST|ND|RD|TH)\b', re.I)
NUM_RE = re.compile(r'^(?:P|PHP|₱)?\s*([\d,]+(?:\.\d+)?)\s*\**$', re.I)

PROVINCE_RE = re.compile(r'^PROVINCE\b\s*:?\s*(.*)$', re.I)
CITY_RE = re.compile(r'^(?:CITY\s*/\s*MUNICIPALITY|MUNICIPALITY\s*/\s*CITY|CITY|MUNICIPALITY)\b\s*:?\s*(.*)$', re.I)
BRGY_RE = re.compile(r'^(?:ZONE\s*/\s*)?(?:BARANGAY|BRGY\.?)(?:\s*/\s*ZONE)?\s*:\s*(.*)$'
                     r'|^(?:ZONE\s*/\s*(?:BARANGAY|BRGY\.?)|(?:BARANGAY|BRGY\.?)\s*/\s*ZONE)\s*(.*)$|^BARANGAY\s*$', re.I)
BRGY_BARE_RE = re.compile(r'^BARANGAY\s+(\S.*)$', re.I)  # "BARANGAY 1 (HENERAL ...)" with no colon
DO_LABEL_RE = re.compile(r'D\.?\s*O\.?\s*NO', re.I)
EFF_LABEL_RE = re.compile(r'EFF\w*\s*DATE', re.I)


@dataclass
class Row:
    rdo: str
    do_number: str
    effective_date: str
    province: str
    city: str
    barangay: str
    street_or_building: str
    vicinity: str
    class_code: str
    value_per_sqm: float
    sheet: str
    row: int  # 1-based, as Excel shows it


@dataclass
class Problem:
    sheet: str
    row: int
    reason: str
    cells: str


@dataclass
class Columns:
    street: int
    vicinity: int | None
    cls: int
    value: int | None
    value_label: str = ''


@dataclass
class SheetResult:
    rows: list[Row] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    blocks: int = 0


def to_number(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if v > 0 else None
    m = NUM_RE.match(text(v))
    if m:
        n = float(m.group(1).replace(',', ''))
        return n if n > 0 else None
    return None


def clean_classes(v) -> list[str]:
    """'RR' -> ['RR']; 'RR/CR' (corner lots) -> ['RR', 'CR']; anything else -> []."""
    t = text(v).upper().replace(' ', '').strip('*')
    out = []
    for part in t.split('/'):
        m = CLASS_RE.match(part)
        if not m:
            return []
        out.append(m.group(1))
    return out


def clean_class(v) -> str | None:
    c = clean_classes(v)
    return c[0] if c else None


def label_value(cells: list[str], i: int, rest: str) -> str:
    """Value of a 'LABEL : value' pair, whether in the same cell or the next non-empty one."""
    if rest.strip(' :'):
        return rest.strip(' :')
    for c in cells[i + 1:]:
        if c.strip(' :'):
            return c.strip(' :')
    return ''


def value_column(header_rows: list[list[str]], cls: int) -> tuple[int | None, str]:
    """Pick the value column right of the class column: FINAL beats everything, then the highest revision
    ordinal; 'recommended'/'proposed'/'previous' columns lose unless nothing else is there."""
    cands: dict[int, str] = {}
    for r in header_rows:
        for j, t in enumerate(r):
            if j > cls and t:
                cands[j] = (cands.get(j, '') + ' ' + t).strip()
    def score(item):
        j, t = item
        u = t.upper()
        if 'FINAL' in u:
            return (3, 0, j)
        weak = any(w in u for w in ('RECOMMEND', 'PROPOSED', 'PREVIOUS', 'PRESENT', 'EXISTING', 'OLD'))
        m = ORDINAL_RE.search(u)
        ordinal = int(m.group(1)) if m else (0 if 'INITIAL' in u else -1)
        valueish = any(w in u for w in ('ZV', 'SQ', 'REV', 'VALUE', 'INITIAL'))
        return (0 if weak else (2 if valueish else 1), ordinal, j)
    if not cands:
        return None, ''
    j, t = max(cands.items(), key=score)
    return j, t


def squash(t: str) -> str:
    """'S T R E E T  N A M E' and 'CLASSI- FICATION' -> 'STREETNAME', 'CLASSIFICATION'."""
    return re.sub(r'[^A-Z/]', '', t.upper())


def is_header(cells: list[str]) -> tuple[int, int, int | None] | None:
    """(street col, class col, vicinity col) if the row is a table header."""
    sq = [squash(t) for t in cells]
    cls = next((j for j, t in enumerate(sq) if 'CLASS' in t and len(t) < 30), None)
    street = next((j for j, t in enumerate(sq) if re.search(r'STREET|SUBD|LOCATION|IDENTIFIED|NAMEOF', t) and len(t) < 60), None)
    if cls is None or street is None or street >= cls:
        return None
    if any(to_number(c) for c in cells):
        return None
    vic = next((j for j, t in enumerate(sq) if 'VICINITY' in t), None)
    return street, cls, vic


def merge_rows(rows: list[list[str]]) -> list[str]:
    """Column-wise join, for headers split over consecutive rows."""
    width = max(len(r) for r in rows)
    return [' '.join(r[j] for r in rows if j < len(r) and r[j]) for j in range(width)]


def parse_sheet(book: Book, sheet: str, rdo: str, do_number: str, do_effective: str,
                default_city: str = '') -> SheetResult:
    raw = book.rows(sheet, 1_000_000)
    res = SheetResult()
    province = city = barangay = ''
    block_date = ''
    cols: Columns | None = None
    street = vicinity = ''
    pending_header: list[list[str]] | None = None
    recent: list[list[str]] = []  # last non-empty rows without numbers, for split headers

    def problem(i: int, reason: str, cells: list[str]):
        res.problems.append(Problem(sheet, i + 1, reason, ' | '.join(c for c in cells if c)[:300]))

    for i, r in enumerate(raw):
        cells = [text(v) for v in r]
        if not any(cells):
            continue
        first = next((j for j, c in enumerate(cells) if c), 0)
        lead = cells[first]

        # a header spanning two rows: the value label may sit on the row below
        if pending_header is not None:
            is_label = any(rx.match(lead) for rx in (PROVINCE_RE, CITY_RE, BRGY_RE))
            if (len(pending_header) < 3 and not is_label and not any(to_number(c) for c in cells)
                    and not clean_class(cells[cols.cls] if cols and cols.cls < len(cells) else '')):
                pending_header.append(cells)
                j, lab = value_column(pending_header, cols.cls)
                if j is not None:
                    cols.value, cols.value_label = j, lab
                continue
            pending_header = None

        # block labels
        m = PROVINCE_RE.match(lead)
        if m and first <= 1:
            province = label_value(cells, first, m.group(1))
            continue
        has_class = any(clean_class(c) for c in cells)
        m = CITY_RE.match(lead)
        if m and first <= 1 and not has_class:
            rest = re.sub(r'^OF\b\s*', '', m.group(1), flags=re.I)  # "CITY OF IMUS", "MUNICIPALITY OF NAIC"
            val = re.split(r'\s{2,}|D\.?\s*O\.?\s*NO', label_value(cells, first, rest))[0].strip(' :')
            if ZONE_RE.match(val):
                # labels shifted down a row (seen in Manila): the "city" value is the barangay/zone
                barangay = val
            else:
                city, barangay = val, ''
            street = vicinity = ''
            for j, c in enumerate(cells):
                if EFF_LABEL_RE.search(c):
                    block_date = label_value(cells, j, '')
            continue
        m = BRGY_RE.match(lead) or (None if has_class else BRGY_BARE_RE.match(lead))
        if m and first <= 1:
            val = label_value(cells, first, m.group(1) or '') if m.re is BRGY_BARE_RE else \
                label_value(cells, first, m.group(1) or m.group(2) or '')
            val = re.split(r'\s{2,}', val)[0]
            if EFF_LABEL_RE.search(val) or DO_LABEL_RE.search(val):
                val = ''
            if val:  # an empty label keeps the barangay a shifted label row already set
                barangay = val
            street = vicinity = ''
            for j, c in enumerate(cells):
                if EFF_LABEL_RE.search(c):
                    block_date = label_value(cells, j, '')
            res.blocks += 1
            continue
        if any(EFF_LABEL_RE.search(c) for c in cells) and not any(clean_class(c) for c in cells):
            j = next(j for j, c in enumerate(cells) if EFF_LABEL_RE.search(c))
            block_date = label_value(cells, j, '')
            continue

        h = is_header(cells)
        hdr_rows = [cells]
        if not h and not any(to_number(c) for c in cells) and any(
                re.search(r'STREET|SUBD|CLASS|FICATION|VICINITY|ZV|SQ', squash(c)) for c in cells):
            for back in (1, 2):
                if len(recent) >= back:
                    hdr_rows = recent[-back:] + [cells]
                    h = is_header(merge_rows(hdr_rows))
                    if h:
                        break
        if not any(to_number(c) for c in cells):
            recent = (recent + [cells])[-2:]
        else:
            recent = []
        if h:
            s, c, v = h
            cols = Columns(s, v, c, None)
            j, lab = value_column(hdr_rows, c)
            cols.value, cols.value_label = j, lab
            if j is None or not re.search(r'ZV|SQ|REV|VALUE|FINAL|INITIAL', lab, re.I):
                pending_header = hdr_rows
            street = vicinity = ''
            continue

        if cols is None:
            continue  # memo text before the first table: not data, not logged

        cls_col = cols.cls
        classes = clean_classes(cells[cls_col]) if cls_col < len(cells) else []
        if not classes and not (cls_col < len(cells) and cells[cls_col]) and cols.value is not None:
            # a merged CLASSIFICATION header (D:F) whose codes sit in a later cell of the merge (Batangas 2022)
            # (the cell after the code may hold only a footnote marker: '*' newly identified classification)
            filled = [j for j in range(cls_col + 1, min(cols.value, len(cells))) if cells[j].strip('* ')]
            if len(filled) == 1 and clean_classes(cells[filled[0]]):
                cls_col = filled[0]
                classes = clean_classes(cells[cls_col])
        cls = classes[0] if classes else None
        val = to_number(cells[cols.value]) if cols.value is not None and cols.value < len(cells) else None
        if cls and val is None and cols.value is None:
            # header never named a value column: take the first number right of the class
            val = next((to_number(x) for x in cells[cols.cls + 1:] if to_number(x)), None)
        s_txt = cells[cols.street] if cols.street < len(cells) else ''
        v_txt = cells[cols.vicinity] if cols.vicinity is not None and cols.vicinity < len(cells) else ''
        if cols.vicinity is None:  # no vicinity column: text between street and class is vicinity
            v_txt = ' '.join(x for x in cells[cols.street + 1:cols.cls] if x)

        if cls and val is not None:
            if s_txt:
                street, vicinity = s_txt, v_txt
            elif v_txt:
                vicinity = v_txt
            if not barangay:
                problem(i, 'value row before any barangay label', cells)
                continue
            # the NOTICE effectivity date is authoritative; a block's own date fills in only when it is missing
            eff = parse_date(do_effective) or parse_date(block_date)
            for c in classes:
                res.rows.append(Row(rdo, do_number, eff.isoformat() if isinstance(eff, date) else '',
                                    province, city or default_city, barangay, street, vicinity, c, val, sheet, i + 1))
        elif cls:
            problem(i, f'class {cls} but value not numeric: {cells[cols.value] if cols.value is not None and cols.value < len(cells) else ""!r}', cells)
        elif val is not None:
            raw_cls = cells[cols.cls] if cols.cls < len(cells) else ''
            problem(i, f'value but unrecognised class {raw_cls!r}', cells)
        elif s_txt and not any(cells[cols.cls:]):
            # a street or section name whose values follow on the next rows
            street, vicinity = s_txt, v_txt
            problem(i, 'text only (street or heading carried to following rows)', cells)
        else:
            problem(i, 'text only (note or unrecognised row)', cells)
    return res


def parse_workbook(path, sheets: list[tuple[str, str, str]], rdo: str, default_city: str = '') -> dict[str, SheetResult]:
    """sheets: (sheet name, DO number, DO effectivity date). -> {sheet: SheetResult}"""
    book = Book(path)
    try:
        return {s: parse_sheet(book, s, rdo, do, eff, default_city) for s, do, eff in sheets}
    finally:
        book.close()


__all__ = ['Row', 'Problem', 'SheetResult', 'parse_sheet', 'parse_workbook', 'parse_do', 'RESIDENTIAL_LAND']
