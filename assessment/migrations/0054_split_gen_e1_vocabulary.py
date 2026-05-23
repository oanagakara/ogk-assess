"""
Split GEN-E-1 vocabulary match (8 words) into two 4-word questions.
Reduces question length for learners with limited attention spans.

GEN-E-1  (pk 53): keeps words 1-4 (exceptional, compelled, surrender, dedication)
GEN-E-1B (new):   words 5-8  (prosthetic, competitive, subsequently, disabilities)

Questions with order ≥ 3 in section 6 are shifted up by 1 to make room.
"""
import json
from django.db import migrations


_PASSAGE = (
    "Natalie du Toit was born in Cape Town in 1984. She showed exceptional talent as a "
    "swimmer from a young age and competed at a high level.\n\n"
    "In 2001, a motorcycle accident resulted in her left leg being amputated. Many people "
    "expected her to give up, but Natalie was compelled by her love for the sport and "
    "refused to surrender.\n\n"
    "With dedication and hard work, she was subsequently able to compete against able-bodied "
    "swimmers. She returned to top-level competition and continued to improve her times.\n\n"
    "In 2008, she became the first amputee swimmer to qualify for the Olympic Games, competing "
    "alongside athletes without disabilities. She later won multiple gold medals at the "
    "Paralympic Games. Natalie's story is a powerful symbol of resilience and determination."
)

_SPEC_PART1 = json.dumps({
    "layout": "passage_split",
    "passage": _PASSAGE,
    "prompt": "Drag each word to its correct definition. (Part 1 of 2)",
    "bank": ["exceptional", "compelled", "surrender", "dedication"],
    "targets": [
        {"id": "t1", "text": "Outstanding or remarkably good"},
        {"id": "t2", "text": "Driven or strongly motivated to do something"},
        {"id": "t3", "text": "To give up or stop trying"},
        {"id": "t4", "text": "Commitment and hard work towards a goal"},
    ],
})

_KEY_PART1 = json.dumps({
    "auto_mark": True,
    "match": {"t1": "exceptional", "t2": "compelled", "t3": "surrender", "t4": "dedication"},
    "marks_per_match": 1,
})

_SPEC_PART2 = json.dumps({
    "layout": "passage_split",
    "passage": _PASSAGE,
    "prompt": "Drag each word to its correct definition. (Part 2 of 2)",
    "bank": ["prosthetic", "competitive", "subsequently", "disabilities"],
    "targets": [
        {"id": "t5", "text": "An artificial replacement for a body part"},
        {"id": "t6", "text": "Having a strong desire to win or succeed"},
        {"id": "t7", "text": "Happening later, as a result"},
        {"id": "t8", "text": "Physical or mental conditions that limit a person's activities"},
    ],
})

_KEY_PART2 = json.dumps({
    "auto_mark": True,
    "match": {
        "t5": "prosthetic",
        "t6": "competitive",
        "t7": "subsequently",
        "t8": "disabilities",
    },
    "marks_per_match": 1,
})


def split_gen_e1(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")

    try:
        q = Question.objects.get(code="GEN-E-1")
    except Question.DoesNotExist:
        return

    section_id = q.section_id

    # Shift all questions at order ≥ 3 upward to create a gap at order 3
    for row in Question.objects.filter(section_id=section_id, order__gte=3).order_by("-order"):
        row.order += 1
        row.save(update_fields=["order"])

    # Update GEN-E-1 to part 1 (stays at order 2)
    q.prompt = "PART E: VOCABULARY — Match each word from the passage to its meaning. (Part 1 of 2)"
    q.max_marks = 4
    q.spec_json = _SPEC_PART1
    q.answer_key_json = _KEY_PART1
    q.save()

    # Insert GEN-E-1B at order 3
    Question.objects.create(
        section_id=section_id,
        order=3,
        code="GEN-E-1B",
        prompt="PART E: VOCABULARY — Match each word from the passage to its meaning. (Part 2 of 2)",
        kind="match",
        max_marks=4,
        spec_json=_SPEC_PART2,
        answer_key_json=_KEY_PART2,
        marking_notes="",
    )


def unsplit_gen_e1(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")

    try:
        q1b = Question.objects.get(code="GEN-E-1B")
    except Question.DoesNotExist:
        return

    section_id = q1b.section_id
    q1b.delete()

    # Shift orders ≥ 3 back down
    for row in Question.objects.filter(section_id=section_id, order__gte=3).order_by("order"):
        row.order -= 1
        row.save(update_fields=["order"])

    # Restore GEN-E-1 prompt and marks (original spec not stored — leave spec as-is)
    try:
        q = Question.objects.get(code="GEN-E-1")
        q.prompt = "PART E: VOCABULARY — Match each word from the passage to its meaning."
        q.max_marks = 8
        q.save(update_fields=["prompt", "max_marks"])
    except Question.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0053_add_moderated_fields"),
    ]

    operations = [
        migrations.RunPython(split_gen_e1, unsplit_gen_e1),
    ]
