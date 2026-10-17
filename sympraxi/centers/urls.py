from django.urls import path

from sympraxi.centers import people_views, views

app_name = "centers"

urlpatterns = [
    path("<slug:slug>/", views.center_detail, name="detail"),
    path("<slug:slug>/members/", views.member_list, name="members"),
    path("<slug:slug>/members/add/", people_views.person_add, name="person_add"),
    path("<slug:slug>/members/import/", people_views.people_import, name="import"),
    path("<slug:slug>/members/turn-off/", people_views.roles_off, name="roles_off"),
    path("<slug:slug>/members/<uuid:membership_id>/", people_views.person, name="person"),
]
