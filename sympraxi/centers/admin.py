from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from sympraxi.centers.models import Center, Membership
from sympraxi.privacy.audit import AuditAction, record


def _audit_membership(request: HttpRequest, membership: Membership, action: AuditAction) -> None:
    record(
        action,
        request=request,
        center=membership.center,
        target=membership,
        member=str(membership.user_id),
        role=membership.role,
        active=membership.is_active,
    )


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]
    fields = ["user", "center", "role", "is_active"]


class AuditedMembershipFormsetMixin:
    """Record every membership added, changed or removed through an admin inline."""

    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        if formset.model is not Membership:
            super().save_formset(request, form, formset, change)  # type: ignore[misc]
            return
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            _audit_membership(request, obj, AuditAction.MEMBERSHIP_REMOVED)
            obj.delete()
        for obj in instances:
            adding = obj._state.adding
            obj.save()
            _audit_membership(
                request,
                obj,
                AuditAction.MEMBERSHIP_ADDED if adding else AuditAction.MEMBERSHIP_CHANGED,
            )
        formset.save_m2m()


@admin.register(Center)
class CenterAdmin(AuditedMembershipFormsetMixin, admin.ModelAdmin):
    list_display = ["name", "slug", "time_zone", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "center", "role", "is_active", "created_at"]
    list_filter = ["center", "role", "is_active"]
    search_fields = ["user__full_name", "user__email", "center__name"]
    autocomplete_fields = ["user", "center"]

    def save_model(self, request: HttpRequest, obj: Membership, form: Any, change: bool) -> None:
        super().save_model(request, obj, form, change)
        _audit_membership(
            request,
            obj,
            AuditAction.MEMBERSHIP_CHANGED if change else AuditAction.MEMBERSHIP_ADDED,
        )

    def delete_model(self, request: HttpRequest, obj: Membership) -> None:
        _audit_membership(request, obj, AuditAction.MEMBERSHIP_REMOVED)
        super().delete_model(request, obj)

    def delete_queryset(self, request: HttpRequest, queryset: Any) -> None:
        for obj in queryset.select_related("center"):
            _audit_membership(request, obj, AuditAction.MEMBERSHIP_REMOVED)
        super().delete_queryset(request, queryset)
