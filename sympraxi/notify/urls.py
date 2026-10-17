from django.urls import path

from sympraxi.notify import views

app_name = "notify"

# Mounted at the site root. The feed's token is a secret: tests/test_url_inventory.py
# lists it among the token pages, and sympraxi/core/logs.py keeps it out of the logs.
urlpatterns = [
    path("calendar/<str:token>.ics", views.feed, name="feed"),
    path("accounts/calendar/", views.calendar, name="calendar"),
]
