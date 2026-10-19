from django.urls import path

from sympraxi.feedback import views

app_name = "feedback"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    # Students
    path("feedback/send/", views.submit, name="submit"),
    path("feedback/mine/", views.mine, name="mine"),
    # One submission
    path("feedback/<uuid:submission_id>/", views.detail, name="detail"),
    path("feedback/<uuid:submission_id>/cancel/", views.cancel, name="cancel"),
    path("feedback/<uuid:submission_id>/files/new/", views.add_file, name="add_file"),
    path("feedback/files/<uuid:file_id>/", views.download, name="download"),
    path("feedback/files/<uuid:file_id>/remove/", views.remove_file, name="remove_file"),
    # Tutors and directors
    path("feedback/", views.queue, name="queue"),
    path("feedback/<uuid:submission_id>/claim/", views.claim, name="claim"),
    path("feedback/<uuid:submission_id>/assign/", views.assign, name="assign"),
    path("feedback/settings/", views.queue_settings, name="settings"),
    path("feedback/turnaround/", views.turnaround, name="turnaround"),
]
