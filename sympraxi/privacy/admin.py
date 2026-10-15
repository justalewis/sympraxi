from django.contrib import admin
from django.http import HttpRequest

from sympraxi.privacy.models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["occurred_at", "action", "actor_label", "center", "ip_address"]
    list_filter = ["action"]
    search_fields = ["actor_label", "target_id"]
    date_hierarchy = "occurred_at"
    list_select_related = ["center"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False
