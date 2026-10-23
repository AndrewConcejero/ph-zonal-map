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


def test_ranges_ordinals_codes_and_pob_suffix():
    from pipeline.bir_values import expand_range
    assert expand_range('ESCOPA I TO IV') == ['ESCOPA I', 'ESCOPA II', 'ESCOPA III', 'ESCOPA IV']
    assert expand_range('195 to 198 ZONE 20') == ['195 ZONE 20', '196 ZONE 20', '197 ZONE 20', '198 ZONE 20']
    assert expand_range('LIGTONG 1 TO 4')[-1] == 'LIGTONG 4'
    assert expand_range('Zone 88 / Brgy 818-A') == []
    assert expand_range('BARANGAY 10-A') == []
    idx = CityIndex({'1': 'Look 1st', '2': 'Arnaldo Pob.', '3': 'Quinale', '4': 'Dampol II-A', '5': 'Dampol II-B'})
    assert idx.exact('LOOK I') == '1'                      # I = 1st
    assert idx.exact('ARNALDO') == '2'                     # "Pob." suffix optional
    assert idx.exact(clean_bir_barangay('0004 - QUINALE (POBLACION)', 'Paete')) == '3'  # numeric code stripped
    assert idx.group('DAMPOL 2ND A & B') == ['4', '5']


def test_dated_alias_resolution():
    from pipeline.bir_values import resolve_target
    t = {'before': '2023-07-29', 'alias': 'old-area', 'else': 'same-name'}
    assert resolve_target(t, '2022-07-10') == 'old-area'   # a pre-merger schedule uses the old mapping
    assert resolve_target(t, '2024-01-01') == 'same-name'  # a later schedule uses the new names as written
    assert resolve_target('a;b', '2024-01-01') == 'a;b'


def test_aliases_file_parses_ignore_dated_and_other_city():
    from pipeline.bir_values import load_bir_aliases
    al = load_bir_aliases()
    z = al[(norm_city('Bacoor'), norm_barangay('ZAPOTE 2'))]
    assert z.names == ['Zapote 1'] and z.valid_before == '2023-07-29'
    assert al[(norm_city('Pasig'), norm_barangay('ALL BARANGAYS (continuation)'))].ignore
    assert al[(norm_city('San Pedro'), norm_barangay('MALITLIT'))].city == 'Santa Rosa'
    assert al[(norm_city('Kawit'), norm_barangay('BINAKAYAN'))].names == ['Binakayan-Aplaya', 'Binakayan-Kanluran']


def test_batangas_naming_rules():
    idx = CityIndex({'1': 'Poblacion Barangay 6', '2': 'Poblacion Barangay 9-A', '3': 'Muzon Primero',
                     '4': 'Muzon Segundo', '5': 'San Isidro Norte', '6': 'Maria Paz', '7': 'Rillo', '8': 'Barangay 12'})
    assert idx.exact(clean_bir_barangay('BARANGAY 6* (CONT.)', 'Lipa')) == '1'   # PSGC adds "Poblacion"
    assert idx.exact('BARANGAY 9A') == '2'
    assert idx.exact('MUZON II') == '4'                   # Segundo = II
    assert idx.exact('SAN ISIDRO NORTH') == '5'           # Norte = North
    assert idx.exact('MA. PAZ') == '6'
    assert idx.exact('POBLACION RILLO') == '7'            # Tuy: a named town-centre barangay
    assert idx.group('POBLACION XII') == ['8']            # numerals past X
