from django.urls import path

from sympraxi.reports import views

app_name = "reports"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py. Each report downloads from its own address with
# ?format=csv, xlsx or json.
urlpatterns = [
    path("reports/statistics/", views.statistics_page, name="statistics"),
    path("reports/hours/", views.utilization_page, name="utilization"),
    path("reports/resources/", views.resources_page, name="resources"),
    path("reports/by-course/", views.by_course_page, name="by_course"),
    path("reports/questions/", views.report_questions, name="report_questions"),
    path("reports/appointments/", views.appointment_list, name="appointment_list"),
    path("reports/students/", views.student_list, name="student_list"),
    path("reports/export/", views.center_export_page, name="center_export"),
    path(
        "reports/export/<uuid:export_id>/download/",
        views.center_export_download,
        name="center_export_download",
    ),
]
