import os
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import Attempt, ExamSession

# Test-scoped override for load testing — unset in normal operation, so this
# never changes behavior for real clients. Only set LOAD_TEST_SEAT_LIMIT
# deliberately, for the duration of a load test.
SEAT_LIMIT = int(os.environ.get("LOAD_TEST_SEAT_LIMIT", "15"))
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


@transaction.atomic
def claim_session_seat(session: ExamSession, learner) -> tuple[bool, str, "Attempt | None"]:
    """
    Create an Attempt for learner in session, enforcing the session seat limit.
    Abandoned attempts count against the limit — a started seat is a billed seat.
    Returns (ok, error_message, attempt_or_None).
    """
    now = timezone.now()

    if not session.is_open:
        return False, "This session has expired. Please ask your assessor for help.", None

    # Lock the session row to serialise concurrent joins
    session = ExamSession.objects.select_for_update().get(pk=session.pk)

    occupied = session.attempts.filter(
        status__in=[Attempt.IN_PROGRESS, Attempt.SUBMITTED, Attempt.INCOMPLETE]
    ).count()

    if occupied >= session.seat_limit:
        return False, "This session is full. Please ask your assessor for help.", None

    workstation_number = session.seat_limit - occupied

    attempt = Attempt.objects.create(
        template=session.template,
        learner=learner,
        session=session,
        workstation_number=workstation_number,
    )
    attempt.start(when=now)
    return True, "", attempt
