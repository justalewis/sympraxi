from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from sympraxi.accounts.models import User
from sympraxi.centers.admin import AuditedMembershipFormsetMixin, MembershipInline

# SSO providers come from the environment (settings.SYMPRAXI_SSO_PROVIDERS), so the
# admin offers no place to store a client secret. Tokens aren't stored at all.
# Linked accounts are changed only through the account page, which is audited.
for model in (SocialApp, SocialToken, SocialAccount):
    admin.site.unregister(model)


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
