from django.urls import path

from sympraxi.training import views

app_name = "training"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    path("training/", views.home, name="home"),
    path("training/modules/<uuid:module_id>/", views.module_page, name="module"),
    path("training/items/<uuid:item_id>/", views.item_page, name="item"),
    path("training/manage/", views.manage, name="manage"),
    path("training/manage/new/", views.module_create, name="module_create"),
    path("training/manage/modules/<uuid:module_id>/", views.module_edit, name="module_edit"),
    path("training/manage/items/<uuid:item_id>/", views.item_edit, name="item_edit"),
    path("training/onboarding/", views.onboarding, name="onboarding"),
]
