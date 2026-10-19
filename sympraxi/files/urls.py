from django.urls import path

from sympraxi.files import views

app_name = "files"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    path("appointments/<uuid:appointment_id>/files/new/", views.upload, name="upload"),
    path("files/<uuid:file_id>/", views.download, name="download"),
    path("files/<uuid:file_id>/remove/", views.remove, name="remove"),
]
