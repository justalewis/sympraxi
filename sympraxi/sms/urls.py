from django.urls import path

from sympraxi.sms import views

app_name = "sms"

# settings: signed-in people turn their own text reminders on and off.
# inbound: the provider posts replies (STOP, HELP) here, signed; it takes no session.
urlpatterns = [
    path("settings/", views.sms_settings, name="settings"),
    path("inbound/", views.inbound, name="inbound"),
]
