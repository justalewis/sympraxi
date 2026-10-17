"""Announcements on the center's pages, and mass email to the center's people.

Announcements are plain text a director posts for a span of days, for everyone
in the center, for students, or for staff.

A mass email goes to one group of the center's people: students, tutors, front
desk staff, directors, all staff, or everyone, and for students optionally only
those with an appointment in a span of days (on one schedule, or all). Students
who turned off center news are left out and counted; staff always get it, since
it's about their work. Each person gets their own copy, so nobody sees anyone
else's address. The director sees how many people it will reach before it goes,
and every send is kept in a log with who it went to, and audited.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import QuerySet
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from sympraxi.accounts.models import User
from sympraxi.centers.models import STAFF_ROLES, Center, Role
from sympraxi.core.policy import roles_in
from sympraxi.notify.models import Announcement, Audience, MassEmail, MassEmailRecipient
from sympraxi.scheduling.models import Participant, Schedule
from sympraxi.scheduling.timeutils import date_range_bounds, local_date

# --- Announcements ------------------------------------------------------------------


def showing(center: Center, user: Any, *, today: date | None = None) -> list[Announcement]:
    """The announcements this person sees in the center today."""
    roles = roles_in(user, center)
    if not roles:
        return []
    today = today or local_date(timezone.now(), ZoneInfo(center.time_zone))
    audiences = [Audience.EVERYONE]
    if Role.STUDENT in roles:
        audiences.append(Audience.STUDENTS)
    if roles & STAFF_ROLES:
        audiences.append(Audience.STAFF)
    return list(
        Announcement.objects.filter(
            center=center,
            audience__in=audiences,
            starts_on__lte=today,
            ends_on__gte=today,
        ).order_by("-starts_on", "-created_at")
    )


# --- Mass email ---------------------------------------------------------------------


GROUPS: dict[str, tuple[Any, frozenset[Role]]] = {
    "students": (gettext_lazy("Students"), frozenset({Role.STUDENT})),
    "tutors": (gettext_lazy("Tutors"), frozenset({Role.TUTOR})),
    "front_desk": (gettext_lazy("Front desk staff"), frozenset({Role.FRONT_DESK})),
    "directors": (gettext_lazy("Directors"), frozenset({Role.DIRECTOR})),
    "staff": (gettext_lazy("All staff: tutors, front desk staff and directors"), STAFF_ROLES),
    "everyone": (gettext_lazy("Everyone in the center"), frozenset(Role)),
}


@dataclass
class Reach:
    """Who a mass email goes to, and who it leaves out."""

    group: str
    first: date | None = None
    last: date | None = None
    schedule: Schedule | None = None

    @property
    def roles(self) -> frozenset[Role]:
        return GROUPS[self.group][1]

    @property
    def booked_only(self) -> bool:
        return self.first is not None and self.last is not None

    def label(self) -> str:
        words = str(GROUPS[self.group][0])
        first, last = self.first, self.last
        if first is not None and last is not None:
            words += " " + _("with an appointment from %(first)s to %(last)s") % {
                "first": first.isoformat(),
                "last": last.isoformat(),
            }
            if self.schedule is not None:
                words += " " + _("on %(schedule)s") % {"schedule": self.schedule.name}
        return words[:300]

    def people(self, center: Center) -> QuerySet[User]:
        """Everyone it fits, before anyone's choice about center news."""
        found = User.objects.filter(
            is_active=True,
            memberships__center=center,
            memberships__is_active=True,
            memberships__role__in=self.roles,
        )
        first, last = self.first, self.last
        if first is not None and last is not None:
            start, end = date_range_bounds(first, last, ZoneInfo(center.time_zone))
            booked = Participant.objects.filter(
                canceled_at__isnull=True,
                appointment__schedule__center=center,
                appointment__starts_at__gte=start,
                appointment__starts_at__lt=end,
            ).exclude(appointment__status="canceled")
            if self.schedule is not None:
                booked = booked.filter(appointment__schedule=self.schedule)
            found = found.filter(pk__in=booked.values("student_id"))
        return found.distinct()

    def split(self, center: Center) -> tuple[QuerySet[User], int]:
        """(who gets it, how many students it leaves out because they turned off
        center news). Staff here get it whatever they chose: it's about their work."""
        everyone = self.people(center)
        quiet = everyone.filter(center_news_emails=False).exclude(pk__in=staff_of(center))
        skipped = quiet.count()
        return everyone.exclude(pk__in=quiet.values("pk")).order_by("full_name", "email"), skipped


def staff_of(center: Center) -> Any:
    """The IDs of the center's staff, as a subquery."""
    return User.objects.filter(
        memberships__center=center,
        memberships__is_active=True,
        memberships__role__in=STAFF_ROLES,
    ).values("pk")


def message(mass: MassEmail, user: User, *, url_base: str, student_only: bool) -> EmailMessage:
    """One person's copy. A student's says how to stop center news; staff can't."""
    center = mass.center
    context = {
        "mass": mass,
        "center": center,
        "person": user,
        "settings_url": url_base.rstrip("/") + reverse("accounts:reminders"),
        "student_only": student_only,
        "SITE_NAME": settings.SYMPRAXI_SITE_NAME,
    }
    body = render_to_string("notify/email/mass_email.txt", context)
    reply_to = center.contact_email or (mass.sent_by.email if mass.sent_by else "")
    return EmailMessage(
        subject=f"[{settings.SYMPRAXI_SITE_NAME}] {mass.subject}",
        body=body,
        to=[user.email],
        reply_to=[reply_to] if reply_to else None,
    )


def send(
    center: Center,
    reach: Reach,
    *,
    subject: str,
    body: str,
    sent_by: User,
    url_base: str,
) -> MassEmail:
    """Log the email with its recipients, then hand the sending to the task worker."""
    from sympraxi.notify.tasks import queue_mass_email

    recipients, skipped = reach.split(center)
    with transaction.atomic():
        mass = MassEmail.objects.create(
            center=center,
            subject=subject.strip(),
            body=body.strip(),
            audience=reach.label(),
            sent_by=sent_by,
            skipped=skipped,
        )
        MassEmailRecipient.objects.bulk_create(
            [
                MassEmailRecipient(mass_email=mass, user_id=pk)
                for pk in recipients.values_list("pk", flat=True)
            ]
        )
        mass_id = str(mass.pk)
        transaction.on_commit(lambda: queue_mass_email.enqueue(mass_id, url_base))
    return mass


def queue_copies(mass_email_id: str, url_base: str, *, chunk: int = 200) -> int:
    """Queue each recipient's copy that isn't queued yet. Safe to run twice."""
    mass = MassEmail.objects.select_related("center", "sent_by").get(pk=mass_email_id)
    staff = set(staff_of(mass.center).values_list("pk", flat=True))
    queued = 0
    while True:
        with transaction.atomic():
            batch = list(
                MassEmailRecipient.objects.select_for_update(skip_locked=True)
                .filter(mass_email=mass, queued_at__isnull=True)
                .select_related("user")
                .order_by("pk")[:chunk]
            )
            if not batch:
                return queued
            now = timezone.now()
            for recipient in batch:
                # The default mailer queues each copy as its own delivery task.
                message(
                    mass,
                    recipient.user,
                    url_base=url_base,
                    student_only=recipient.user_id not in staff,
                ).send()
                recipient.queued_at = now
            MassEmailRecipient.objects.bulk_update(batch, ["queued_at"])
            queued += len(batch)
