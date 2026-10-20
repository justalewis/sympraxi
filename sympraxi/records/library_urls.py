from django.urls import path

from sympraxi.records import library_views

app_name = "library"

# Mounted at /library/. Classified in tests/test_url_inventory.py (any signed-in person).
urlpatterns = [
    path("<slug:item_id>/", library_views.item, name="item"),
]
