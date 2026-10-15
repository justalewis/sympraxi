from django.conf import settings
from django.http import HttpRequest


def site(request: HttpRequest) -> dict[str, object]:
    return {"SITE_NAME": settings.SYMPRAXI_SITE_NAME}
