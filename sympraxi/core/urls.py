from django.urls import path

from sympraxi.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("source/", views.source_code, name="source"),
]
