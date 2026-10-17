from django.urls import path

from sympraxi.notify import center_views as views

app_name = "news"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    path("announcements/", views.announcements, name="announcements"),
    path("announcements/new/", views.announcement_create, name="announcement_create"),
    path(
        "announcements/<uuid:announcement_id>/",
        views.announcement_edit,
        name="announcement_edit",
    ),
    path("email/", views.mass_email, name="mass_email"),
    path("email/sent/", views.mass_email_log, name="mass_email_log"),
    path("email/sent/<uuid:mass_email_id>/", views.mass_email_detail, name="mass_email_detail"),
]
