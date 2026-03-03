from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import Attempt

SEAT_LIMIT = 15
SEAT_TIMEOUT_MINUTES = 30

@transaction.atomic
def claim_seat(attempt: Attempt) -> tuple[bool, str]:
    now = timezone.now()
    cutoff = now - timedelta(minutes=SEAT_TIMEOUT_MINUTES)

    active = (
        Attempt.objects.select_for_update()
        .filter(status=Attempt.IN_PROGRESS, last_activity_at__gte=cutoff)
        .count()
    )

    if active >= SEAT_LIMIT and attempt.status != Attempt.IN_PROGRESS:
        return False, "All seats are in use. Please wait and try again."

    # mark attempt as in progress and update activity
    if attempt.started_at is None:
        attempt.started_at = now
    attempt.status = Attempt.IN_PROGRESS
    attempt.last_activity_at = now
    attempt.save(update_fields=["started_at", "status", "last_activity_at"])

    return True, ""
