from django.urls import path

from sympraxi.integrations import api

app_name = "api"

# Mounted at /api/v1/centers/<slug>/. Authenticated by bearer token, not by session.
urlpatterns = [
    path("schedules/", api.schedules, name="schedules"),
    path("appointments/", api.appointments, name="appointments"),
    path("appointments/<uuid:appointment_id>/cancel/", api.appointment_cancel, name="cancel"),
    path("students/", api.students, name="students"),
    path("workshops/", api.workshops, name="workshops"),
]
