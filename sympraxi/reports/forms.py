"""The filters at the top of each report page. All are sent with GET, so a report's
address says exactly what it shows and its download links can repeat it."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from django import forms
from django.utils.formats import date_format
from django.utils.translation import gettext, gettext_lazy
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center
from sympraxi.reports.models import ResearchSettings
from sympraxi.scheduling.forms import DateInput
from sympraxi.scheduling.models import Schedule, Term
from sympraxi.scheduling.timeutils import Interval, date_range_bounds, week_start

# The longest stretch a report covers, so a typo in a year can't make one
# report walk ten years of hours.
MAX_DAYS = 366
# Without a term, reports cover this many days up to today.
DEFAULT_DAYS = 30


class ResearchSettingsForm(forms.ModelForm):
    """The IRB protocol the research export is made under."""

    class Meta:
        model = ResearchSettings
        fields = ["protocol_number", "investigator", "approved_on", "expires_on"]
        widgets = {
            "approved_on": forms.DateInput(attrs={"type": "date"}),
            "expires_on": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        first, last = data.get("approved_on"), data.get("expires_on")
        if first and last and last < first:
            self.add_error("expires_on", gettext("The approval can't end before it starts."))
        if data.get("protocol_number", "").strip() and not (first and last):
            self.add_error(None, gettext("Give both dates the protocol is approved for."))
        return data


class ReportQuestionsForm(forms.Form):
    """Choose the questions that give class standing and course."""

    standing = forms.ChoiceField(
        label=gettext_lazy("Class standing"),
        required=False,
        help_text=gettext_lazy(
            "For example: first-year, sophomore. Choose a short-answer or choose-one question."
        ),
    )
    course = forms.ChoiceField(
        label=gettext_lazy("Course"),
        required=False,
        help_text=gettext_lazy("The course the student came about, asked at booking."),
    )

    def __init__(self, *args: Any, choices: list[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        options = [("", gettext_lazy("None chosen")), *choices]
        for name in ("standing", "course"):
            cast(forms.ChoiceField, self.fields[name]).choices = options


@dataclass
class Period:
    """The days a report covers, in the center's time zone, and on which schedules."""

    first: date
    last: date
    term: Term | None
    schedule: Schedule | None

    @property
    def label(self) -> str:
        days = _("%(first)s to %(last)s") % {
            "first": date_format(self.first, "F j, Y"),
            "last": date_format(self.last, "F j, Y"),
        }
        if self.term is not None:
            return f"{self.term.name} ({days})"
        return days

    @property
    def schedule_label(self) -> str:
        return self.schedule.name if self.schedule is not None else _("Every schedule")

    def bounds(self, center: Center) -> Interval:
        return date_range_bounds(self.first, self.last, ZoneInfo(center.time_zone))

    def described(self) -> dict[str, str]:
        return {_("Period"): self.label, _("Schedules"): self.schedule_label}

    def meta(self) -> dict[str, Any]:
        return {
            "from": self.first,
            "to": self.last,
            "term": self.term.name if self.term else None,
            "schedule": self.schedule.name if self.schedule else None,
        }


def _schedule_choices(center: Center) -> list[tuple[str, str]]:
    schedules = Schedule.objects.filter(center=center).select_related("term")
    return [("", _("Every schedule"))] + [
        (str(s.pk), f"{s.name} ({s.term.name})") for s in schedules.order_by("-starts_on", "name")
    ]


class PeriodForm(forms.Form):
    """A term, or a first and last day, and one schedule or all of them."""

    term = forms.ChoiceField(label=gettext_lazy("Term"), required=False)
    first = forms.DateField(
        label=gettext_lazy("From"),
        required=False,
        widget=DateInput(),
        help_text=gettext_lazy("Dates, if given, are used instead of the term."),
    )
    last = forms.DateField(label=gettext_lazy("To"), required=False, widget=DateInput())
    schedule = forms.ChoiceField(label=gettext_lazy("Schedule"), required=False)

    def __init__(self, *args: Any, center: Center, today: date, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.center = center
        self.today = today
        self.terms = list(Term.objects.filter(center=center).order_by("-starts_on", "name"))
        term_field = cast(forms.ChoiceField, self.fields["term"])
        term_field.choices = [("", _("Choose a term"))] + [(str(t.pk), t.name) for t in self.terms]
        cast(forms.ChoiceField, self.fields["schedule"]).choices = _schedule_choices(center)

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

    def period(self) -> Period:
        """What the form asks for, or the current term when it asks for nothing valid."""
        data = self.cleaned_data if self.is_bound and self.is_valid() else {}
        chosen = None
        if data.get("schedule"):
            chosen = Schedule.objects.filter(center=self.center, pk=data["schedule"]).first()
        if data.get("first") and data.get("last"):
            return Period(data["first"], data["last"], None, chosen)
        term = next((t for t in self.terms if str(t.pk) == data.get("term")), None)
        term = term or current_term(self.terms, self.today)
        if term is not None:
            return Period(term.starts_on, term.ends_on, term, chosen)
        return Period(self.today - timedelta(days=DEFAULT_DAYS - 1), self.today, None, chosen)

    def initial_for(self, period: Period) -> None:
        """Show the period actually used, so a page opened without filters says what it is."""
        if not self.is_bound:
            self.initial = {
                "term": str(period.term.pk) if period.term else "",
                "schedule": str(period.schedule.pk) if period.schedule else "",
            }


def current_term(terms: list[Term], today: date) -> Term | None:
    """The latest term that has started, or else the first one to come."""
    started = [t for t in terms if t.starts_on <= today]
    if started:
        return max(started, key=lambda t: t.starts_on)
    return min(terms, key=lambda t: t.starts_on) if terms else None


SPAN_CHOICES = [
    ("day", gettext_lazy("One day")),
    ("week", gettext_lazy("The week, Monday to Sunday")),
]


class AppointmentListForm(forms.Form):
    """A day or a week, and one schedule or all of them."""

    date = forms.DateField(label=gettext_lazy("Date"), widget=DateInput())
    span = forms.ChoiceField(label=gettext_lazy("Show"), choices=SPAN_CHOICES, initial="day")
    schedule = forms.ChoiceField(label=gettext_lazy("Schedule"), required=False)
    canceled = forms.BooleanField(
        label=gettext_lazy("Include canceled appointments"), required=False
    )

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.center = center
        cast(forms.ChoiceField, self.fields["schedule"]).choices = _schedule_choices(center)


@dataclass
class ListChoice:
    """The appointment list's choices, resolved."""

    first: date
    last: date
    schedule: Schedule | None
    include_canceled: bool

    @property
    def is_week(self) -> bool:
        return self.first != self.last

    @property
    def label(self) -> str:
        if not self.is_week:
            return date_format(self.first, "l, F j, Y")
        return _("Week of %(day)s") % {"day": date_format(self.first, "F j, Y")}

    def described(self) -> dict[str, str]:
        return {
            _("Days"): self.label,
            _("Schedules"): self.schedule.name if self.schedule else _("Every schedule"),
            _("Canceled appointments"): _("included") if self.include_canceled else _("left out"),
        }


def list_choice(form: AppointmentListForm, today: date) -> ListChoice:
    data = form.cleaned_data if form.is_bound and form.is_valid() else {}
    day = data.get("date") or today
    schedule = None
    if data.get("schedule"):
        schedule = Schedule.objects.filter(center=form.center, pk=data["schedule"]).first()
    if data.get("span") == "week":
        first = week_start(day)
        return ListChoice(first, first + timedelta(days=6), schedule, bool(data.get("canceled")))
    return ListChoice(day, day, schedule, bool(data.get("canceled")))
