from django.urls import path

from sympraxi.staff import kiosk_views, views

app_name = "staff"

urlpatterns = [
    path("time-clock/", views.clock, name="clock"),
    path("time-clock/entries/", views.entries, name="entries"),
    path("time-clock/approvals/", views.approvals, name="approvals"),
    path("kiosk/", kiosk_views.kiosk_page, name="kiosk"),
    path("waiting-room/", kiosk_views.waiting_room, name="waiting_room"),
    path("time-clock/settings/", views.settings, name="settings"),
    path("time-clock/entries/new/", views.entry_add, name="entry_add"),
    path("time-clock/entries/<uuid:entry_id>/", views.entry_edit, name="entry_edit"),
]
