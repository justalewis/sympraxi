from django.urls import path

from sympraxi.integrations import views

app_name = "integrations"

urlpatterns = [
    path("integrations/", views.home, name="home"),
]
