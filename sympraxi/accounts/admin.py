from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.models import User
from sympraxi.centers.admin import AuditedMembershipFormsetMixin, MembershipInline


@admin.register(User)
class UserAdmin(AuditedMembershipFormsetMixin, DjangoUserAdmin):
    ordering = ["full_name"]
    list_display = ["full_name", "preferred_name", "email", "is_active", "is_superuser"]
    list_filter = ["is_active", "is_superuser"]
    search_fields = ["full_name", "preferred_name", "email", "student_id"]
    inlines = [MembershipInline]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Person"), {"fields": ("full_name", "preferred_name", "pronouns", "student_id")}),
        (
            _("Access"),
            {
                "fields": ("is_active", "is_staff", "is_superuser"),
                "description": _(
                    "Site administrators (superusers) run the installation. Center roles "
                    "are set as memberships below."
                ),
            },
        ),
        (_("Dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "password1", "password2"),
            },
        ),
    )
    readonly_fields = ["last_login", "date_joined"]
