from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.centers.models import STAFF_ROLES, Center
from sympraxi.scheduling.forms import DateInput, TimeInput
from sympraxi.scheduling.timeutils import first_instant
from sympraxi.staff import timeclock
from sympraxi.staff.models import Category, PayrollDetail, TimeEntry

# The longest a single entry may be, so a typo in a time can't add a day of pay.
LONGEST = timedelta(hours=16)
# The longest stretch a timesheet covers.
MAX_DAYS = 366
# Without dates, a timesheet covers the last two weeks.
DEFAULT_DAYS = 14


class ClockInForm(forms.Form):
    category = forms.ChoiceField(
        label=gettext_lazy("What you're working on"),
        choices=Category.choices,
        initial=Category.TUTORING,
    )
    note = forms.CharField(label=gettext_lazy("Note"), max_length=200, required=False)


def staff_of(center: Center) -> list[User]:
    return list(
        User.objects.filter(
            memberships__center=center,
            memberships__is_active=True,
            memberships__role__in=STAFF_ROLES,
            is_active=True,
        )
        .distinct()
        .order_by("full_name", "email")
    )


class EntryForm(forms.Form):
    """A director's entry: whose, what for, the day and times, and why."""

    person = forms.ChoiceField(label=gettext_lazy("Who"))
    category = forms.ChoiceField(label=gettext_lazy("What for"), choices=Category.choices)
    day = forms.DateField(label=gettext_lazy("Day"), widget=DateInput())
    start = forms.TimeField(label=gettext_lazy("Started"), widget=TimeInput())
    end = forms.TimeField(
        label=gettext_lazy("Ended"),
        widget=TimeInput(),
        help_text=gettext_lazy("A time earlier than the start means the next day."),
    )
    note = forms.CharField(label=gettext_lazy("Note"), max_length=200, required=False)
    reason = forms.CharField(
        label=gettext_lazy("Why you're making this change"),
        max_length=200,
        help_text=gettext_lazy("Kept in the audit log with the change."),
    )

    def __init__(
        self, *args: Any, center: Center, entry: TimeEntry | None = None, **kwargs: Any
    ) -> None:
        self.center = center
        self.entry = entry
        self.tz = ZoneInfo(center.time_zone)
        if entry is not None and "initial" not in kwargs:
            start = entry.started_at.astimezone(self.tz)
            kwargs["initial"] = {
                "category": entry.category,
                "day": start.date(),
                "start": start.time().replace(second=0, microsecond=0),
                "end": entry.ended_at.astimezone(self.tz).time().replace(second=0, microsecond=0)
                if entry.ended_at
                else None,
                "note": entry.note,
            }
        super().__init__(*args, **kwargs)
        if entry is None:
            self.people = {str(u.pk): u for u in staff_of(center)}
            cast(forms.ChoiceField, self.fields["person"]).choices = [
                ("", _("Choose someone")),
                *[(pk, u.display_name) for pk, u in self.people.items()],
            ]
        else:
            # An entry stays with its person; it can be removed and added again.
            del self.fields["person"]
            if entry.ended_at is None:
                self.fields["end"].required = False
                self.fields["end"].help_text = _(
                    "Leave empty while they're still clocked in. A time earlier than the "
                    "start means the next day."
                )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        day: date | None = data.get("day")
        start = data.get("start")
        if day is None or start is None:
            return data
        started_at = first_instant(day, start, self.tz)
        ended_at: datetime | None = None
        if data.get("end") is not None:
            end_day = day if data["end"] > start else day + timedelta(days=1)
            ended_at = first_instant(end_day, data["end"], self.tz)
            if ended_at - started_at > LONGEST:
                self.add_error("end", _("An entry can't be longer than 16 hours."))
        now = timezone.now()
        if started_at > now or (ended_at is not None and ended_at > now + timedelta(minutes=5)):
            self.add_error("day", _("Time can't be entered before it has been worked."))
        if self.entry is None:
            person = self.people.get(data.get("person") or "")
            if person is None:
                self.add_error("person", _("Choose someone on the center's staff."))
            data["person_user"] = person
        data["started_at"] = started_at
        data["ended_at"] = ended_at
        return data


class RemoveForm(forms.Form):
    reason = forms.CharField(
        label=gettext_lazy("Why you're removing it"),
        max_length=200,
        help_text=gettext_lazy("Kept in the audit log with what the entry said."),
    )


class TimesheetForm(forms.Form):
    first = forms.DateField(label=gettext_lazy("From"), required=False, widget=DateInput())
    last = forms.DateField(label=gettext_lazy("To"), required=False, widget=DateInput())

    def __init__(self, *args: Any, today: date, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.today = today

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        first, last = data.get("first"), data.get("last")
        if (first is None) != (last is None):
            raise forms.ValidationError(_("Give both a first and a last day, or neither."))
        if first is not None and last is not None:
            if last < first:
                self.add_error("last", _("The last day can't be before the first day."))
            elif (last - first).days + 1 > MAX_DAYS:
                self.add_error("last", _("Choose a year or less."))
        return data

    def period(self) -> tuple[date, date]:
        data = self.cleaned_data if self.is_bound and self.is_valid() else {}
        if data.get("first") and data.get("last"):
            return data["first"], data["last"]
        return self.today - timedelta(days=DEFAULT_DAYS - 1), self.today


class NetworksForm(forms.Form):
    clock_networks = forms.CharField(
        label=gettext_lazy("Networks staff may clock in from"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=gettext_lazy(
            "One IP address or range per line, like 203.0.113.0/24. Leave empty to let staff "
            "clock in from anywhere. Directors can always clock in."
        ),
    )

    def clean_clock_networks(self) -> str:
        text = self.cleaned_data["clock_networks"]
        try:
            timeclock.parse_networks(text)
        except ValueError as bad:
            raise forms.ValidationError(
                _("%(line)s isn't an IP address or a range like 203.0.113.0/24.")
                % {"line": bad.args[0]}
            ) from bad
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())


class PayrollForm(forms.Form):
    """A payroll ID and pay code for each person on staff, as paired fields."""

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.people = staff_of(center)
        details = {d.person_id: d for d in PayrollDetail.objects.filter(center=center)}
        for person in self.people:
            detail = details.get(person.pk)
            self.fields[f"id_{person.pk}"] = forms.CharField(
                label=_("Payroll ID for %(name)s") % {"name": person.display_name},
                max_length=40,
                required=False,
                initial=detail.payroll_id if detail else "",
            )
            self.fields[f"code_{person.pk}"] = forms.CharField(
                label=_("Pay code for %(name)s") % {"name": person.display_name},
                max_length=40,
                required=False,
                initial=detail.pay_code if detail else "",
            )

    def rows(self) -> list[tuple[User, Any, Any]]:
        return [(p, self[f"id_{p.pk}"], self[f"code_{p.pk}"]) for p in self.people]


class ApprovalForm(forms.Form):
    """Which person's hours, for the days on the page."""

    person = forms.UUIDField()
    first = forms.DateField()
    last = forms.DateField()


class WithdrawForm(forms.Form):
    approval = forms.UUIDField()
    first = forms.DateField(required=False)
    last = forms.DateField(required=False)
    reason = forms.CharField(
        label=gettext_lazy("Why you're withdrawing it"),
        max_length=300,
        help_text=gettext_lazy("Kept in the audit log."),
    )
