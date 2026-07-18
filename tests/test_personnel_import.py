from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook

from bot.personnel_import import parse_personnel_workbook


def _xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_no_header_name_extension_20_rows():
    """dis as.xlsx formati: baslik yok, 20 personel."""
    rows = [
        ["sadik", 569],
        ["adem", 583],
        ["selen", 585],
        ["elcin", 586],
        ["turkan", 591],
        ["doga", 595],
        ["burcin", 599],
        ["asu", 605],
        ["selcuk", 608],
        ["asya", 614],
        ["seda", 622],
        ["sergen", 623],
        ["asel", 630],
        ["berkan", 631],
        ["celal", 632],
        ["ayaz", 634],
        ["dilara", 635],
        ["ruya", 636],
        ["serdar", 637],
        ["umit", 639],
    ]
    imported = parse_personnel_workbook(_xlsx(rows))
    assert len(imported) == 20
    assert imported[0].name == "sadik"
    assert imported[0].extension == "569"
    assert imported[-1].name == "umit"
    assert imported[-1].extension == "639"


def test_header_personel_ismi_dahili_ad():
    rows = [
        ["DAHİLİ AD", "PERSONEL İSMİ"],
        [632, "celal"],
        [591, "turkan"],
        [569, "sadik"],
    ]
    imported = parse_personnel_workbook(_xlsx(rows))
    assert len(imported) == 3
    by_name = {p.name: p.extension for p in imported}
    assert by_name["celal"] == "632"
    assert by_name["sadik"] == "569"
    assert by_name["turkan"] == "591"


def test_header_ad_dahili():
    rows = [["Ad", "Dahili"], ["Ali Veli", "101"], ["Ayse", "202"]]
    imported = parse_personnel_workbook(_xlsx(rows))
    assert len(imported) == 2
    assert imported[0].name == "Ali Veli"
    assert imported[0].extension == "101"


def test_skip_duplicate_names():
    rows = [["Ali", "1"], ["Ali", "2"], ["Veli", "3"]]
    imported = parse_personnel_workbook(_xlsx(rows))
    assert len(imported) == 2
