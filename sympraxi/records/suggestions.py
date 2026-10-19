"""Resources to suggest for a record's topics, and catalog search.

Suggestions follow the concerns a tutor tagged. Stages and tutor actions only
break ties, because nearly every resource fits "drafting" or "revising". A
resource scores 3 for each concern it shares with the record. One that shares
none scores 2 if it has a more specific term under one of them (record:
`citation`, resource: `citation:apa`), or else 1 if it has a related term: the
parent, or another term under the same parent (record: `citation:apa`,
resource: `citation` or `citation:mla`). Each matching stage or action adds 1.

A resource with no concern points isn't suggested, and neither is one the link
checker found broken. Ties go to the center's own
links, then to the title.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from django.db.models import Q

from sympraxi.centers.models import Center
from sympraxi.records.models import Axis, LinkState, Resource, Topic

LIMIT = 8
SEARCH_LIMIT = 20


@dataclass
class Suggestion:
    resource: Resource
    score: int
    matched: list[Topic]


def suggest(
    center: Center,
    topics: Iterable[Topic],
    *,
    exclude: Iterable[object] = (),
    limit: int = LIMIT,
) -> list[Suggestion]:
    topics = list(topics)
    concerns = [t for t in topics if t.axis == Axis.CONCERN]
    if not concerns:
        return []
    exact = {t.slug for t in concerns}
    families = {t.slug.split(":", 1)[0] for t in concerns}
    others = {t.slug for t in topics if t.axis != Axis.CONCERN}
    candidates = (
        Resource.objects.available_to(center)
        .filter(Q(topics__slug__in=exact | families) | Q(topics__parent__slug__in=families))
        .exclude(pk__in=list(exclude))
        .exclude(link_state=LinkState.BROKEN)
        .distinct()
        .prefetch_related("topics")
    )
    results = []
    for resource in candidates:
        shared, narrower, related, extra = [], [], [], []
        for topic in resource.topics.all():
            family = topic.slug.split(":", 1)[0]
            if topic.axis != Axis.CONCERN:
                if topic.slug in others:
                    extra.append(topic)
            elif topic.slug in exact:
                shared.append(topic)
            elif family in exact:
                narrower.append(topic)
            elif family in families:
                related.append(topic)
        if shared:
            concern_score, matched = 3 * len(shared), shared
        elif narrower:
            concern_score, matched = 2, narrower
        elif related:
            concern_score, matched = 1, related
        else:
            continue
        results.append(
            Suggestion(
                resource=resource,
                score=concern_score + len(extra),
                matched=matched + extra,
            )
        )
    results.sort(key=lambda s: (-s.score, not s.resource.is_local, s.resource.title.lower()))
    return results[:limit]


def search(
    center: Center, query: str, *, exclude: Iterable[object] = (), limit: int = SEARCH_LIMIT
) -> list[Resource]:
    """Resources whose title, summary or publisher contain every word of the query."""
    words = query.split()[:8]
    if not words:
        return []
    found = (
        Resource.objects.available_to(center)
        .exclude(pk__in=list(exclude))
        .exclude(link_state=LinkState.BROKEN)
    )
    for word in words:
        found = found.filter(
            Q(title__icontains=word) | Q(summary__icontains=word) | Q(publisher__icontains=word)
        )
    return list(found.order_by("title")[:limit])
