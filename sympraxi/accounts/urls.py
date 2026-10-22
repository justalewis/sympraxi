from django.urls import path

from sympraxi.accounts import cas_views, saml_views, views

app_name = "accounts"

urlpatterns = [
    path("login/local/", views.LocalLoginView.as_view(), name="local_login"),
    path("profile/", views.profile, name="profile"),
    path("cas/login/", cas_views.cas_login, name="cas_login"),
    path("cas/return/", cas_views.cas_callback, name="cas_callback"),
    path("saml/login/", saml_views.saml_login, name="saml_login"),
    path("saml/acs/", saml_views.saml_acs, name="saml_acs"),
    path("saml/metadata/", saml_views.saml_metadata, name="saml_metadata"),
    path("reminders/", views.reminders, name="reminders"),
]
