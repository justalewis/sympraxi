"""Adding people to a center and turning their roles on and off.

Directors (and site administrators) add people one at a time or from a CSV
file, and turn roles off at the end of a term. Each person's role in a center
is a `Membership`; turning a role off keeps the membership, and its history,
with `is_active` false.

Someone not yet in Sympraxi gets an account with no password. They claim it by
signing in with single sign-on, which matches the account by its verified email
address, or with "Forgot your password?". An account that already exists is
never changed by an import: its name, pronouns and student ID belong to the
person, and another center can't rename them. The preview and the results say
only whether each person will be added to this center, never whether they had
an account already, so an import can't be used to look people up.

Every membership added, turned on or turned off is audited, and so is each
account created.
"""

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Participant

# The most rows one file may add, and its largest size.
MAX_ROWS = 5000
MAX_BYTES = 2 * 1024 * 1024

# Column headings, compared without case, spaces or punctuation.
COLUMNS = {
    "email": {"email", "emailaddress", "mail", "collegeemail", "schoolemail"},
    "full_name": {"fullname", "name"},
    "first_name": {"firstname", "givenname", "first"},
    "last_name": {"lastname", "surname", "familyname", "last"},
    "preferred_name": {"preferredname", "nickname", "chosenname"},
    "pronouns": {"pronouns"},
    "student_id": {"studentid", "id", "studentnumber", "sid"},
    "role": {"role"},
}

# What a role cell may say, compared the same way.
ROLE_WORDS = {
    "student": Role.STUDENT,
    "students": Role.STUDENT,
    "tutor": Role.TUTOR,
    "tutors": Role.TUTOR,
    "consultant": Role.TUTOR,
    "consultants": Role.TUTOR,
    "frontdesk": Role.FRONT_DESK,
    "director": Role.DIRECTOR,
    "directors": Role.DIRECTOR,
}

COLUMN_LABELS = {
    "email": gettext_lazy("Email"),
    "full_name": gettext_lazy("Full name"),
    "preferred_name": gettext_lazy("Preferred name"),
    "pronouns": gettext_lazy("Pronouns"),
    "student_id": gettext_lazy("Student ID"),
}

# Longest values, from the User model.
MAX_LENGTHS = {
    "email": 254,
    "full_name": 200,
    "preferred_name": 100,
    "pronouns": 50,
    "student_id": 64,
}


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass
class PersonRow:
    """One person to add, in one role, from a form or a line of a file."""

    line: int
    email: str
    full_name: str
    role: Role
    preferred_name: str = ""
    pronouns: str = ""
    student_id: str = ""

    def as_data(self) -> dict[str, Any]:
        return {**asdict(self), "role": str(self.role)}

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "PersonRow":
        return cls(**{**data, "role": Role(data["role"])})


@dataclass
class RowProblem:
    line: int
    email: str
    problems: list[str]


@dataclass
class Reading:
    """What a file holds: rows ready to add, and lines that can't be added as they are."""

    rows: list[PersonRow] = field(default_factory=list)
    problems: list[RowProblem] = field(default_factory=list)
    # A problem with the whole file, such as no email column. Then nothing is read.
    file_problem: str = ""


def allowed_domains() -> list[str]:
    return [d.lower() for d in settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS]


def taken_student_ids(student_ids: Iterable[str]) -> dict[str, str]:
    """Student ID -> the email address of the account that has it."""
    wanted = {s.strip() for s in student_ids if s and s.strip()}
    if not wanted:
        return {}
    return {
        str(sid): email.lower()
        for sid, email in User.objects.filter(student_id__in=wanted).values_list(
            "student_id", "email"
        )
    }


