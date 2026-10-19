from django.urls import path

from sympraxi.online import room_views, views

app_name = "online"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    path("appointments/<uuid:appointment_id>/join/", views.join, name="join"),
    path("appointments/<uuid:appointment_id>/room/", room_views.room_page, name="room"),
    path(
        "appointments/<uuid:appointment_id>/room/versions/",
        room_views.room_versions,
        name="room_versions",
    ),
    path("video/", views.video_settings, name="settings"),
]
