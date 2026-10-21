from django.contrib import admin

from sympraxi.lti.models import LtiPlatform


@admin.register(LtiPlatform)
class LtiPlatformAdmin(admin.ModelAdmin):
    list_display = ["name", "center", "issuer", "client_id", "is_active"]
    list_filter = ["is_active"]
