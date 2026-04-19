from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import Score, ScoreAuditLog

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


@receiver(post_save, sender=Score)
def _write_audit_entry(sender, instance, created, **kwargs):
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
