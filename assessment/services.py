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
        .filter(
            status=Attempt.IN_PROGRESS,
            last_activity_at__isnull=False,
            last_activity_at__gte=cutoff,
        )
        .count()
    )

    current_attempt_already_holds_seat = (
        attempt.status == Attempt.IN_PROGRESS
        and attempt.last_activity_at is not None
        and attempt.last_activity_at >= cutoff
    )

    if active >= SEAT_LIMIT and not current_attempt_already_holds_seat:
        return False, "All seats are in use. Please wait and try again."

    attempt.start(when=now)
    return True, ""
