from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .metrics import attempt_submissions_total, score_writes_total
from .models import Attempt, Score, ScoreAuditLog

_score_points_before: dict = {}


@receiver(pre_save, sender=Score)
def _capture_score_before(sender, instance, **kwargs):
    if instance.pk:
        try:
            _score_points_before[instance.pk] = Score.objects.values_list(
                "points", flat=True
            ).get(pk=instance.pk)
        except Score.DoesNotExist:
            _score_points_before[instance.pk] = None


@receiver(post_save, sender=Attempt)
def _count_submission(sender, instance, created, **kwargs):
    if not created and instance.status == Attempt.SUBMITTED:
        attempt_submissions_total.inc()


@receiver(post_save, sender=Score)
def _write_audit_entry(sender, instance, created, **kwargs):
    score_writes_total.labels(action="created" if created else "updated").inc()
    points_before = _score_points_before.pop(instance.pk, None)

    rubric = instance.rubric_json if isinstance(instance.rubric_json, dict) else {}
    mode = rubric.get("mode", "")
    notes = rubric.get("notes", "")

    ScoreAuditLog.objects.create(
        score=instance,
        changed_by=instance.assessor,
        action="created" if created else "updated",
        mode=mode,
        points_before=points_before,
        points_after=instance.points,
        max_points=instance.max_points,
        notes=notes,
    )
