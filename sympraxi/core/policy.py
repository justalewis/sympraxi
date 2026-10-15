"""Every permission decision in Sympraxi goes through this module.

Views never check roles themselves. They ask `can(user, action, obj)`, or fetch
objects through `get_for_action()` / `centers_for()`, which apply the same rules
to querysets. Keeping the rules in one table makes them reviewable and lets
`tests/test_policy_matrix.py` check every role against every action.

Session notes are FERPA education records. Access must be limited to people with
a legitimate educational interest (34 CFR 99.31(a)(1)(ii)), which is why roles are
scoped to a center and why site administrators get only the operational actions in
`SITE_ADMIN_GRANTS`. Actions that read student records must never be added there.
"""

from enum import StrEnum
from typing import Any

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Model, QuerySet
from django.http import Http404

from sympraxi.centers.models import STAFF_ROLES, Center, Membership, Role

# `request.user`: a signed-in User or an AnonymousUser.
UserLike = Any


class Action(StrEnum):
    VIEW_CENTER = "center.view"
    MANAGE_CENTER = "center.manage"
    VIEW_MEMBERS = "center.view_members"
    MANAGE_MEMBERS = "center.manage_members"
    VIEW_AUDIT_LOG = "privacy.view_audit_log"


ROLE_GRANTS: dict[Role, frozenset[Action]] = {
    Role.STUDENT: frozenset({Action.VIEW_CENTER}),
    Role.TUTOR: frozenset({Action.VIEW_CENTER}),
    Role.FRONT_DESK: frozenset({Action.VIEW_CENTER, Action.VIEW_MEMBERS}),
    Role.DIRECTOR: frozenset(
        {
            Action.VIEW_CENTER,
            Action.MANAGE_CENTER,
            Action.VIEW_MEMBERS,
            Action.MANAGE_MEMBERS,
            Action.VIEW_AUDIT_LOG,
        }
    ),
}

# Site administrators keep the installation running: they create centers, appoint
# directors and investigate security events. They are not a back door to records.
SITE_ADMIN_GRANTS: frozenset[Action] = frozenset(
    {
        Action.VIEW_CENTER,
        Action.MANAGE_CENTER,
        Action.VIEW_MEMBERS,
        Action.MANAGE_MEMBERS,
        Action.VIEW_AUDIT_LOG,
    }
)


def _is_site_admin(user: UserLike) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


def roles_in(user: UserLike, center: Center) -> frozenset[Role]:
    """The user's active roles in a center, cached on the user object per request."""
    if not user.is_authenticated or not user.is_active:
        return frozenset()
    cache: dict[object, frozenset[Role]] = user.__dict__.setdefault("_sympraxi_roles", {})
    if center.pk not in cache:
        cache[center.pk] = frozenset(
            Role(r)
            for r in Membership.objects.filter(
                user=user, center=center, is_active=True
            ).values_list("role", flat=True)
        )
    return cache[center.pk]


def center_of(obj: Model) -> Center:
    """The center an object belongs to. Every protected object must have one."""
    if isinstance(obj, Center):
        return obj
    center = getattr(obj, "center", None)
    if isinstance(center, Center):
        return center
    raise TypeError(f"{type(obj).__name__} has no center; the policy cannot decide access to it")


def can(user: UserLike, action: Action, obj: Model) -> bool:
    if not user.is_authenticated or not user.is_active:
        return False
    center = center_of(obj)
    if _is_site_admin(user) and action in SITE_ADMIN_GRANTS:
        return True
    if not center.is_active:
        return False
    return any(action in ROLE_GRANTS[role] for role in roles_in(user, center))


def centers_for(user: UserLike, action: Action) -> QuerySet[Center]:
    """All centers where the user may perform the action."""
    if not user.is_authenticated or not user.is_active:
        return Center.objects.none()
    if _is_site_admin(user) and action in SITE_ADMIN_GRANTS:
        return Center.objects.all()
    roles = [role for role, actions in ROLE_GRANTS.items() if action in actions]
    return Center.objects.filter(
        is_active=True,
        memberships__user=user,
        memberships__is_active=True,
        memberships__role__in=roles,
    ).distinct()


def get_for_action[M: Model](user: UserLike, action: Action, queryset: QuerySet[M], **lookup) -> M:
    """Fetch one object the user may act on.

    People outside the object's center get a 404, so IDs and slugs can't be used to
    probe which records exist. Members of the center who lack the permission get a 403.
    """
    try:
        obj = queryset.get(**lookup)
    except ObjectDoesNotExist as exc:
        raise Http404 from exc
    if can(user, action, obj):
        return obj
    if roles_in(user, center_of(obj)) or _is_site_admin(user):
        raise PermissionDenied
    raise Http404


def is_staff_member(user: UserLike) -> bool:
    """Site admins and anyone with an active staff role in any center."""
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return Membership.objects.filter(user=user, is_active=True, role__in=STAFF_ROLES).exists()
