from django.urls import path

from sympraxi.alerts import views

app_name = "alerts"

urlpatterns = [
    path("alerts/", views.alert_list, name="list"),
    path("alerts/mine/", views.my_alerts, name="mine"),
    path("alerts/<uuid:alert_id>/", views.alert_detail, name="detail"),
    path("students/<uuid:membership_id>/alert/", views.raise_alert, name="raise"),
    path("appointments/<uuid:appointment_id>/share/", views.share_visit, name="share"),
    path("shared-visits/", views.my_shares, name="shares"),
]
