import json
from django.db import migrations


# Curriculum evaluation intent for every auto-marked no-review question.
# These notes appear in the marking panel for assessors doing spot-checks.
# Format: what the question measures at NQF2, then the verified answer.

NOTES = {
    # ── Literacy A — Vocabulary & Grammar ──────────────────────────────────
    "LIT-A-1": (
        "Curriculum intent: Establishes foundational workplace vocabulary at NQF2. "
        "The learner must recognise four employment-sector terms (Salary, Interview, Contract, Apply) "
        "by matching each to its functional definition. "
        "Tests whether the learner has the lexical base required before entering reading comprehension.\n\n"
        "Auto-mark: Each correct match awards 1 mark. Expected: t1=Salary, t2=Interview, t3=Contract, t4=Apply."
    ),
    "LIT-A-2A": (
        "Curriculum intent: Tests grammatical tense selection in a workplace sentence at NQF2. "
        "The learner must identify the correct past-tense form for a completed action. "
        "Tense accuracy is a baseline requirement for workplace writing, letters, and forms.\n\n"
        "Auto-mark: Correct answer — 'went'. Present-tense selection (go) = 0 marks."
    ),
    "LIT-A-2B": (
        "Curriculum intent: Tests verb form selection in context at NQF2. "
        "The learner distinguishes between the base verb and the past participle in an instruction sentence. "
        "Demonstrates contextual grammar awareness necessary for following written workplace instructions.\n\n"
        "Auto-mark: Correct answer — 'fill'. Past form (filled) in an infinitive slot = 0 marks."
    ),

    # ── Literacy B — Comprehension ──────────────────────────────────────────
    "LIT-B-1": (
        "Curriculum intent: Tests literal fact extraction from a reading passage at NQF2. "
        "Thandi's age is stated directly in the text. "
        "The question confirms whether the learner engaged with the passage at the surface level "
        "before inferential questions are applied.\n\n"
        "Auto-mark: Correct answer — 23. Any other value = 0 marks."
    ),
    "LIT-B-2": (
        "Curriculum intent: Tests precise identification of a named entity from a reading passage. "
        "Both 'Office Administration' and 'learnership' must appear — a paraphrase is insufficient. "
        "At NQF2, reading precision means reproducing the correct name, not approximating it. "
        "This is scored per keyword (1 mark each) to reward partial precision.\n\n"
        "Auto-mark: 1 mark per keyword found. Full marks require both terms."
    ),
    "LIT-B-3": (
        "Curriculum intent: Tests causal reasoning from the passage — why does the learner want to join? "
        "The answer requires the learner to infer a purposive relationship: she acts toward her family, "
        "not the reverse. This is a comprehension gate: 'her family supported her' answers a different question.\n\n"
        "Auto-mark: 2 marks if 'family' and a support-action verb appear and no reversed-direction phrase is present. "
        "0 marks if the direction is inverted or family is mentioned without a purposive verb."
    ),

    # ── Literacy C — Vocabulary in Context ────────────────────────────────
    "LIT-C-1": (
        "Curriculum intent: Tests ability to explain the significance of a workplace form using specific details. "
        "At NQF2, importance must be demonstrated through named elements, not generic statements. "
        "The tiered scoring rewards specificity: naming more required fields earns more marks. "
        "A learner who says 'it is important for information' has not answered the question.\n\n"
        "Auto-mark: 3 marks — four or more specific fields named (full name, ID number, contact, grade, disability). "
        "2 marks — two or three specific fields. 1 mark — one specific field. 0 marks — no specific content."
    ),
    "LIT-C-2": (
        "Curriculum intent: Tests interpretation of a qualifying phrase in a legal/administrative context. "
        "'If any' signals a conditional — something that may or may not exist. "
        "At NQF2, learners working with forms must understand that optional fields are conditional on circumstance. "
        "A learner who cannot interpret this phrase cannot correctly complete workplace documentation.\n\n"
        "Auto-mark: 2 marks — response contains a word indicating conditionality or existence (exist, existing, etc.) "
        "in a sentence of at least 4 words. 1 mark — keyword present but response too brief to constitute an explanation. "
        "0 marks — no relevant term detected."
    ),

    # ── Numeracy A — Basic Operations ─────────────────────────────────────
    "NUM-A-1": (
        "Curriculum intent: Tests addition fluency with two-digit numbers at NQF2. "
        "A learner entering a workplace numeracy programme must be able to add without a calculator. "
        "This question isolates the operation before contextualised application in later sections.\n\n"
        "Auto-mark: Correct answer — 72."
    ),
    "NUM-A-2": (
        "Curriculum intent: Tests subtraction from a round number, requiring borrowing or number-line reasoning. "
        "Subtraction from 100 is a practical benchmark — change calculation, remaining balance, stock counts.\n\n"
        "Auto-mark: Correct answer — 62."
    ),
    "NUM-A-3": (
        "Curriculum intent: Tests multiplication fact recall at NQF2. "
        "Times table fluency is a prerequisite for percentage, ratio, and scaling tasks in the sections that follow.\n\n"
        "Auto-mark: Correct answer — 42."
    ),
    "NUM-A-4": (
        "Curriculum intent: Tests division as the inverse of multiplication. "
        "The learner may use long division or factor reasoning. "
        "Division underpins unit-rate problems, payroll calculations, and fair-share distribution in the workplace.\n\n"
        "Auto-mark: Correct answer — 21."
    ),

    # ── Numeracy B — Fractions & Percentages ──────────────────────────────
    "NUM-B-1": (
        "Curriculum intent: Tests whether the learner understands a fraction as a division operation. "
        "'Half of 60' requires the learner to treat ½ as ÷ 2, not as an abstract symbol. "
        "This is the entry point to percentage reasoning in the questions that follow.\n\n"
        "Auto-mark: Correct answer — 30."
    ),
    "NUM-B-2": (
        "Curriculum intent: Tests percentage calculation applied to a monetary base value. "
        "25% of R200 requires either the conversion 0.25 × 200 or the equivalence 200 ÷ 4. "
        "Financial literacy at NQF2 requires fluency with these transformations — they appear "
        "in every payslip, discount, and VAT calculation a learner will encounter.\n\n"
        "Auto-mark: Correct answer — 50. Working verified via physical working sheet."
    ),
    "NUM-B-3": (
        "Curriculum intent: Tests a two-step percentage application in a consumer context. "
        "The learner must calculate the discount (10% of R400 = R40) and then subtract it (R400 − R40 = R360). "
        "Chaining two operations within a single contextualised problem is a core NQF2 numeracy competency.\n\n"
        "Auto-mark: Correct answer — R360. Working verified via physical working sheet."
    ),

    # ── Numeracy C — Applied Numeracy ─────────────────────────────────────
    "NUM-C-1": (
        "Curriculum intent: Tests elapsed time calculation in a 24-hour shift context. "
        "Reading timesheets, calculating hours worked, and verifying payslips all require this skill. "
        "The answer (8.5 hours or 8,5) accepts both decimal formats as correct — penalising formatting "
        "over mathematical accuracy serves no pedagogical purpose at this level.\n\n"
        "Auto-mark: Correct answer — 8.5 (or 8,5). Working verified via physical working sheet."
    ),
    "NUM-C-2": (
        "Curriculum intent: Tests multiplicative reasoning in a commuting context. "
        "Distance × 2 (return journey) is a direct application of doubling — the simplest rate problem. "
        "Transport cost planning is an immediate practical need for learnership participants.\n\n"
        "Auto-mark: Correct answer — 30."
    ),
    "NUM-C-3": (
        "Curriculum intent: Tests multi-step subtraction in a personal budgeting context. "
        "The learner must sum three expense categories (R800 + R1,200 + R300 = R2,300) "
        "and subtract from a total (R4,000 − R2,300 = R1,700). "
        "Budget management is a functional life-skill measured at NQF2.\n\n"
        "Auto-mark: Correct answer — R1,700. Working verified via physical working sheet."
    ),

    # ── Numeracy D — Patterns & Algebra ───────────────────────────────────
    "NUM-D-1": (
        "Curriculum intent: Tests recognition and extension of a linear number pattern (+2 sequence). "
        "Pattern thinking is the entry point to algebraic reasoning. "
        "At NQF2, the learner must identify the rule and apply it — not merely continue by inspection.\n\n"
        "Auto-mark: Correct answer — 10."
    ),
    "NUM-D-2": (
        "Curriculum intent: Tests linear equation solving — the ceiling skill of NQF2 numeracy. "
        "5x + 10 = 30 requires the learner to isolate the variable through inverse operations: "
        "subtract 10, then divide by 5. This is the first instance of formal algebraic manipulation "
        "in the assessment, and its presence distinguishes NQF2 from NQF1.\n\n"
        "Auto-mark: Correct answer — x = 4 (accept 4). Working verified via physical working sheet."
    ),
}


def apply(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    for code, note in NOTES.items():
        Question.objects.filter(code=code).update(marking_notes=note)


def reverse(apps, schema_editor):
    Question = apps.get_model("assessment", "Question")
    Question.objects.filter(code__in=list(NOTES.keys())).update(marking_notes="")


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0034_num_marking_notes"),
    ]

    operations = [
        migrations.RunPython(apply, reverse),
    ]
