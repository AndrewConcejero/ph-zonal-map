"""Parser tests against real BIR workbooks in raw/bir/ (six layouts) and small synthetic sheets for edge cases."""
from pathlib import Path

import openpyxl
import pytest

from pipeline.bir_parser import clean_classes, parse_sheet, value_column
from pipeline.inventory import Book

BIR = Path(__file__).resolve().parents[2] / 'raw' / 'bir'


def parse_real(rdo: str, file_part: str, sheet_part: str, do='DO X', eff='2020-01-01', default_city=''):
    path = next(p for p in (BIR / rdo).iterdir() if file_part in p.name)
    book = Book(path)
    try:
        sheet = next(n for n in book.names if sheet_part in n)
        return parse_sheet(book, sheet, rdo, do, eff, default_city)
    finally:
        book.close()


def find(res, **kw):
    return [r for r in res.rows if all(getattr(r, k) == v for k, v in kw.items())]


needs_raw = pytest.mark.skipif(not (BIR / 'RDO-047').exists(), reason='raw BIR workbooks not present')


# ---------------------------------------------------------------- real layouts

@needs_raw
def test_xls_standard_layout_makati():
    """Labels in one cell ('BARANGAY: X'), 4 columns, condo section header mid-block."""
    res = parse_real('RDO-047', 'East Makati', 'DO 37-2021')
    rows = find(res, street_or_building='EAST DRIVE', class_code='CR')
    assert rows and rows[0].value_per_sqm == 750000
    assert rows[0].city == 'MAKATI CITY'
    assert rows[0].barangay.startswith('SAN LORENZO')
    assert rows[0].vicinity == 'AYALA CENTER'


@needs_raw
def test_xlsx_split_labels_and_carried_street_taguig():
    """xlsx; labels split across cells ('PROVINCE' | ': NCR'); class/value rows under a street leave it blank."""
    res = parse_real('RDO-044', 'Taguig', 'DO 005-24')
    first = find(res, street_or_building='1st AVENUE', barangay='BAGUMBAYAN')
    assert {(r.class_code, r.value_per_sqm) for r in first} >= {('CR', 41000), ('I', 38000)}
    assert first[0].city == 'TAGUIG'


@needs_raw
def test_spaced_header_two_value_columns_binondo():
    """'S T R E E T  N A M E' header; 4th (previous) and 5th revision side by side: take the 5th."""
    res = parse_real('RDO-030', 'Binondo', 'DO 62-17')
    rows = find(res, street_or_building='DASMARINAS', class_code='CR')
    assert rows and rows[0].value_per_sqm == 132400
    assert rows[0].city == 'Binondo, Manila'
    assert rows[0].barangay == 'Barangay 287'


@needs_raw
def test_header_split_across_rows_marikina():
    """'CLASSI-' on one row and 'FICATION' on the next; '5TH REV.' above 'ZV/SQ.M.'."""
    res = parse_real('RDO-045', 'Marikina', 'DO 28-2017')
    rows = find(res, street_or_building='A BONIFACIO AVE')
    assert {(r.class_code, r.value_per_sqm) for r in rows} >= {('CR', 8350), ('RR', 7050), ('I', 7750)}
    assert rows[0].barangay == 'BARANGKA'


@needs_raw
def test_shifted_labels_manila():
    """'City/Municipality | 236/22' then a bare 'Barangay/Zone': the zone value belongs to the barangay."""
    res = parse_real('RDO-029', 'Tondo', 'DO 27-19')
    rows = [r for r in res.rows if r.row == 5668]
    assert rows and rows[0].barangay == '236/22'
    assert rows[0].street_or_building == 'ANTONIO RIVERA' and rows[0].value_per_sqm == 40000


@needs_raw
def test_many_value_columns_takes_final_bulacan():
    """Previous / recommended (BIR, appraiser, assessor) / proposed / final: only the final column counts."""
    res = parse_real('RDO-25B', 'Sta. Maria', 'DO 14-18')
    rows = find(res, barangay='BAYBAY', street_or_building='PROVINCIAL ROAD', class_code='RR')
    assert rows and rows[0].value_per_sqm == 950
    assert rows[0].city == 'ANGAT'
    a15 = find(res, barangay='BAYBAY', class_code='A15')
    assert a15[0].value_per_sqm == 100  # proposed was 95


