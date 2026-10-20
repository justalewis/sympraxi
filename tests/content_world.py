"""A content-pack item for page tests, made directly rather than through the loader."""

from sympraxi.records.models import ContentItem, ContentTier, LinkCheck, Resource


def make_content_item(item_id: str = "thesis-basics") -> ContentItem:
    resource = Resource.objects.create(
        catalog_id=f"pack:{item_id}",
        title="Thesis basics",
        url=f"/library/{item_id}/",
        publisher="A Guide to Writing",
        license="CC-BY-4.0",
        summary="How to state a claim a reader can argue with.",
        link_check=LinkCheck.OFF,
    )
    return ContentItem.objects.create(
        id=item_id,
        resource=resource,
        pack="guide",
        tier=ContentTier.CC_BY,
        title="Thesis basics",
        author="Pat Author",
        source_title="A Guide to Writing",
        source_url="https://example.org/guide/thesis",
        license="CC-BY-4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        changes="none",
        body="<h2>What a thesis does</h2><p>A thesis is a claim a reader can argue with.</p>",
    )
