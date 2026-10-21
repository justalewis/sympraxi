from django.urls import path

from sympraxi.workshops import views

app_name = "workshops"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    path("workshops/", views.workshop_list, name="list"),
    path("workshops/new/", views.workshop_create, name="create"),
    path("workshops/<uuid:workshop_id>/", views.workshop_detail, name="detail"),
    path("workshops/<uuid:workshop_id>/edit/", views.workshop_edit, name="edit"),
    path("workshops/<uuid:workshop_id>/register/", views.register, name="register"),
    path("workshops/<uuid:workshop_id>/roster/", views.workshop_roster, name="roster"),
]
