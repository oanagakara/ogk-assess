from django.contrib import admin

from .models import AssessmentTemplate, Section, Question, Learner, Attempt, Response, Tenant, Score, ScoreAuditLog, DemoRequest

admin.site.register([AssessmentTemplate, Section, Question, Learner, Attempt, Response])


@admin.register(ScoreAuditLog)
class ScoreAuditLogAdmin(admin.ModelAdmin):
    list_display  = ("score", "action", "mode", "points_before", "points_after", "max_points", "changed_by", "changed_at")
    list_filter   = ("action", "mode", "changed_at")
    search_fields = ("score__response__attempt__code", "changed_by__username", "notes")
    readonly_fields = (
        "score", "changed_by", "changed_at", "action", "mode",
        "points_before", "points_after", "max_points", "notes",
    )
    ordering = ("-changed_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DemoRequest)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display  = ("name", "email", "org", "created_at")
    search_fields = ("name", "email", "org")
    readonly_fields = ("name", "email", "org", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display     = ("name", "slug", "is_active", "support_email")
    readonly_fields  = ("created_at",)
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        ("Identity",   {"fields": ("name", "slug", "legal_name", "tagline", "is_active")}),
        ("Branding",   {"fields": ("logo_url", "logo_white_url", "logo_alt", "favicon_url", "banner_url")}),
        ("Colours",    {"fields": ("color_primary", "color_secondary", "color_accent", "color_text", "color_bg")}),
        ("Typography", {"fields": ("font_family_primary", "font_family_secondary")}),
        ("Contact",    {"fields": ("support_email", "website_url", "footer_line")}),
        ("Meta",       {"fields": ("created_at",)}),
    )
