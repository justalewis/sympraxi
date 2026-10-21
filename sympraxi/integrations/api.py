"""The JSON API, under /api/v1/centers/<slug>/.

Every call carries `Authorization: Bearer <token>`. A token belongs to one center and
acts as the director who made it, limited to its scopes; if that director no longer
has access, the token stops working. Lists are paged with `limit` (up to 500) and
`after`, a cursor taken from the previous answer's `next`. Errors look like
`{"error": {"code": "...", "message": "..."}}`.

Answers name people only to tokens with the scope that allows it. Writes go through the
same booking rules the site uses, so limits, blocks and double-booking checks apply,
and each write is in the audit log with the token that made it.
"""

import json
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Any
from uuid import UUID

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.core.policy import Action, can
from sympraxi.integrations import tokens
from sympraxi.integrations.models import ApiToken
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling import services
from sympraxi.scheduling.models import (
    Appointment,
    Modality,
    Schedule,
    TutorAssignment,
)
from sympraxi.workshops.models import Workshop

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
# Calls a token may make in a minute.
RATE_LIMIT = 600


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _error(status: int, code: str, message: str) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _bearer(request: HttpRequest) -> str:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    kind, _, value = header.partition(" ")
    return value.strip() if kind.lower() == "bearer" else ""


def authenticate(request: HttpRequest, slug: str, scope: str) -> ApiToken:
    row = tokens.find(_bearer(request))
    if row is None:
        raise ApiError(401, "invalid_token", "The token is missing, wrong, expired or revoked.")
    if row.center.slug != slug or not row.center.is_active:
        # The same answer as a wrong token, so a token can't be used to find centers.
        raise ApiError(401, "invalid_token", "The token is missing, wrong, expired or revoked.")
    if scope not in row.scopes:
        raise ApiError(403, "missing_scope", f"This token needs the {scope} scope.")
    owner = row.created_by
    if not owner.is_active or not can(owner, Action.MANAGE_INTEGRATIONS, row.center):
        raise ApiError(
            403, "owner_lost_access", "The director who made this token no longer has access."
        )
    key = f"api-rate-{row.pk}-{int(timezone.now().timestamp() // 60)}"
    cache.add(key, 0, 120)
    if cache.incr(key) > RATE_LIMIT:
        raise ApiError(429, "rate_limited", "Too many requests. Wait a minute.")
    tokens.touch(row)
    return row


