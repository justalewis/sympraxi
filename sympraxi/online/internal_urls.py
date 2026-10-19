from django.urls import path

from sympraxi.online import room_views

app_name = "collab"

# The editor service's address, for the service only: a shared secret, no session. Caddy
# answers 404 to anything under /internal/ from outside. Classified in
# tests/test_url_inventory.py.
urlpatterns = [
    path("load/", room_views.internal_load, name="load"),
    path("store/", room_views.internal_store, name="store"),
]
