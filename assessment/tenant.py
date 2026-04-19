from functools import lru_cache

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver


@lru_cache(maxsize=1)
def get_active_tenant():
    from assessment.models import Tenant
    try:
        return Tenant.objects.get(slug=settings.ACTIVE_TENANT_SLUG, is_active=True)
    except Tenant.DoesNotExist:
        return Tenant.objects.filter(is_active=True).first()


@receiver(post_save, sender="assessment.Tenant")
def invalidate_tenant_cache(sender, **kwargs):
    get_active_tenant.cache_clear()
