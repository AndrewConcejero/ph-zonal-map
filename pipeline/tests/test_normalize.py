import pytest

from pipeline.bir_values import CityIndex, clean_bir_barangay, keys_for
from pipeline.inventory import canon_do, parse_date, parse_do, rdo_code
from pipeline.normalize import norm_barangay, norm_city


@pytest.mark.parametrize('a,b', [
    ('Sto. Niño', 'Santo Nino'),
    ('STO NIÑO', 'Santo Niño'),
    ('Brgy. 001', 'Barangay 1'),
    ('Bgy. No. 1', 'Barangay 1'),
    ('Sta. Cruz', 'Santa Cruz'),
    ('Gen. T. de Leon', 'General T. De Leon'),
    ('Bagong Silang II', 'Bagong Silang 2'),
    ('Bel-Air', 'Bel Air'),
])
def test_same_barangay(a, b):
    assert norm_barangay(a) == norm_barangay(b)


@pytest.mark.parametrize('a,b', [('Barangay 12', 'Barangay 112'), ('San Isidro', 'San Isidro Labrador')])
def test_different_barangay(a, b):
    assert norm_barangay(a) != norm_barangay(b)


@pytest.mark.parametrize('a,b', [
    ('City of Las Piñas', 'Las Pinas'),
    ('Quezon City', 'QUEZON'),
    ('City of San Jose del Monte', 'San Jose Del Monte'),
    ('Dasmariñas', 'DASMARINAS'),
])
def test_same_city(a, b):
    assert norm_city(a) == norm_city(b)


@pytest.mark.parametrize('raw,city,want', [
    ('Zone 46 / Brgy 471', 'Manila', 'Barangay 471'),
    ('124/10', 'Manila', 'Barangay 124'),
    ('74677', 'Manila', 'Barangay 677'),       # zone 74 + barangay 677, slash lost in Excel
    ('Zone 99 / Brgy. 897*', 'Manila', 'Barangay 897'),
    ('12 ZONE 4', 'Pasay', 'Barangay 12'),
    ('1 (cont.)', 'Caloocan', 'Barangay 1'),
    ('POST PROPER SOUTHSIDE (continuation)', 'Makati', 'POST PROPER SOUTHSIDE'),
    ('*ANIBAN I', 'Bacoor', 'ANIBAN I'),
    ('BEL-AIR (SALCEDO VILLAGE)(continuation)', 'Makati', 'BEL-AIR (SALCEDO VILLAGE)'),
])
def test_clean_bir_barangay(raw, city, want):
    assert clean_bir_barangay(raw, city) == want


def test_bir_name_matching_rules():
    idx = CityIndex({'1': 'Barangay I', '2': 'Barangay II', '3': 'Barangay III', '4': 'Teachers Village West',
                     '5': 'East Kamias', '6': 'Saint Ignatius', '7': 'Francisco De Castro', '8': 'Batasan Matanda',
                     '9': 'Batasan Bata', '10': 'Singkamas', '11': 'Concepcion Uno'})
    assert idx.exact("TEACHER'S VILLAGE (WEST)") == '4'   # punctuation, word order
    assert idx.exact('KAMIAS (EAST)') == '5'
    assert idx.exact('ST. IGNATIUS') == '6'
    assert idx.exact('SINCAMAS') is None                  # a typo is not an exact match...
    assert any(k.startswith('~') for k in keys_for('Singkamas'))  # ...but has a phonetic key for the fuzzy pass
    assert idx.exact('CONCEPCION I') == '11'              # Uno = I
    assert idx.group('POBLACION') == ['1', '2', '3']      # town centre = the numbered barangays
    assert idx.group('POBLACION II') == ['2']
    assert idx.partial('F. DE CASTRO') == '7'
    assert idx.partial('BATASAN (M)') == '8'
    assert idx.partial('BATASAN (B)') == '9'


@pytest.mark.parametrize('raw,want', [
    (44552.0, '2021-12-22'),            # Excel serial
    ('4-June-21', '2021-06-04'),
    ('25-Sept-94', '1994-09-25'),
    ('15-10-2017', '2017-10-15'),       # dashes: day first
    ('9/17/2023', '2023-09-17'),        # slashes: Excel US export, month first
    ('1/8/2022', '2022-01-08'),
    ('Dec. 22, 2021', '2021-12-22'),
    ('2009-07-15 00:00:00', '2009-07-15'),
])
def test_parse_date(raw, want):
    assert parse_date(raw).isoformat() == want


def test_department_order_and_rdo_codes():
    assert parse_do('037-2021') == 'DO 37-2021'
    assert parse_do(' 22-17') == 'DO 22-2017'
    assert parse_do('059 - 2023') == 'DO 59-2023'    # not year "20"
    assert parse_do('Sheet 6') is None
    assert canon_do('8', '97') == 'DO 8-1997'
    assert rdo_code('27', None) == '027'
    assert rdo_code('53', 'a') == '53A'
