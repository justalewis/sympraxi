from allauth.account.decorators import secure_admin_login
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

# The admin uses the regular sign-in, so it gets rate limiting and two-factor login.
admin.site.login = secure_admin_login(admin.site.login)  # type: ignore[method-assign]
admin.site.site_header = f"{settings.SYMPRAXI_SITE_NAME} administration"
admin.site.site_title = settings.SYMPRAXI_SITE_NAME

urlpatterns = [
    path("", include("sympraxi.core.urls")),
    path("", include("sympraxi.privacy.urls")),
    path("centers/", include("sympraxi.centers.urls")),
    path("centers/<slug:slug>/", include("sympraxi.scheduling.urls")),
    path("centers/<slug:slug>/", include("sympraxi.records.urls")),
    path("accounts/", include("sympraxi.accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]
