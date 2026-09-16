from django.contrib import admin

from .models import AgentExplanation


@admin.register(AgentExplanation)
class AgentExplanationAdmin(admin.ModelAdmin):
    list_display = ("score", "timestamp")
    readonly_fields = ("timestamp",)