def endpoint(scope: str, methods: tuple[str, ...] = ("GET",)) -> Callable[..., Any]:
    def wrap(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @csrf_exempt
        @wraps(view)
        def inner(request: HttpRequest, slug: str, *args: Any, **kwargs: Any) -> HttpResponse:
            if request.method not in methods:
                response = _error(405, "method_not_allowed", "That method isn't allowed here.")
                response["Allow"] = ", ".join(methods)
                return response
            try:
                token = authenticate(request, slug, scope)
                return view(request, token, *args, **kwargs)
            except ApiError as error:
                return _error(error.status, error.code, error.message)

        return inner

    return wrap


def _limit(request: HttpRequest) -> int:
    try:
        return max(1, min(int(request.GET.get("limit", DEFAULT_LIMIT)), MAX_LIMIT))
    except ValueError:
        raise ApiError(400, "bad_limit", "limit must be a whole number.") from None


def _instant(request: HttpRequest, name: str) -> datetime | None:
    raw = request.GET.get(name)
    if not raw:
        return None
    found = parse_datetime(raw)
    if found is None or found.tzinfo is None:
        raise ApiError(
            400,
            "bad_time",
            f"{name} must be a time with an offset, like 2026-10-21T09:00:00-04:00.",
        )
    return found


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise ApiError(400, "bad_json", "The body isn't valid JSON.") from None
    if not isinstance(data, dict):
        raise ApiError(400, "bad_json", "The body must be a JSON object.")
    return data


def _page(items: list[Any], limit: int, cursor: Callable[[Any], str]) -> dict[str, Any]:
    more = len(items) > limit
    items = items[:limit]
    return {"next": cursor(items[-1]) if more and items else None, "items": items}


# --- Reading -----------------------------------------------------------------------------------


def _appointment(a: Appointment, *, with_people: bool) -> dict[str, Any]:
    data = {
        "id": str(a.pk),
        "schedule": str(a.schedule_id),
        "assignment": str(a.assignment_id),
        "tutor": {"id": str(a.tutor_id), "name": a.tutor.display_name},
        "starts_at": a.starts_at.isoformat(),
        "ends_at": a.ends_at.isoformat(),
        "modality": a.modality,
        "status": a.status,
        "walk_in": a.is_walk_in,
        "placeholder": a.is_placeholder,
        "tentative": a.is_tentative,
        "capacity": a.capacity,
    }
    if with_people:
        data["students"] = [
            {
                "id": str(p.student_id),
                "name": p.student.display_name,
                "email": p.student.email,
                "student_id": p.student.student_id or "",
                "attendance": p.attendance,
                "late_cancel": p.late_cancel,
            }
            for p in a.participants.all()
            if p.canceled_at is None
        ]
    return data


@endpoint("schedules:read")
def schedules(request: HttpRequest, token: ApiToken) -> JsonResponse:
    rows = Schedule.objects.filter(center=token.center).prefetch_related("assignments__tutor")
    items = [
        {
            "id": str(s.pk),
            "name": s.name,
            "starts_on": s.starts_on.isoformat(),
            "ends_on": s.ends_on.isoformat(),
            "visibility": s.visibility,
            "slot_minutes": s.slot_minutes,
            "tutors": [
                {
                    "assignment": str(a.pk),
                    "name": a.tutor.display_name,
                    "lengths": a.lengths(),
                    "modalities": a.modalities,
                    "active": a.is_active,
                }
                for a in s.assignments.all()
            ],
        }
        for s in rows
    ]
    return JsonResponse({"items": items, "next": None})


@endpoint("appointments:read", ("GET", "POST"))
def appointments(request: HttpRequest, token: ApiToken) -> JsonResponse:
    if request.method == "POST":
        return _book(request, token)
    limit = _limit(request)
    rows = (
        Appointment.objects.filter(schedule__center=token.center)
        .select_related("tutor")
        .prefetch_related("participants__student")
        .order_by("starts_at", "id")
    )
    start, end = _instant(request, "from"), _instant(request, "to")
    if start:
        rows = rows.filter(starts_at__gte=start)
    if end:
        rows = rows.filter(starts_at__lt=end)
    if status := request.GET.get("status"):
        rows = rows.filter(status=status)
    if after := request.GET.get("after"):
        when, _, ident = after.partition("|")
        cursor = parse_datetime(when)
        if cursor is None or not ident:
            raise ApiError(400, "bad_cursor", "after isn't a cursor from a previous answer.")
        rows = rows.filter(starts_at__gt=cursor) | rows.filter(
            starts_at=cursor, id__gt=_uuid(ident)
        )
        rows = rows.order_by("starts_at", "id")
    found = list(rows[: limit + 1])
    page = _page(
        [_appointment(a, with_people=True) for a in found],
        limit,
        lambda item: f"{item['starts_at']}|{item['id']}",
    )
    return JsonResponse(page)


def _uuid(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        raise ApiError(400, "bad_id", "That isn't a valid id.") from None


def _student(center: Center, email: str) -> User:
    user = User.objects.filter(
        email__iexact=email.strip(),
        is_active=True,
        memberships__center=center,
        memberships__role=Role.STUDENT,
        memberships__is_active=True,
    ).first()
    if user is None:
        raise ApiError(404, "student_not_found", "No student in this center has that email.")
    return user


def _book(request: HttpRequest, token: ApiToken) -> JsonResponse:
    if "appointments:write" not in token.scopes:
        raise ApiError(403, "missing_scope", "This token needs the appointments:write scope.")
    body = _body(request)
    try:
        assignment = TutorAssignment.objects.select_related("schedule__center").get(
            pk=_uuid(str(body.get("assignment", ""))), schedule__center=token.center
        )
    except TutorAssignment.DoesNotExist:
        raise ApiError(
            404, "assignment_not_found", "No such tutor on this center's schedules."
        ) from None
    starts = parse_datetime(str(body.get("starts_at", "")))
    if starts is None or starts.tzinfo is None:
        raise ApiError(400, "bad_time", "starts_at must be a time with an offset.")
    try:
        minutes = int(body.get("minutes", 0))
    except (TypeError, ValueError):
        raise ApiError(400, "bad_minutes", "minutes must be a whole number.") from None
    modality = str(body.get("modality", Modality.IN_PERSON))
    if modality not in Modality.values:
        raise ApiError(400, "bad_modality", "modality must be in_person or online.")
    student = _student(token.center, str(body.get("student_email", "")))
    try:
        appointment = services.book(
            actor=token.created_by,
            assignment=assignment,
            starts_at=starts,
            minutes=minutes,
            modality=modality,
            students=[student],
            as_staff=True,
        )
    except services.BookingError as problem:
        status = 409 if problem.code in ("slot_taken", "full") else 422
        raise ApiError(status, problem.code, str(problem)) from None
    _audit_write(token, "appointment booked", appointment)
    appointment = (
        Appointment.objects.select_related("tutor")
        .prefetch_related("participants__student")
        .get(pk=appointment.pk)
    )
    return JsonResponse(_appointment(appointment, with_people=True), status=201)


def _audit_write(token: ApiToken, what: str, target: Any) -> None:
    record(
        AuditAction.API_WRITE,
        actor=token.created_by,
        actor_label=f"API token {token.prefix}",
        center=token.center,
        target=target,
        what=what,
        token=str(token.pk),
    )


@endpoint("appointments:write", ("POST",))
def appointment_cancel(request: HttpRequest, token: ApiToken, appointment_id: UUID) -> JsonResponse:
    found = Appointment.objects.filter(schedule__center=token.center, pk=appointment_id).first()
    if found is None:
        raise ApiError(404, "appointment_not_found", "No such appointment in this center.")
    try:
        services.cancel(actor=token.created_by, appointment=found, as_staff=True)
    except services.BookingError as problem:
        raise ApiError(422, problem.code, str(problem)) from None
    _audit_write(token, "appointment canceled", found)
    found.refresh_from_db()
    found = (
        Appointment.objects.select_related("tutor")
        .prefetch_related("participants__student")
        .get(pk=found.pk)
    )
    return JsonResponse(_appointment(found, with_people=True))


@endpoint("students:read")
def students(request: HttpRequest, token: ApiToken) -> JsonResponse:
    limit = _limit(request)
    rows = (
        Membership.objects.filter(center=token.center, role=Role.STUDENT)
        .select_related("user")
        .order_by("user__email", "id")
    )
    if after := request.GET.get("after"):
        rows = rows.filter(user__email__gt=after)
    found = list(rows[: limit + 1])
    items = [
        {
            "id": str(m.user_id),
            "name": m.user.full_name,
            "preferred_name": m.user.preferred_name,
            "email": m.user.email,
            "student_id": m.user.student_id or "",
            "active": m.is_active and m.user.is_active,
        }
        for m in found
    ]
    return JsonResponse(_page(items, limit, lambda item: item["email"]))


@endpoint("workshops:read")
def workshops(request: HttpRequest, token: ApiToken) -> JsonResponse:
    limit = _limit(request)
    rows = (
        Workshop.objects.filter(center=token.center)
        .prefetch_related("registrations")
        .order_by("starts_at", "id")
    )
    if after := request.GET.get("after"):
        when, _, ident = after.partition("|")
        cursor = parse_datetime(when)
        if cursor is None or not ident:
            raise ApiError(400, "bad_cursor", "after isn't a cursor from a previous answer.")
        rows = (
            rows.filter(starts_at__gt=cursor) | rows.filter(starts_at=cursor, id__gt=_uuid(ident))
        ).order_by("starts_at", "id")
    found = list(rows[: limit + 1])
    items = [
        {
            "id": str(w.pk),
            "title": w.title,
            "starts_at": w.starts_at.isoformat(),
            "ends_at": w.ends_at.isoformat(),
            "location": w.location,
            "capacity": w.capacity,
            "status": w.status,
            "registered": sum(1 for r in w.registrations.all() if r.canceled_at is None),
            "attended": sum(1 for r in w.registrations.all() if r.attendance == "attended"),
        }
        for w in found
    ]
    return JsonResponse(_page(items, limit, lambda item: f"{item['starts_at']}|{item['id']}"))
