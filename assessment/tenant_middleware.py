from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.urls import set_script_prefix

_RESERVED_PREFIXES = {"health", "favicon.ico", "accounts", "static"}


class PathTenantMiddleware:
    """Resolves the active tenant from the first URL path segment
    (ogk-solutions.co.za/<slug>/...) for the free-tier multi-tenant
    deployment, only when MULTI_TENANT_MODE is on.

    Strips the slug from request.path_info and sets the script prefix so
    every existing route, reverse() call, and {% url %} tag keeps working
    unmodified — the rest of the app never needs to know about the prefix.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.MULTI_TENANT_MODE:
            return self.get_response(request)

        from assessment.models import Tenant

        path = request.path_info
        segments = path.split("/")
        slug = segments[1] if len(segments) > 1 and segments[1] else ""

        if not slug:
            return HttpResponseRedirect(f"/{settings.DEFAULT_TENANT_SLUG}/")

        reserved = _RESERVED_PREFIXES | {settings.ADMIN_URL_PREFIX}
        if slug in reserved:
            return self.get_response(request)

        tenant = Tenant.objects.filter(slug=slug, is_active=True).first()
        if tenant is None:
            raise Http404(f"Unknown tenant: {slug}")

        request.tenant = tenant
        request.path_info = "/" + "/".join(segments[2:])
        set_script_prefix(f"/{slug}/")
        try:
            return self.get_response(request)
        finally:
            set_script_prefix("/")
