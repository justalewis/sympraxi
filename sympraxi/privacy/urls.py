from django.urls import path

from sympraxi.privacy import disclosure_views, views

app_name = "privacy"

urlpatterns = [
    path("centers/<slug:slug>/audit/", views.audit_log, name="audit_log"),
    path(
        "centers/<slug:slug>/students/<uuid:membership_id>/disclosures/",
        disclosure_views.disclosures,
        name="disclosures",
    ),
    path(
        "centers/<slug:slug>/my-writing/disclosures/",
        disclosure_views.my_disclosures,
        name="my_disclosures",
    ),
]
