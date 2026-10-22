from django.urls import path

from sympraxi.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("healthz", views.healthz, name="healthz"),
    path("source/", views.source_code, name="source"),
    path("demo/", views.demo_page, name="demo"),
    path("demo/sign-in/", views.demo_sign_in, name="demo_sign_in"),
    path("demo/videos/<slug:slug>/", views.demo_video, name="demo_video"),
    path("language/<str:code>/", views.set_language, name="language"),
]
