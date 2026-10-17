"""The questions on the booking pages.

A student booking for themselves answers the center's registration form, the
first time they book there, and the schedule's intake form. Staff booking for a
student see only the intake form, and may leave it all blank to skip it; the
student can answer it later from the appointment page.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from django.http import HttpRequest, QueryDict

from sympraxi.accounts.models import User
from sympraxi.forms import services
from sympraxi.forms.answering import CONTINUE, Questions
from sympraxi.scheduling.models import Appointment, Schedule


@dataclass
class BookingQuestions:
    registration: Questions | None = None
    intake: Questions | None = None

    @property
    def groups(self) -> list[Questions]:
        return [q for q in (self.registration, self.intake) if q is not None]

    @property
    def has_follow_ups(self) -> bool:
        return any(q.has_follow_ups for q in self.groups)

    @property
    def status(self) -> str:
        return " ".join(s for q in self.groups if (s := q.status))

    def needs_another_look(self, data: QueryDict) -> bool:
        """Continue was pressed, or the answers revealed follow-ups not yet seen."""
        return CONTINUE in data or any(q.needs_another_look for q in self.groups)

    def again(self) -> "BookingQuestions":
        return BookingQuestions(
            registration=self.registration.again() if self.registration else None,
            intake=self.intake.again() if self.intake else None,
        )

    def is_valid(self) -> bool:
        # Validate every group, so each shows its own errors.
        return all([q.is_valid() for q in self.groups])

    def save(
        self,
        *,
        appointments: Sequence[Appointment],
        student: User,
        answered_by: User,
        request: HttpRequest | None = None,
    ) -> None:
        """Store the answers. Staff who left the intake blank skipped it: nothing is saved."""
        if self.registration is not None:
            services.save_response(
                version=self.registration.version,
                values=self.registration.values(),
                answered_by=answered_by,
                student=student,
                request=request,
            )
        if self.intake is not None and not self.intake.skipped:
            values = self.intake.values()
            for appointment in appointments:
                services.save_response(
                    version=self.intake.version,
                    values=values,
                    answered_by=answered_by,
                    student=student,
                    appointment=appointment,
                    request=request,
                )


def for_student(schedule: Schedule, student: User, data: QueryDict | None) -> BookingQuestions:
    registration = services.registration_to_ask(schedule.center, student)
    intake = services.intake_version(schedule)
    return BookingQuestions(
        registration=Questions(registration, prefix="registration", data=data)
        if registration
        else None,
        intake=Questions(intake, prefix="intake", data=data) if intake else None,
    )


def for_staff(schedule: Schedule, data: QueryDict | None) -> BookingQuestions:
    intake = services.intake_version(schedule)
    return BookingQuestions(
        intake=Questions(intake, prefix="intake", data=data, skippable=True) if intake else None
    )
