"""Show report tables on the page: one value per cell, in the center's time zone."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django import template
from django.utils import timezone
from django.utils.formats import date_format, number_format
from django.utils.translation import gettext as _

from sympraxi.reports.tables import Column, Table

register = template.Library()


@register.filter
def cells(table: Table) -> list[list[tuple[Any, Column, bool]]]:
    """Each row as (value, its column, whether it is the row's header) for every column."""
    return [
        [
            (value, column, i == table.row_header)
            for i, (column, value) in enumerate(zip(table.columns, row, strict=True))
        ]
        for row in table.rows
    ]


@register.filter
def shown(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return _("Yes") if value else _("No")
    if isinstance(value, datetime):
        return date_format(timezone.localtime(value), "SHORT_DATETIME_FORMAT")
    if isinstance(value, date):
        return date_format(value, "M j, Y")
    if isinstance(value, Decimal | int | float):
        return number_format(value, use_l10n=True)
    return str(value)
