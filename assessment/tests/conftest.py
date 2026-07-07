import pytest

from assessment.models import Tenant


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="Test Tenant", slug="test-tenant")
