"""Onboarding a cohort: mentors, sign-offs, observations and completion.

A person has finished onboarding when, in their cohort:

- every required, published module is done (items finished, quizzes passed) and signed
  off by their mentor or a director,
- they have at least the number of submitted observations the cohort asks for, and
- they have the training hours the center requires, if it requires any.

A director then marks onboarding complete, which is logged. Nothing is marked complete
on its own, so a person's file always shows who decided.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.training import progress
from sympraxi.training.models import (
    Cohort,
    CohortMember,
    Criterion,
    Module,
    Observation,
    ObservationStatus,
    Rating,
    SignOff,
    Template,
)

# Roles that can mentor and observe.
MENTOR_ROLES = (Role.TUTOR, Role.FRONT_DESK, Role.DIRECTOR)


class OnboardingError(Exception):
    """A rule refused the request; the message is written for the person who asked."""


def people_of(center: Center, roles: tuple[str, ...]) -> list[User]:
    return list(
        User.objects.filter(
            memberships__center=center,
            memberships__is_active=True,
            memberships__role__in=roles,
            is_active=True,
        )
        .distinct()
        .order_by("full_name", "email")
    )


def mentors_for(center: Center) -> list[User]:
    return people_of(center, MENTOR_ROLES)


def addable(cohort: Cohort) -> list[User]:
    """Tutors and front desk staff who aren't yet in this cohort."""
    taken = set(cohort.members.values_list("person_id", flat=True))
    return [p for p in people_of(cohort.center, progress.TRAINEE_ROLES) if p.pk not in taken]


def add_member(
    cohort: Cohort, person: User, mentor: User | None, *, request: HttpRequest
) -> CohortMember:
    center = cohort.center
    if not Membership.objects.filter(
        user=person, center=center, role__in=progress.TRAINEE_ROLES, is_active=True
    ).exists():
        raise OnboardingError(_("Only tutors and front desk staff are onboarded."))
    if mentor is not None and mentor.pk == person.pk:
        raise OnboardingError(_("A person can't be their own mentor."))
    member, made = CohortMember.objects.get_or_create(
        cohort=cohort, person=person, defaults={"mentor": mentor}
    )
    if not made:
        raise OnboardingError(
            _("%(name)s is already in this cohort.") % {"name": person.display_name}
        )
    record(
        AuditAction.ONBOARDING_CHANGED,
        request=request,
        center=center,
        target=member,
        what="added",
        person=str(person.pk),
        mentor=str(mentor.pk) if mentor else "",
    )
    return member


def set_mentor(member: CohortMember, mentor: User | None, *, request: HttpRequest) -> None:
    if mentor is not None and mentor.pk == member.person_id:
        raise OnboardingError(_("A person can't be their own mentor."))
    member.mentor = mentor
    member.save(update_fields=["mentor"])
    record(
        AuditAction.ONBOARDING_CHANGED,
        request=request,
        center=member.center,
        target=member,
        what="mentor set",
        mentor=str(mentor.pk) if mentor else "",
    )


def remove_member(member: CohortMember, *, request: HttpRequest) -> None:
    record(
        AuditAction.ONBOARDING_CHANGED,
        request=request,
        center=member.center,
        target=member,
        what="removed",
        person=str(member.person_id),
    )
    member.delete()


@dataclass
class ModuleLine:
    module: Module
    done: bool
    signed_off: SignOff | None


@dataclass
class MemberStatus:
    member: CohortMember
    lines: list[ModuleLine] = field(default_factory=list)
    observations: int = 0
    required_observations: int = 0
    hours: Decimal = Decimal("0.00")
    hours_ok: bool = True

    @property
    def signed_off_count(self) -> int:
        return sum(1 for line in self.lines if line.signed_off is not None)

    @property
    def modules_ok(self) -> bool:
        return all(line.signed_off is not None for line in self.lines if line.module.is_required)

    @property
    def observations_ok(self) -> bool:
        return self.observations >= self.required_observations

    @property
    def ready(self) -> bool:
        return self.modules_ok and self.observations_ok and self.hours_ok

    @property
    def complete(self) -> bool:
        return self.member.completed_at is not None


