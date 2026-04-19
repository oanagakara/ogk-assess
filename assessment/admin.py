from django.contrib import admin

from .models import AssessmentTemplate, Section, Question, Learner, Attempt, Response, Tenant

admin.site.register([AssessmentTemplate, Section, Question, Learner, Attempt, Response])


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
