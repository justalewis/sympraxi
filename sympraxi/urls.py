from allauth.account.decorators import secure_admin_login
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

# The admin uses the regular sign-in, so it gets rate limiting and two-factor login.
admin.site.login = secure_admin_login(admin.site.login)  # type: ignore[method-assign]
admin.site.site_header = f"{settings.SYMPRAXI_SITE_NAME} administration"
admin.site.site_title = settings.SYMPRAXI_SITE_NAME

# allauth looks here for the page to show when one of its rate limits is reached.
handler429 = "sympraxi.core.views.too_many_requests"

urlpatterns = [
    path("", include("sympraxi.core.urls")),
    path("", include("sympraxi.privacy.urls")),
    path("", include("sympraxi.notify.urls")),
    path("centers/", include("sympraxi.centers.urls")),
    path("centers/<slug:slug>/", include("sympraxi.scheduling.urls")),
    path("centers/<slug:slug>/", include("sympraxi.records.urls")),
    path("centers/<slug:slug>/", include("sympraxi.forms.urls")),
    path("centers/<slug:slug>/", include("sympraxi.reports.urls")),
    path("centers/<slug:slug>/", include("sympraxi.staff.urls")),
    path("centers/<slug:slug>/", include("sympraxi.notify.center_urls")),
    path("centers/<slug:slug>/", include("sympraxi.files.urls")),
    path("centers/<slug:slug>/", include("sympraxi.feedback.urls")),
    path("centers/<slug:slug>/", include("sympraxi.online.urls")),
    path("centers/<slug:slug>/", include("sympraxi.workshops.urls")),
    path("centers/<slug:slug>/", include("sympraxi.training.urls")),
    path("survey/", include("sympraxi.forms.survey_urls")),
    path("internal/collab/", include("sympraxi.online.internal_urls")),
    path("library/", include("sympraxi.records.library_urls")),
    path("accounts/", include("sympraxi.accounts.urls")),
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
]

# 403.html and 404.html are rendered by Django's own handlers. The 500 page
# leaves out the request's context (sympraxi/core/views.py).
handler500 = "sympraxi.core.views.server_error"
