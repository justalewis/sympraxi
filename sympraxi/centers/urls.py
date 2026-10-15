from django.urls import path

from sympraxi.centers import views

app_name = "centers"

urlpatterns = [
    path("<slug:slug>/", views.center_detail, name="detail"),
    path("<slug:slug>/members/", views.member_list, name="members"),
]