@needs_raw
def test_masked_values_are_logged_not_dropped():
    """The Ayala Center block of DO 23-2012 shows '*' for every value (the commercial centre is valued separately)."""
    res = parse_real('RDO-047', 'East Makati', 'DO 23-12')
    assert not [r for r in res.rows if r.barangay.startswith('AYALA CENTER')]
    logged = [p for p in res.problems if p.cells.startswith('EAST DRIVE | CR | *')]
    assert logged and 'not numeric' in logged[0].reason


# ---------------------------------------------------------------- synthetic edge cases

def sheet(tmp_path: Path, rows: list[list]) -> Book:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'DO 1-2024'
    for r in rows:
        ws.append(r)
    path = tmp_path / 'fixture.xlsx'
    wb.save(path)
    return Book(path)


MEMO = [['Republic of the Philippines'], ['DEPARTMENT ORDER NO. 001-2024'], ['SUBJECT: ZONAL VALUES']]


def test_compound_class_numbers_as_text_and_logging(tmp_path):
    book = sheet(tmp_path, MEMO + [
        ['PROVINCE: NCR'],
        ['CITY/MUNICIPALITY: PASIG CITY', None, 'D.O. NO.', '001-2024'],
        ['BARANGAY: KAPITOLYO', None, 'Effectivity Date', 'Jan. 5, 2024'],
        ['STREET/SUBDIVISION', 'VICINITY', 'CLASSIFICATION', '9TH REV ZV/SQM'],
        ['UNITED ST.', 'SHAW - PIONEER', 'RR/CR', '1,250'],
        [None, None, 'I', '***'],
        ['ALL OTHER STREETS', None, 'RR*', 900],
        ['* see note'],
    ])
    res = parse_sheet(book, 'DO 1-2024', 'RDO-043', 'DO 1-2024', '2024-01-05')
    got = {(r.street_or_building, r.class_code, r.value_per_sqm) for r in res.rows}
    assert got == {('UNITED ST.', 'RR', 1250), ('UNITED ST.', 'CR', 1250), ('ALL OTHER STREETS', 'RR', 900)}
    assert all(r.barangay == 'KAPITOLYO' and r.city == 'PASIG CITY' for r in res.rows)
    reasons = [p.reason for p in res.problems]
    assert any('not numeric' in r for r in reasons)  # the '***' row
    assert any(r.startswith('text only') for r in reasons)  # the footnote


def test_street_name_like_barangay_label_is_data(tmp_path):
    """A street called 'BARANGAY ROAD' must not be read as a new barangay label."""
    book = sheet(tmp_path, MEMO + [
        ['CITY/MUNICIPALITY', ': NAIC'],
        ['BARANGAY', ': SABANG'],
        ['STREET NAME', 'VICINITY', 'CLASS', '4TH REV ZV/SQM'],
        ['BARANGAY ROAD', None, 'RR', 2500],
        [None, None, 'CR', 3000],
    ])
    res = parse_sheet(book, 'DO 1-2024', 'RDO-54B', 'DO 1-2024', '2024-01-05')
    assert {(r.barangay, r.street_or_building, r.class_code) for r in res.rows} == {
        ('SABANG', 'BARANGAY ROAD', 'RR'), ('SABANG', 'BARANGAY ROAD', 'CR')}


def test_value_column_prefers_final_then_highest_revision():
    hdr = [['STREET', 'VICINITY', 'CLASS', '4TH REVISION', 'RECOMMENDED VALUE', '5TH REVISION']]
    assert value_column(hdr, 2)[0] == 5
    hdr = [['STREET', 'CLASS', '6TH REV', 'FINAL']]
    assert value_column(hdr, 1)[0] == 3


def test_clean_classes():
    assert clean_classes('RR') == ['RR']
    assert clean_classes('RR/CR') == ['RR', 'CR']
    assert clean_classes('A50*') == ['A50']
    assert clean_classes('ALL OTHER STREETS') == []
