from django.urls import path

from sympraxi.training import onboarding_views, views

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
    path("training/cohorts/", onboarding_views.cohorts, name="cohorts"),
    path("training/cohorts/<uuid:cohort_id>/", onboarding_views.cohort_detail, name="cohort"),
    path("training/checklists/", onboarding_views.templates, name="templates"),
    path(
        "training/checklists/<uuid:template_id>/", onboarding_views.template_edit, name="template"
    ),
    path("training/mentees/", onboarding_views.mentees, name="mentees"),
    path("training/mentees/<uuid:member_id>/", onboarding_views.mentee, name="mentee"),
    path(
        "training/observations/<uuid:observation_id>/",
        onboarding_views.observation,
        name="observation",
    ),
]
