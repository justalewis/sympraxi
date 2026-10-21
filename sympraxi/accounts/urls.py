from django.urls import path

from sympraxi.accounts import cas_views, views

app_name = "accounts"

urlpatterns = [
    path("login/local/", views.LocalLoginView.as_view(), name="local_login"),
    path("profile/", views.profile, name="profile"),
    path("cas/login/", cas_views.cas_login, name="cas_login"),
    path("cas/return/", cas_views.cas_callback, name="cas_callback"),
    path("reminders/", views.reminders, name="reminders"),
]
