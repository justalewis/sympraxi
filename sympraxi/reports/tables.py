"""Reports as tables, and the files they download as: CSV, XLSX and JSON.

Every report builds a list of `Table`s. The page draws them, and the same tables
download in each format, so a file never says more or less than the page.

Spreadsheet formulas. A cell whose text starts with `=`, `+`, `-` or `@` (or a
tab or carriage return) is read as a formula by Excel, LibreOffice and Google
Sheets when a CSV is opened, and a name or answer typed by a student could be
`=HYPERLINK(...)`. Text cells in CSV and XLSX files get a leading apostrophe in
that case, as OWASP recommends, so the spreadsheet shows the text and runs
nothing. Numbers stay numbers, negative ones included. The XLSX writer also
never turns text into formulas or links on its own. JSON keeps every value as
it is: it is read by programs, not opened in a spreadsheet.
"""

import csv
import io
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import xlsxwriter

# Characters that make a spreadsheet read a cell as a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

FORMATS = ("csv", "xlsx", "json")

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json; charset=utf-8",
}


@dataclass
class Column:
    key: str
    label: str
    # Numbers are right-aligned on the page and written as numbers in files.
    numeric: bool = False


@dataclass
class Table:
    """One table of a report. `name` is its key in JSON and its sheet name in XLSX."""

    name: str
    title: str
    columns: list[Column]
    rows: list[list[Any]] = field(default_factory=list)
    # The column whose cells are row headers (`<th scope="row">`) on the page.
    row_header: int = 0
    # A short note shown under the table and kept in the files.
    note: str = ""

    def records(self) -> list[dict[str, Any]]:
        return [
            {c.key: value for c, value in zip(self.columns, row, strict=True)} for row in self.rows
        ]


@dataclass
class Report:
    """What a report page shows and downloads: its name, the period, and its tables."""

    kind: str
    title: str
    # Filters that produced it, in words and as values: {"period": "Fall 2026", ...}.
    described: dict[str, str]
    tables: list[Table]
    # Extra values for JSON only, such as {"from": "2026-09-01"}.
    meta: dict[str, Any] = field(default_factory=dict)

    def filename(self, fmt: str) -> str:
        return f"{self.kind}.{fmt}"


def safe_text(value: str) -> str:
    """Text a spreadsheet won't run as a formula."""
    if value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


def cell(value: Any) -> Any:
    """A value as it goes into a CSV or XLSX cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int | float | Decimal):
        return value
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    return safe_text(str(value))


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, bool | int | float | str | list | dict):
        return value
    return str(value)


# --- Writers ----------------------------------------------------------------------


def write_csv_rows(out: io.TextIOBase | Any, rows: Iterable[Sequence[Any]]) -> None:
    writer = csv.writer(out)
    for row in rows:
        writer.writerow([cell(v) for v in row])


def to_csv(report: Report) -> bytes:
    """One CSV. A report with several tables stacks them, each under its title,
    with an empty row between, the way spreadsheets show sections."""
    out = io.StringIO()
    stacked = len(report.tables) > 1
    rows: list[Sequence[Any]] = []
    for i, table in enumerate(report.tables):
        if stacked:
            if i:
                rows.append([])
            rows.append([table.title])
        rows.append([c.label for c in table.columns])
        rows.extend(table.rows)
    write_csv_rows(out, rows)
    # The byte order mark tells Excel the file is UTF-8, so accented names survive.
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def to_json(report: Report) -> bytes:
    data = {
        "format": f"sympraxi.report.{report.kind}",
        "version": 1,
        "title": report.title,
        "filters": report.described,
        **{k: json_value(v) for k, v in report.meta.items()},
        "tables": [
            {
                "name": t.name,
                "title": t.title,
                "note": t.note,
                "columns": [{"key": c.key, "label": c.label} for c in t.columns],
                "rows": [
                    {c.key: json_value(v) for c, v in zip(t.columns, row, strict=True)}
                    for row in t.rows
                ],
            }
            for t in report.tables
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def to_xlsx(report: Report) -> bytes:
    """One sheet per table, with the filters on a first sheet."""
    out = io.BytesIO()
    book = xlsxwriter.Workbook(
        out,
        {
            "in_memory": True,
            # Text is text: never a formula, a link or a number.
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "strings_to_numbers": False,
        },
    )
    bold = book.add_format({"bold": True})
    about = book.add_worksheet("About")
    about.write_string(0, 0, safe_text(report.title), bold)
    for line, (label, value) in enumerate(report.described.items(), start=2):
        about.write_string(line, 0, safe_text(label))
        about.write_string(line, 1, safe_text(value))
    used: set[str] = {"About"}
    for table in report.tables:
        sheet = book.add_worksheet(_sheet_name(table.title, used))
        for col, column in enumerate(table.columns):
            sheet.write_string(0, col, safe_text(column.label), bold)
        for r, row in enumerate(table.rows, start=1):
            for col, value in enumerate(row):
                _write_cell(sheet, r, col, cell(value))
        if table.note:
            sheet.write_string(len(table.rows) + 2, 0, safe_text(table.note))
        sheet.freeze_panes(1, 0)
    book.close()
    return out.getvalue()


def _write_cell(sheet: Any, row: int, col: int, value: Any) -> None:
    if isinstance(value, int | float | Decimal):
        sheet.write_number(row, col, float(value))
    else:
        sheet.write_string(row, col, value)


def _sheet_name(title: str, used: set[str]) -> str:
    """Excel's limits: 31 characters, none of []:*?/\\, and unique."""
    base = "".join(" " if ch in "[]:*?/\\" else ch for ch in title).strip()[:31] or "Sheet"
    name, n = base, 2
    while name.lower() in {u.lower() for u in used}:
        suffix = f" ({n})"
        name = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(name)
    return name


WRITERS = {"csv": to_csv, "xlsx": to_xlsx, "json": to_json}


def render(report: Report, fmt: str) -> bytes:
    return WRITERS[fmt](report)
