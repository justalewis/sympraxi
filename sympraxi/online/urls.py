from django.urls import path

from sympraxi.online import views

app_name = "online"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    path("appointments/<uuid:appointment_id>/join/", views.join, name="join"),
    path("video/", views.video_settings, name="settings"),
]
