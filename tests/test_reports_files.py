"""Report files: CSV, XLSX and JSON, and text that a spreadsheet would run as a formula."""

import csv
import io
import json
import re
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from sympraxi.reports import tables
from sympraxi.reports.tables import Column, Report, Table, cell, safe_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("=1+1", "'=1+1"),
        ('=HYPERLINK("https://x.test","click")', '\'=HYPERLINK("https://x.test","click")'),
        ("+1 555 0100", "'+1 555 0100"),
        ("-2", "'-2"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tTab", "'\tTab"),
        ("\rReturn", "'\rReturn"),
        ("Ana", "Ana"),
        ("a=b", "a=b"),
        ("", ""),
    ],
)
def test_text_a_spreadsheet_would_run_gets_an_apostrophe(text, expected) -> None:
    assert safe_text(text) == expected


def test_numbers_stay_numbers() -> None:
    assert cell(-3) == -3
    assert cell(Decimal("-1.5")) == Decimal("-1.5")
    assert cell(None) == ""
    assert cell(True) == "yes"
    assert cell(date(2025, 9, 1)) == "2025-09-01"


def report() -> Report:
    return Report(
        kind="example",
        title="Example",
        described={"Period": "Fall 2025", "Schedules": "=cmd"},
        tables=[
            Table(
                "people",
                "People",
                [Column("name", "Name"), Column("visits", "Visits", numeric=True)],
                [['=WEBSERVICE("https://x.test")', 3], ["Zoë", -1]],
            ),
            Table("hours", "Hours", [Column("week", "Week of")], [[date(2025, 9, 1)]], note="n"),
        ],
        meta={"from": date(2025, 9, 1)},
    )


def test_csv_stacks_tables_and_guards_text() -> None:
    body = tables.to_csv(report())
    assert body.startswith("\ufeff".encode())
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert rows == [
        ["People"],
        ["Name", "Visits"],
        ['\'=WEBSERVICE("https://x.test")', "3"],
        ["Zoë", "-1"],
        [],
        ["Hours"],
        ["Week of"],
        ["2025-09-01"],
    ]


def test_csv_with_one_table_has_no_title_row() -> None:
    single = report()
    single.tables = single.tables[:1]
    rows = list(csv.reader(io.StringIO(tables.to_csv(single).decode("utf-8-sig"))))
    assert rows[0] == ["Name", "Visits"]


def test_json_keeps_values_as_they_are() -> None:
    data = json.loads(tables.to_json(report()))
    assert data["format"] == "sympraxi.report.example"
    assert data["from"] == "2025-09-01"
    assert data["filters"] == {"Period": "Fall 2025", "Schedules": "=cmd"}
    people, hours = data["tables"]
    assert people["rows"] == [
        {"name": '=WEBSERVICE("https://x.test")', "visits": 3},
        {"name": "Zoë", "visits": -1},
    ]
    assert hours["rows"] == [{"week": "2025-09-01"}]
    assert hours["note"] == "n"


def test_xlsx_has_a_sheet_per_table_and_no_formulas() -> None:
    body = tables.to_xlsx(report())
    with zipfile.ZipFile(io.BytesIO(body)) as book:
        names = book.namelist()
        workbook = book.read("xl/workbook.xml").decode()
        sheets = [book.read(n).decode() for n in names if n.startswith("xl/worksheets/sheet")]
        strings = book.read("xl/sharedStrings.xml").decode()
    assert re.findall(r'<sheet name="([^"]+)"', workbook) == ["About", "People", "Hours"]
    assert not any("<f>" in s for s in sheets), "No cell may hold a formula"
    assert "&apos;=WEBSERVICE" in strings or "'=WEBSERVICE" in strings
    assert "'=cmd" in strings or "&apos;=cmd" in strings
    # Numbers are numbers: -1 is stored as a value, not as text.
    assert any("<v>-1</v>" in s for s in sheets)


def test_sheet_names_fit_excels_rules() -> None:
    used: set[str] = set()
    assert tables._sheet_name("By tutor: hours/week [all]", used) == "By tutor  hours week  all"
    assert tables._sheet_name("x" * 40, used) == "x" * 31
    assert tables._sheet_name("x" * 40, used) == "x" * 27 + " (2)"