class Checker:
    """Checks rows one at a time, remembering what earlier rows said.

    `taken` maps student IDs that accounts already have to those accounts' email
    addresses (`taken_student_ids`), so a file is checked with one query.
    """

    def __init__(self, default_role: Role | None, taken: dict[str, str]) -> None:
        self.default_role = default_role
        self.taken = taken
        self.domains = allowed_domains()
        self.seen: dict[tuple[str, Role], int] = {}
        self.student_ids: dict[str, tuple[str, int]] = {}

    def check(self, line: int, values: dict[str, str]) -> PersonRow | RowProblem:
        problems: list[str] = []
        email = values.get("email", "").strip()
        first = values.get("first_name", "").strip()
        last = values.get("last_name", "").strip()
        full_name = values.get("full_name", "").strip() or " ".join(p for p in (first, last) if p)
        preferred_name = values.get("preferred_name", "").strip()
        pronouns = values.get("pronouns", "").strip()
        student_id = values.get("student_id", "").strip()

        if not email:
            problems.append(_("There's no email address."))
        else:
            try:
                validate_email(email)
            except ValidationError:
                problems.append(_("“%(email)s” isn't an email address.") % {"email": email})
            else:
                local, _at, domain = email.rpartition("@")
                email = f"{local}@{domain.lower()}"
                if self.domains and domain.lower() not in self.domains:
                    problems.append(
                        _("Only college addresses can be added (%(domains)s).")
                        % {"domains": ", ".join("@" + d for d in self.domains)}
                    )
        if not full_name:
            problems.append(_("There's no name."))
        for name, value in (
            ("email", email),
            ("full_name", full_name),
            ("preferred_name", preferred_name),
            ("pronouns", pronouns),
            ("student_id", student_id),
        ):
            if len(value) > MAX_LENGTHS[name]:
                problems.append(
                    _("%(column)s is longer than %(n)s characters.")
                    % {"column": COLUMN_LABELS[name], "n": MAX_LENGTHS[name]}
                )

        role_text = values.get("role", "").strip()
        role: Role | None
        if role_text:
            role = ROLE_WORDS.get(_key(role_text))
            if role is None:
                problems.append(
                    _("“%(role)s” isn't a role. Use student, tutor, front desk or director.")
                    % {"role": role_text}
                )
        else:
            role = self.default_role
            if role is None:
                problems.append(_("There's no role, and none was chosen for the whole file."))

        if email and role is not None:
            earlier = self.seen.get((email.lower(), role))
            if earlier is not None:
                problems.append(
                    _("Line %(line)s already adds this person in this role.") % {"line": earlier}
                )
        if student_id:
            earlier_id = self.student_ids.get(student_id)
            if earlier_id is not None and earlier_id[0] != email.lower():
                problems.append(
                    _("Line %(line)s gives someone else this student ID.") % {"line": earlier_id[1]}
                )
            elif self.taken.get(student_id, email.lower()) != email.lower():
                problems.append(_("Another account already has this student ID."))

        if problems or role is None:
            return RowProblem(line=line, email=email, problems=problems)
        self.seen[(email.lower(), role)] = line
        if student_id:
            self.student_ids.setdefault(student_id, (email.lower(), line))
        return PersonRow(
            line=line,
            email=email,
            full_name=full_name,
            role=role,
            preferred_name=preferred_name,
            pronouns=pronouns,
            student_id=student_id,
        )


