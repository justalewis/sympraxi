from django.urls import path

from sympraxi.scheduling import setup_views, views

app_name = "scheduling"

# Mounted under /centers/<slug>/. Every name here is classified in
# tests/test_url_inventory.py.
urlpatterns = [
    path("schedules/", views.schedule_list, name="schedules"),
    path("schedules/<uuid:schedule_id>/", views.open_times_page, name="open_times"),
    path("schedules/<uuid:schedule_id>/book/", views.book, name="book"),
    path("schedules/<uuid:schedule_id>/book-for/", views.staff_book, name="staff_book"),
    path("schedules/<uuid:schedule_id>/day/", views.day_sheet, name="day_sheet"),
    path("appointments/", views.my_appointments, name="my_appointments"),
    path("appointments/<uuid:appointment_id>/", views.appointment_detail, name="appointment"),
    path(
        "appointments/<uuid:appointment_id>/cancel/",
        views.appointment_cancel,
        name="appointment_cancel",
    ),
    path(
        "appointments/<uuid:appointment_id>/change/",
        views.appointment_change,
        name="appointment_change",
    ),
    path(
        "appointments/<uuid:appointment_id>/confirm/",
        views.appointment_confirm,
        name="appointment_confirm",
    ),
    path(
        "appointments/<uuid:appointment_id>/calendar.ics",
        views.appointment_ics,
        name="appointment_ics",
    ),
    path("tutoring/", views.my_tutoring, name="my_tutoring"),
    # Setup, for directors
    path("schedules/new/", setup_views.schedule_create, name="schedule_create"),
    path("schedules/<uuid:schedule_id>/manage/", setup_views.schedule_manage, name="manage"),
    path("schedules/<uuid:schedule_id>/edit/", setup_views.schedule_edit, name="schedule_edit"),
    path(
        "schedules/<uuid:schedule_id>/tutors/new/",
        setup_views.assignment_create,
        name="assignment_create",
    ),
    path("tutors/<uuid:assignment_id>/edit/", setup_views.assignment_edit, name="assignment_edit"),
    path("tutors/<uuid:assignment_id>/hours/", setup_views.hours_edit, name="hours"),
    path(
        "tutors/<uuid:assignment_id>/changes/new/",
        setup_views.exception_create,
        name="exception_create",
    ),
    path(
        "hours-changes/<uuid:exception_id>/delete/",
        setup_views.exception_delete,
        name="exception_delete",
    ),
    path("terms/", setup_views.term_list, name="terms"),
    path("terms/new/", setup_views.term_create, name="term_create"),
    path("terms/<uuid:term_id>/edit/", setup_views.term_edit, name="term_edit"),
    path("terms/<uuid:term_id>/closures/new/", setup_views.closure_create, name="closure_create"),
    path("closures/<uuid:closure_id>/delete/", setup_views.closure_delete, name="closure_delete"),
    path("rooms/", setup_views.room_list, name="rooms"),
    path("rooms/new/", setup_views.room_create, name="room_create"),
    path("rooms/<uuid:room_id>/edit/", setup_views.room_edit, name="room_edit"),
    path("exemptions/", setup_views.exemption_list, name="exemptions"),
    path("exemptions/new/", setup_views.exemption_create, name="exemption_create"),
    path(
        "exemptions/<uuid:exemption_id>/end/",
        setup_views.exemption_revoke,
        name="exemption_revoke",
    ),
]
