import datetime as dt
from datetime import UTC, datetime
from typing import Any, cast

from django import forms
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.scheduling.models import (
    REMINDER_CHOICES,
    Appointment,
    AppointmentStatus,
    Attendance,
    AvailabilityException,
    Closure,
    ExceptionKind,
    Exemption,
    Modality,
    Room,
    Schedule,
    Term,
    TutorAssignment,
    WeeklyHours,
)

DATETIME_LOCAL = "%Y-%m-%dT%H:%M"


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        super().__init__(attrs, format="%Y-%m-%d")


class TimeInput(forms.TimeInput):
    input_type = "time"

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        super().__init__(attrs, format="%H:%M")


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        super().__init__(attrs, format=DATETIME_LOCAL)


def students_of(center: Center) -> QuerySet[User]:
    return User.objects.filter(
        memberships__center=center,
        memberships__role=Role.STUDENT,
        memberships__is_active=True,
        is_active=True,
    ).order_by("full_name", "email")


def tutors_of(center: Center) -> QuerySet[User]:
    return User.objects.filter(
        memberships__center=center,
        memberships__role=Role.TUTOR,
        memberships__is_active=True,
        is_active=True,
    ).order_by("full_name", "email")


def choice_field(form: forms.BaseForm, name: str) -> forms.ModelChoiceField:
    field = form.fields[name]
    if not isinstance(field, forms.ModelChoiceField):
        raise TypeError(f"{name} is not a model choice field")
    return field


class PersonChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: Any) -> str:
        return f"{obj.display_name} ({obj.email})"


def format_start(instant: datetime) -> str:
    """A compact UTC timestamp for links: 2026-11-02T15:00:00Z."""
    return instant.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- Booking ------------------------------------------------------------------


