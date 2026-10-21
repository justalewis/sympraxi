from django.urls import path

from sympraxi.lti import views

app_name = "lti"

urlpatterns = [
    path("login/", views.login_initiation, name="login"),
    path("launch/", views.launch, name="launch"),
    path("config.json", views.config, name="config"),
]
