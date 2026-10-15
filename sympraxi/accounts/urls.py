from django.urls import path

from sympraxi.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/local/", views.LocalLoginView.as_view(), name="local_login"),
    path("reminders/", views.reminders, name="reminders"),
]
