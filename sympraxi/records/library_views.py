"""A page for one item of the content pack: its credit, then its text."""

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.safestring import mark_safe

from sympraxi.records import contentpack
from sympraxi.records.models import ContentItem


@login_required
def item(request: HttpRequest, item_id: str) -> HttpResponse:
    found = ContentItem.objects.filter(pk=item_id, is_active=True).first()
    if found is None:
        raise Http404
    context = {
        "item": found,
        # Cleaned when loaded, and again here, so nothing odd in the database reaches a page.
        "body": mark_safe(contentpack.clean(found.body)),  # noqa: S308
    }
    return render(request, "records/library_item.html", context)
