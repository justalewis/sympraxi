from django.urls import path

from sympraxi.alerts import views

app_name = "visit"

urlpatterns = [path("<str:token>/", views.confirmation, name="confirmation")]
