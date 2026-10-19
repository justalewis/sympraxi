from django.urls import path

from sympraxi.records import views

app_name = "records"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    # Writing records
    path("appointments/<uuid:appointment_id>/record/", views.record_start, name="start"),
    path("records/to-write/", views.to_write, name="to_write"),
    path("records/new/", views.standalone_start, name="new"),
    path("records/<uuid:record_id>/", views.record_detail, name="detail"),
    path("records/<uuid:record_id>/edit/", views.record_edit, name="edit"),
    path("records/<uuid:record_id>/amend/", views.record_amend, name="amend"),
    # Parts of the record form that htmx fetches
    path("records/resource-panel/", views.resource_panel, name="resource_panel"),
    path("records/attach/", views.attach_row, name="attach_row"),
    # A student's history, for staff, and the FERPA export
    path(
        "students/<uuid:membership_id>/writing/",
        views.student_history,
        name="student_history",
    ),
    path("students/<uuid:membership_id>/export/", views.export_page, name="export"),
    path("students/<uuid:membership_id>/export.json", views.export_json, name="export_json"),
    path("projects/<uuid:project_id>/edit/", views.project_edit, name="project_edit"),
    # The student's own pages
    path("my-writing/", views.my_writing, name="my_writing"),
    path("my-writing/<uuid:record_id>/", views.my_record, name="my_record"),
    # Setup, for directors
    path("records/settings/", views.record_settings, name="settings"),
    path("resources/", views.resource_list, name="resources"),
    path("resources/new/", views.resource_create, name="resource_create"),
    path("resources/<uuid:resource_id>/edit/", views.resource_edit, name="resource_edit"),
    path("resources/links/", views.resource_links, name="resource_links"),
    path("resources/favorites/", views.favorites_page, name="favorites"),
    path(
        "resources/<uuid:resource_id>/favorite/",
        views.favorite_toggle,
        name="favorite_toggle",
    ),
    path("resources/<uuid:resource_id>/check/", views.resource_check, name="resource_check"),
]
