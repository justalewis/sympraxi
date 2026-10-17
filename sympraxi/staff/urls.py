from django.urls import path

from sympraxi.staff import views

app_name = "staff"

urlpatterns = [
    path("time-clock/", views.clock, name="clock"),
    path("time-clock/entries/", views.entries, name="entries"),
    path("time-clock/entries/new/", views.entry_add, name="entry_add"),
    path("time-clock/entries/<uuid:entry_id>/", views.entry_edit, name="entry_edit"),
]