class OpenTimesForm(forms.Form):
    """The filters on the booking page. Read from the query string."""

    date = forms.DateField(label=_("Date"), widget=DateInput())
    minutes = forms.TypedChoiceField(label=_("Length"), coerce=int)
    tutor = forms.ChoiceField(label=_("Tutor"), required=False)
    modality = forms.ChoiceField(
        label=_("How"),
        required=False,
        choices=[("", _("Any way")), *Modality.choices],
    )

    def __init__(
        self,
        *args: Any,
        lengths: list[int],
        assignments: list[TutorAssignment],
        first: dt.date,
        last: dt.date,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.first, self.last = first, last
        cast(forms.ChoiceField, self.fields["minutes"]).choices = [
            (n, _("%(n)s minutes") % {"n": n}) for n in lengths
        ]
        cast(forms.ChoiceField, self.fields["tutor"]).choices = [("", _("Any tutor"))] + [
            (str(a.pk), a.tutor.display_name) for a in assignments
        ]
        self.fields["date"].widget.attrs.update({"min": first.isoformat(), "max": last.isoformat()})

    def clean_date(self) -> dt.date:
        day: dt.date = self.cleaned_data["date"]
        if not (self.first <= day <= self.last):
            raise forms.ValidationError(
                _("Choose a date from %(first)s to %(last)s."),
                params={"first": self.first, "last": self.last},
            )
        return day


class SlotForm(forms.Form):
    """Which open time was chosen. Travels in the query string and in hidden fields."""

    assignment = forms.UUIDField(widget=forms.HiddenInput)
    start = forms.CharField(widget=forms.HiddenInput)
    minutes = forms.IntegerField(widget=forms.HiddenInput, min_value=1, max_value=600)

    def clean_start(self) -> datetime:
        try:
            value = datetime.fromisoformat(self.cleaned_data["start"])
        except ValueError as exc:
            raise forms.ValidationError(_("That time isn't valid.")) from exc
        if value.tzinfo is None:
            raise forms.ValidationError(_("That time isn't valid."))
        return value.astimezone(UTC)


class BookForm(SlotForm):
    modality = forms.ChoiceField(label=_("How would you like to meet?"), choices=Modality.choices)

    def __init__(self, *args: Any, modalities: list[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["modality"]).choices = [
            (value, label) for value, label in Modality.choices if value in modalities
        ]


class StaffBookForm(BookForm):
    student = PersonChoiceField(
        label=_("Student"),
        queryset=User.objects.none(),
        required=False,
        help_text=_("Leave empty only for a placeholder."),
    )
    is_walk_in = forms.BooleanField(label=_("Walk-in"), required=False)
    is_placeholder = forms.BooleanField(
        label=_("Placeholder: hold this time without a student"), required=False
    )
    is_tentative = forms.BooleanField(label=_("Tentative: confirm it later"), required=False)
    override_limits = forms.BooleanField(
        label=_("Book even if it goes over the student's or tutor's limits"), required=False
    )
    weeks = forms.IntegerField(
        label=_("Repeat weekly, number of weeks"),
        min_value=1,
        max_value=16,
        initial=1,
        help_text=_("1 books just this date."),
    )

    def __init__(self, *args: Any, center: Center, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        choice_field(self, "student").queryset = students_of(center)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("is_placeholder") and data.get("student"):
            self.add_error("student", _("A placeholder doesn't have a student."))
        if not data.get("is_placeholder") and not data.get("student"):
            self.add_error("student", _("Choose a student, or make this a placeholder."))
        return data


class DayForm(forms.Form):
    date = forms.DateField(label=_("Date"), widget=DateInput())


# --- Setup ----------------------------------------------------------------------


class TermForm(forms.ModelForm):
    class Meta:
        model = Term
        fields = ["name", "starts_on", "ends_on"]
        widgets = {"starts_on": DateInput(), "ends_on": DateInput()}


class ClosureForm(forms.ModelForm):
    class Meta:
        model = Closure
        fields = ["label", "starts_on", "ends_on"]
        widgets = {"starts_on": DateInput(), "ends_on": DateInput()}

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        term = self.instance.term
        for name in ("starts_on", "ends_on"):
            day = data.get(name)
            if day and not (term.starts_on <= day <= term.ends_on):
                self.add_error(name, _("Choose a date inside the term."))
        return data


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["name", "details", "is_active"]


class ScheduleForm(forms.ModelForm):
    class Meta:
        model = Schedule
        fields = [
            "name",
            "description",
            "term",
            "starts_on",
            "ends_on",
            "opens_at",
            "closes_at",
            "slot_minutes",
            "buffer_minutes",
            "visibility",
            "min_notice_minutes",
            "horizon_days",
            "change_cutoff_minutes",
            "limits_span_center",
            "max_per_day",
            "max_per_week",
            "max_per_term",
            "max_minutes_per_day",
            "max_minutes_per_week",
            "max_minutes_per_term",
            "reminder_1_minutes",
            "reminder_2_minutes",
            "reminder_3_minutes",
            "late_cancel_minutes",
            "no_show_grace_minutes",
            "no_show_block_limit",
            "late_cancel_block_limit",
            "block_count_from",
        ]
        widgets = {
            "starts_on": DateInput(),
            "ends_on": DateInput(),
            "opens_at": TimeInput(),
            "closes_at": TimeInput(),
            "block_count_from": DateInput(),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        choice_field(self, "term").queryset = Term.objects.filter(center=self.instance.center)
        for name in ("reminder_1_minutes", "reminder_2_minutes", "reminder_3_minutes"):
            cast(forms.ChoiceField, self.fields[name]).choices = [
                ("", _("No reminder")),
                *REMINDER_CHOICES,
            ]
        # Left empty, these take their defaults: no late window, 15 minutes' grace.
        for name in ("late_cancel_minutes", "no_show_grace_minutes"):
            self.fields[name].required = False

    def clean_late_cancel_minutes(self) -> int:
        value = self.cleaned_data.get("late_cancel_minutes")
        return 0 if value is None else int(value)

    def clean_no_show_grace_minutes(self) -> int:
        value = self.cleaned_data.get("no_show_grace_minutes")
        return 15 if value is None else int(value)


class AssignmentForm(forms.ModelForm):
    tutor = PersonChoiceField(label=_("Tutor"), queryset=User.objects.none())

    class Meta:
        model = TutorAssignment
        fields = [
            "tutor",
            "min_minutes",
            "max_minutes",
            "max_per_day",
            "max_per_week",
            "offers_in_person",
            "room",
            "offers_online",
            "meeting_url",
            "capacity",
            "needs_confirmation",
            "is_active",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        center = self.instance.schedule.center
        tutor = choice_field(self, "tutor")
        tutor.queryset = tutors_of(center)
        choice_field(self, "room").queryset = Room.objects.filter(center=center, is_active=True)
        if not self.instance._state.adding:
            # Moving appointments to another person isn't an edit; add a new assignment.
            tutor.disabled = True

    def clean_tutor(self) -> User:
        tutor: User = self.cleaned_data["tutor"]
        schedule = self.instance.schedule
        clash = TutorAssignment.objects.filter(schedule=schedule, tutor=tutor).exclude(
            pk=self.instance.pk
        )
        if clash.exists():
            raise forms.ValidationError(_("This tutor is already on this schedule."))
        return tutor


class WeeklyHoursForm(forms.ModelForm):
    class Meta:
        model = WeeklyHours
        fields = ["weekday", "starts_at", "ends_at"]
        widgets = {"starts_at": TimeInput(), "ends_at": TimeInput()}


WeeklyHoursFormSet = forms.inlineformset_factory(
    TutorAssignment,
    WeeklyHours,
    form=WeeklyHoursForm,
    extra=3,
    can_delete=True,
)


class ExceptionForm(forms.ModelForm):
    class Meta:
        model = AvailabilityException
        fields = ["kind", "starts_at", "ends_at", "note"]
        widgets = {"starts_at": DateTimeLocalInput(), "ends_at": DateTimeLocalInput()}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in ("starts_at", "ends_at"):
            self.fields[name].input_formats = [DATETIME_LOCAL]  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        start, end = data.get("starts_at"), data.get("ends_at")
        if data.get("kind") == ExceptionKind.BLACKOUT and start and end and end > start:
            booked = Appointment.objects.filter(
                assignment=self.instance.assignment, starts_at__lt=end, ends_at__gt=start
            ).exclude(status=AppointmentStatus.CANCELED)
            if booked.exists():
                raise forms.ValidationError(
                    _("The tutor has appointments during that time. Cancel or move them first.")
                )
        return data


class ExemptionForm(forms.ModelForm):
    student = PersonChoiceField(label=_("Student"), queryset=User.objects.none())

    class Meta:
        model = Exemption
        fields = ["student", "schedule", "expires_on", "note"]
        widgets = {"expires_on": DateInput(), "note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        center = self.instance.center
        choice_field(self, "student").queryset = students_of(center)
        schedule = choice_field(self, "schedule")
        schedule.queryset = Schedule.objects.filter(center=center)
        schedule.empty_label = str(_("Every schedule"))


class ConfirmForm(forms.Form):
    """A POST with nothing but the CSRF token, for "Are you sure?" pages."""


class StaffCancelForm(forms.Form):
    student_asked = forms.BooleanField(
        label=_("The student asked to cancel"),
        required=False,
        help_text=_(
            "Inside the late cancellation window, this records a late cancellation for the "
            "student. Leave it unchecked when the center is canceling."
        ),
    )


# --- Attendance -------------------------------------------------------------------


def attendance_field_name(student_id: Any) -> str:
    return f"attendance-{student_id}"


class AttendanceForm(forms.Form):
    """One set of choices per student in the appointment. Students left unanswered
    aren't changed, so a list page can post one student's mark on its own."""

    CHOICES = [
        (Attendance.ATTENDED.value, _("Came")),
        (Attendance.NO_SHOW.value, _("No-show")),
        (Attendance.UNMARKED.value, _("Not marked")),
    ]

    def __init__(self, *args: Any, participants: list[Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.participants = participants
        for p in participants:
            name = attendance_field_name(p.student_id)
            self.fields[name] = forms.ChoiceField(
                label=p.student.display_name,
                choices=self.CHOICES,
                widget=forms.RadioSelect,
                required=False,
                initial=p.attendance,
            )
            p.field = self[name]

    def marks(self) -> dict[Any, str]:
        """The marks that were posted, by student ID."""
        return {
            p.student_id: self.cleaned_data[attendance_field_name(p.student_id)]
            for p in self.participants
            if attendance_field_name(p.student_id) in self.data
        }


class WaiveForm(forms.Form):
    reason = forms.CharField(
        label=_("Why it's being waived"),
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=1000,
        help_text=_("Kept with the student's record. They can ask to see it."),
    )


class LiftBlockForm(forms.Form):
    note = forms.CharField(
        label=_("Note (optional)"),
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=1000,
        required=False,
        help_text=_("Kept with the block. The student can ask to see it."),
    )


class CenterBlockForm(forms.ModelForm):
    class Meta:
        model = Center
        fields = ["no_show_block_limit", "late_cancel_block_limit", "block_count_from"]
        widgets = {"block_count_from": DateInput()}
