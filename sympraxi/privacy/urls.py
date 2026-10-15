from django.urls import path

from sympraxi.privacy import views

app_name = "privacy"

urlpatterns = [
    path("centers/<slug:slug>/audit/", views.audit_log, name="audit_log"),
]