def decode(data: bytes) -> str:
    """UTF-8 (with or without Excel's byte order mark), else Windows-1252, which is
    what Excel's plain "CSV (Comma delimited)" saves on Windows."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_csv(data: bytes, *, default_role: Role | None) -> Reading:
    if len(data) > MAX_BYTES:
        return Reading(file_problem=_("The file is larger than 2 MB. Split it into smaller files."))
    text = decode(data)
    if not text.strip():
        return Reading(file_problem=_("The file is empty."))
    first_line = text.splitlines()[0]
    try:
        dialect: Any = csv.Sniffer().sniff(first_line, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        return Reading(file_problem=_("The file couldn't be read as CSV."))
    columns: dict[int, str] = {}
    for index, heading in enumerate(header):
        key = _key(heading)
        for name, words in COLUMNS.items():
            if key in words and name not in columns.values():
                columns[index] = name
    if "email" not in columns.values():
        return Reading(
            file_problem=_(
                "The first line must name the columns, and one of them must be Email. "
                "The file's first line is: %(line)s"
            )
            % {"line": first_line[:200]}
        )
    if not {"full_name", "first_name", "last_name"} & set(columns.values()):
        return Reading(
            file_problem=_(
                "The file needs a Full name column, or First name and Last name columns."
            )
        )

    lines: list[tuple[int, dict[str, str]]] = []
    try:
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            if len(lines) == MAX_ROWS:
                return Reading(
                    file_problem=_(
                        "The file has more than %(n)s people. Split it into smaller files."
                    )
                    % {"n": MAX_ROWS}
                )
            values = {name: cells[i] for i, name in columns.items() if i < len(cells)}
            lines.append((reader.line_num, values))
    except csv.Error as error:
        return Reading(
            file_problem=_("Line %(line)s couldn't be read: %(error)s")
            % {"line": reader.line_num, "error": error}
        )

    reading = Reading()
    checker = Checker(default_role, taken_student_ids(v.get("student_id", "") for _n, v in lines))
    for line, values in lines:
        result = checker.check(line, values)
        if isinstance(result, PersonRow):
            reading.rows.append(result)
        else:
            reading.problems.append(result)
    if not reading.rows and not reading.problems:
        reading.file_problem = _("The file names its columns but has nobody in it.")
    return reading


# --- Adding ------------------------------------------------------------------------


@dataclass
class Outcome:
    added: int = 0
    turned_on: int = 0
    already: int = 0
    # The people whose membership was added or turned on, for the welcome email.
    welcomed: dict[Any, list[Role]] = field(default_factory=dict)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(
                ngettext("added %(n)s role", "added %(n)s roles", self.added) % {"n": self.added}
            )
        if self.turned_on:
            parts.append(
                ngettext("turned %(n)s role back on", "turned %(n)s roles back on", self.turned_on)
                % {"n": self.turned_on}
            )
        if self.already:
            parts.append(
                ngettext(
                    "%(n)s was already in the center",
                    "%(n)s were already in the center",
                    self.already,
                )
                % {"n": self.already}
            )
        if not parts:
            return _("Nothing changed.")
        text = ", ".join(parts)
        return text[0].upper() + text[1:] + "."


def _audit_membership(
    request: HttpRequest, membership: Membership, action: AuditAction, *, via: str
) -> None:
    record(
        action,
        request=request,
        center=membership.center,
        target=membership,
        member=str(membership.user_id),
        role=membership.role,
        active=membership.is_active,
        via=via,
    )


def _account_for(row: PersonRow, *, request: HttpRequest, center: Center, via: str) -> User:
    user = User.objects.filter(email__iexact=row.email).first()
    if user is not None:
        return user
    user = User.objects.create_user(
        row.email,
        password=None,
        full_name=row.full_name,
        preferred_name=row.preferred_name,
        pronouns=row.pronouns,
        student_id=row.student_id or None,
    )
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=False)
    # A site-level event: the center's log says the person was added to the center.
    record(
        AuditAction.ACCOUNT_CREATED, request=request, target=user, center_id=str(center.pk), via=via
    )
    return user


def add_people(
    center: Center, rows: Iterable[PersonRow], *, request: HttpRequest, via: str
) -> Outcome:
    """Add each row's person in its role. All of it happens, or none of it does.

    Raises IntegrityError if another change got in first (two imports giving the
    same student ID at once, say); nothing is added then.
    """
    outcome = Outcome()
    with transaction.atomic():
        for row in rows:
            user = _account_for(row, request=request, center=center, via=via)
            membership, made = Membership.objects.select_for_update().get_or_create(
                user=user, center=center, role=row.role
            )
            if made:
                outcome.added += 1
                _audit_membership(request, membership, AuditAction.MEMBERSHIP_ADDED, via=via)
            elif not membership.is_active:
                membership.is_active = True
                membership.save(update_fields=["is_active"])
                outcome.turned_on += 1
                _audit_membership(request, membership, AuditAction.MEMBERSHIP_CHANGED, via=via)
            else:
                outcome.already += 1
                continue
            outcome.welcomed.setdefault(user.pk, []).append(row.role)
    return outcome


# --- Turning roles on and off ------------------------------------------------------


class CantTurnOff(Exception):
    pass


def set_active(membership: Membership, active: bool, *, request: HttpRequest) -> bool:
    """Turn one role on or off. False if it already was."""
    if not active and membership.user_id == request.user.pk and membership.role == Role.DIRECTOR:
        raise CantTurnOff(_("You can't turn off your own director role. Ask another director."))
    with transaction.atomic():
        locked = Membership.objects.select_for_update().get(pk=membership.pk)
        if locked.is_active == active:
            return False
        locked.is_active = active
        locked.save(update_fields=["is_active"])
        _audit_membership(request, locked, AuditAction.MEMBERSHIP_CHANGED, via="person_page")
    membership.is_active = active
    return True


def active_in_role(center: Center, role: Role, *, keep: Any) -> Any:
    """The active memberships a bulk turn-off would change: everyone in the role but
    the person doing it."""
    return Membership.objects.filter(center=center, role=role, is_active=True).exclude(user=keep)


def with_upcoming(center: Center, role: Role, memberships: Any) -> int | None:
    """How many of these people have an appointment still to come in the center: as a
    student in it, or as its tutor. None for front desk staff and directors, whose
    roles don't hold appointments."""
    if role not in (Role.STUDENT, Role.TUTOR):
        return None
    now = timezone.now()
    upcoming = Appointment.objects.filter(schedule__center=center, starts_at__gte=now).exclude(
        status=AppointmentStatus.CANCELED
    )
    people = memberships.values("user_id")
    busy: Any
    if role == Role.STUDENT:
        busy = Participant.objects.filter(
            appointment__in=upcoming, canceled_at__isnull=True, student_id__in=people
        ).values("student_id")
    else:
        busy = upcoming.filter(tutor_id__in=people).values("tutor_id")
    return int(memberships.filter(user_id__in=busy).count())


def turn_off_role(center: Center, role: Role, *, request: HttpRequest) -> int:
    """Turn off everyone's role but the requester's own. Returns how many changed."""
    with transaction.atomic():
        found = list(
            active_in_role(center, role, keep=request.user)
            .select_for_update()
            .select_related("center")
        )
        for membership in found:
            membership.is_active = False
            _audit_membership(
                request, membership, AuditAction.MEMBERSHIP_CHANGED, via="turn_off_all"
            )
        Membership.objects.filter(pk__in=[m.pk for m in found]).update(is_active=False)
        record(
            AuditAction.ROLES_TURNED_OFF,
            request=request,
            center=center,
            role=role,
            count=len(found),
        )
    return len(found)
