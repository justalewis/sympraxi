"""A tutor's favorite resources.

They show first in the resource panel on the record form, the ones that fit the
topics just tagged ahead of the rest, and on a page of their own where a tutor adds
and removes them. A resource the center no longer offers, or one that isn't
available in this center, is left out without being forgotten.
"""

from collections.abc import Iterable
from typing import Any

from django.db import IntegrityError, transaction

from sympraxi.centers.models import Center
from sympraxi.records.models import Axis, LinkState, Resource, ResourceFavorite, Topic


def favorites_for(user: Any, center: Center) -> list[Resource]:
    """The user's favorites that can be attached in this center, by title."""
    return list(
        Resource.objects.available_to(center)
        .filter(favorites__user=user)
        .exclude(link_state=LinkState.BROKEN)
        .prefetch_related("topics")
        .order_by("title")
    )


def ids_for(user: Any, center: Center) -> set[object]:
    """The ids of the user's favorites here, for marking them in lists."""
    return set(
        ResourceFavorite.objects.filter(
            user=user, resource__in=Resource.objects.available_to(center)
        ).values_list("resource_id", flat=True)
    )


def for_topics(favorites: Iterable[Resource], topics: Iterable[Topic]) -> list[Resource]:
    """Favorites that share a concern with the topics, then the rest, each by title."""
    concerns = {t.slug.split(":", 1)[0] for t in topics if t.axis == Axis.CONCERN}
    if not concerns:
        return list(favorites)
    fits: list[Resource] = []
    others: list[Resource] = []
    for resource in favorites:
        families = {
            t.slug.split(":", 1)[0] for t in resource.topics.all() if t.axis == Axis.CONCERN
        }
        (fits if families & concerns else others).append(resource)
    return fits + others


def is_favorite(user: Any, resource: Resource) -> bool:
    return ResourceFavorite.objects.filter(user=user, resource=resource).exists()


def toggle(user: Any, resource: Resource) -> bool:
    """Add the resource to the user's favorites, or take it off. Returns whether it is one now."""
    removed, _details = ResourceFavorite.objects.filter(user=user, resource=resource).delete()
    if removed:
        return False
    try:
        with transaction.atomic():
            ResourceFavorite.objects.create(user=user, resource=resource)
    except IntegrityError:
        pass  # a second click got there first
    return True