def status_of(member: CohortMember) -> MemberStatus:
    center = member.center
    modules = list(
        Module.objects.filter(center=center, is_published=True).prefetch_related("items")
    )
    done = progress.done_item_ids(member.person, [m.pk for m in modules])
    signs = {s.module_id: s for s in member.sign_offs.all()}
    lines = []
    for module in modules:
        ids = {i.pk for i in module.items.all()}
        lines.append(
            ModuleLine(module, bool(ids) and ids <= done, signs.get(module.pk)),
        )
    hours = progress.training_hours(center, [member.person_id]).get(
        member.person_id, Decimal("0.00")
    )
    return MemberStatus(
        member=member,
        lines=lines,
        observations=Observation.objects.filter(
            tutor=member.person, center=center, status=ObservationStatus.SUBMITTED
        ).count(),
        required_observations=member.cohort.required_observations,
        hours=hours,
        hours_ok=progress.hours_met(center, hours),
    )


def sign_off(
    member: CohortMember, module: Module, *, by: User, note: str, request: HttpRequest
) -> SignOff:
    line = next((ln for ln in status_of(member).lines if ln.module.pk == module.pk), None)
    if line is None:
        raise OnboardingError(_("That module isn't published."))
    if not line.done:
        raise OnboardingError(
            _("%(name)s hasn't finished that module yet.") % {"name": member.person.display_name}
        )
    signed, made = SignOff.objects.get_or_create(
        member=member, module=module, defaults={"signed_by": by, "note": note[:300]}
    )
    if made:
        record(
            AuditAction.ONBOARDING_CHANGED,
            request=request,
            center=member.center,
            target=member,
            what="signed off",
            module=str(module.pk),
        )
    return signed


def complete(member: CohortMember, *, by: User, request: HttpRequest) -> None:
    status = status_of(member)
    if member.completed_at is not None:
        raise OnboardingError(_("Onboarding is already complete."))
    if not status.ready:
        raise OnboardingError(_("Something is still outstanding."))
    member.completed_at = timezone.now()
    member.completed_by = by
    member.save(update_fields=["completed_at", "completed_by"])
    record(
        AuditAction.ONBOARDING_COMPLETED,
        request=request,
        center=member.center,
        target=member,
        person=str(member.person_id),
    )


# --- Observations ----------


def start_observation(
    template: Template, tutor: User, observer: User, observed_on: date, *, request: HttpRequest
) -> Observation:
    if not template.is_active:
        raise OnboardingError(_("That checklist isn't in use."))
    if not template.criteria.exists():
        raise OnboardingError(_("That checklist has nothing on it yet."))
    if tutor.pk == observer.pk:
        raise OnboardingError(_("You can't observe yourself."))
    observation = Observation.objects.create(
        center=template.center,
        template=template,
        tutor=tutor,
        observer=observer,
        observed_on=observed_on,
    )
    record(
        AuditAction.OBSERVATION_STARTED,
        request=request,
        center=template.center,
        target=observation,
        tutor=str(tutor.pk),
    )
    return observation


def save_observation(
    observation: Observation,
    scores: dict[str, str],
    comments: dict[str, str],
    notes: str,
    *,
    submit: bool,
    request: HttpRequest,
) -> None:
    """Save the ratings. Submitting needs a score for every criterion and locks the form."""
    criteria = list(observation.template.criteria.all())
    clean: dict[Criterion, int] = {}
    for criterion in criteria:
        raw = scores.get(str(criterion.pk), "")
        if raw in {"1", "2", "3", "4"}:
            clean[criterion] = int(raw)
    if submit and len(clean) < len(criteria):
        raise OnboardingError(_("Give every item a rating before submitting."))
    with transaction.atomic():
        locked = Observation.objects.select_for_update().get(pk=observation.pk)
        if locked.is_submitted:
            raise OnboardingError(_("A submitted observation can't be changed."))
        locked.notes = notes
        for criterion in criteria:
            if criterion in clean:
                Rating.objects.update_or_create(
                    observation=locked,
                    criterion=criterion,
                    defaults={
                        "score": clean[criterion],
                        "comment": comments.get(str(criterion.pk), "")[:300],
                    },
                )
            else:
                Rating.objects.filter(observation=locked, criterion=criterion).delete()
        if submit:
            locked.status = ObservationStatus.SUBMITTED
            locked.submitted_at = timezone.now()
        locked.save()
        if submit:
            record(
                AuditAction.OBSERVATION_SUBMITTED,
                request=request,
                center=locked.center,
                target=locked,
                tutor=str(locked.tutor_id),
            )
