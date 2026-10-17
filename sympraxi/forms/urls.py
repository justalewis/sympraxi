from django.urls import path

from sympraxi.forms import views

app_name = "forms"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    # The builder, for directors
    path("forms/", views.form_list, name="list"),
    path("forms/new/", views.form_create, name="create"),
    path("forms/<uuid:form_id>/", views.form_detail, name="detail"),
    path("forms/<uuid:form_id>/questions/new/", views.question_create, name="question_create"),
    path("forms/<uuid:form_id>/preview/", views.preview, name="preview"),
    path("forms/<uuid:form_id>/publish/", views.publish, name="publish"),
    path("form-questions/<uuid:question_id>/edit/", views.question_edit, name="question_edit"),
    path(
        "form-questions/<uuid:question_id>/remove/",
        views.question_remove,
        name="question_remove",
    ),
    # Surveys, for directors
    path("surveys/settings/", views.survey_settings, name="survey_settings"),
    path("surveys/results/", views.survey_results, name="survey_results"),
    # Answering, for students
    path("registration/", views.my_registration, name="my_registration"),
    path("appointments/<uuid:appointment_id>/intake/", views.intake, name="intake"),
    # Reading, for staff
    path(
        "students/<uuid:membership_id>/registration/",
        views.student_registration,
        name="student_registration",
    ),
]
