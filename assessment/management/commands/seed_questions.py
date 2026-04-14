from __future__ import annotations

import json
from django.core.management.base import BaseCommand
from django.db import transaction

from assessment.models import AssessmentTemplate, Section, Question

class Command(BaseCommand):
    help = "Seed the NQF placement assessment questions into the database."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="NQF Learner Placement Assessment", help="Template name")
        parser.add_argument("--template-version", default="v1", help="Template version")

    @transaction.atomic
    def handle(self, *args, **opts):
        name: str = opts["name"]
        version: str = opts["template_version"]

        template, _ = AssessmentTemplate.objects.get_or_create(name=name, version=version)

        # Wipe existing questions/sections for this template to make re-seeding deterministic
        Question.objects.filter(section__template=template).delete()
        Section.objects.filter(template=template).delete()

        sec_lit = Section.objects.create(template=template, title="SECTION 1: LITERACY (45 MINUTES)", order=1)
        sec_num = Section.objects.create(template=template, title="SECTION 2: NUMERACY (45 MINUTES)", order=2)

        items = []

        # ===== Part A =====
        items.append({
            "section": sec_lit,
            "code": "LIT-A-1",
            "prompt": (
                "Part A: Vocabulary & Sentences (NQF 1–2)\n\n"
                "1) Match the word to the meaning:\n"
                "a) Apply\n"
                "b) Interview\n"
                "c) Contract\n"
                "d) Payslip\n\n"
                "1. Money you earn from work\n"
                "2. A meeting where you answer questions for a job\n"
                "3. A written agreement\n"
                "4. Ask for a job or programme\n"
            ),
            "max_marks": 4,
            "spec": {"kind_hint": "match"},
        })

        items.append({
            "section": sec_lit,
            "code": "LIT-A-2A",
            "prompt": "2a) Choose the correct word:\nShe ____ (go / went) to the interview yesterday.",
            "max_marks": 1,
            "spec": {"kind_hint": "mcq_or_choice"},
        })
        items.append({
            "section": sec_lit,
            "code": "LIT-A-2B",
            "prompt": "2b) Choose the correct word:\nThe supervisor asked me to ____ (fill / filled) in the form.",
            "max_marks": 1,
            "spec": {"kind_hint": "mcq_or_choice"},
        })

        items.append({
            "section": sec_lit,
            "code": "LIT-A-3A",
            "prompt": "3) Write a sentence using the word: Learn",
            "max_marks": 1,
        })
        items.append({
            "section": sec_lit,
            "code": "LIT-A-3B",
            "prompt": "3) Write a sentence using the word: Work",
            "max_marks": 1,
        })
        items.append({
            "section": sec_lit,
            "code": "LIT-A-3C",
            "prompt": "3) Write a sentence using the word: Help",
            "max_marks": 1,
        })

        # ===== Part B =====
        items.append({
            "section": sec_lit,
            "code": "LIT-B-READ",
            "prompt": (
                "Part B: Reading Comprehension (NQF 2–3)\n\n"
                "Read the text:\n\n"
                "Thandi is 23 years old. She lives in a township near Cape Town. She wants to join a\n"
                "learnership in Office Administration. She sends her CV to a training provider and goes\n"
                "for an interview. She hopes the programme will help her get a job and support her\n"
                "family.\n"
            ),
            "max_marks": 0,
        })
        items.append({"section": sec_lit, "code": "LIT-B-1", "prompt": "1) How old is Thandi?", "max_marks": 1})
        items.append({"section": sec_lit, "code": "LIT-B-2", "prompt": "2) What programme does she want to join?", "max_marks": 1})
        items.append({"section": sec_lit, "code": "LIT-B-3", "prompt": "3) Why does she want to join the learnership?", "max_marks": 2})
        items.append({"section": sec_lit, "code": "LIT-B-4", "prompt": "4) What does “support her family” mean?", "max_marks": 2})

        # ===== Part C =====
        items.append({
            "section": sec_lit,
            "code": "LIT-C-FORM",
            "prompt": (
                "Part C: Functional Literacy (NQF 3)\n\n"
                "Complete the form:\n"
                "Name: __________\n"
                "ID Number: __________\n"
                "Cell Number: __________\n"
                "Highest Grade Passed: __________\n"
                "Disability (if any): __________\n"
            ),
            "max_marks": 0,
            "spec": {"kind_hint": "fill_blanks"},
        })
        items.append({"section": sec_lit, "code": "LIT-C-1", "prompt": "1) Why is this form important?", "max_marks": 2})
        items.append({"section": sec_lit, "code": "LIT-C-2", "prompt": "2) What does “if any” mean?", "max_marks": 1})

        # ===== Part D =====
        items.append({
            "section": sec_lit,
            "code": "LIT-D-WRITE",
            "prompt": (
                "Part D: Writing Task (NQF 3–4)\n\n"
                "Write a short paragraph (6–8 sentences):\n"
                "“Why I want to join a learnership and what skills I want to develop.”\n"
            ),
            "max_marks": 8,
        })

        # ===== Numeracy =====
        items.append({
            "section": sec_num,
            "code": "NUM-A-INTRO",
            "prompt": "Part A: Basic Arithmetic (NQF 1–2)",
            "max_marks": 0,
        })
        items.append({"section": sec_num, "code": "NUM-A-1", "prompt": "1) 25 + 47 =", "max_marks": 1})
        items.append({"section": sec_num, "code": "NUM-A-2", "prompt": "2) 100 − 38 =", "max_marks": 1})
        items.append({"section": sec_num, "code": "NUM-A-3", "prompt": "3) 6 × 7 =", "max_marks": 1})
        items.append({
            "section": sec_num,
            "code": "NUM-A-4",
            "prompt": "4) 84 ÷ 4 =",
            "max_marks": 1,
            "answer_key": {"auto_mark": True, "answers": ["21"]},
        })

        items.append({
            "section": sec_num,
            "code": "NUM-B-INTRO",
            "prompt": "Part B: Fractions & Percentages (NQF 2–3)",
            "max_marks": 0,
        })
        items.append({"section": sec_num, "code": "NUM-B-1", "prompt": "1) What is ½ of 60?", "max_marks": 1})
        items.append({"section": sec_num, "code": "NUM-B-2", "prompt": "2) What is 25% of 200?", "max_marks": 1})
        items.append({
            "section": sec_num,
            "code": "NUM-B-3",
            "prompt": "3) A jacket costs R400. It is on special with 10% off. → How much do you pay?",
            "max_marks": 2,
        })

        items.append({
            "section": sec_num,
            "code": "NUM-C-INTRO",
            "prompt": "Part C: Time, Distance & Money (NQF 3)",
            "max_marks": 0,
        })
        items.append({
            "section": sec_num,
            "code": "NUM-C-1",
            "prompt": "1) Your shift starts at 08:00 and ends at 16:30. → How many hours do you work?",
            "max_marks": 2,
        })
        items.append({
            "section": sec_num,
            "code": "NUM-C-2",
            "prompt": "2) You travel 15 km to training and 15 km back. → How far in total per day?",
            "max_marks": 1,
        })
        items.append({
            "section": sec_num,
            "code": "NUM-C-3",
            "prompt": (
                "3) Your monthly stipend is R4,000.\n"
                "You spend:\n"
                "• Transport: R800\n"
                "• Food: R1,200\n"
                "• Airtime: R300\n\n"
                "→ How much money is left?"
            ),
            "max_marks": 2,
        })

        items.append({
            "section": sec_num,
            "code": "NUM-D-INTRO",
            "prompt": "Part D: Patterns & Simple Algebra (NQF 3–4)",
            "max_marks": 0,
        })
        items.append({"section": sec_num, "code": "NUM-D-1", "prompt": "1) Complete the pattern:\n2, 4, 6, 8, ___", "max_marks": 1})
        items.append({"section": sec_num, "code": "NUM-D-2", "prompt": "2) Solve:\n5x + 10 = 30", "max_marks": 2})

        created = 0
        for idx, it in enumerate(items, start=1):
            spec = it.get("spec", {})
            answer_key = it.get("answer_key", {})
            Question.objects.create(
                section=it["section"],
                order=idx,
                code=it["code"],
                prompt=it["prompt"],
                kind=it.get("kind", Question.TEXT),
                max_marks=it["max_marks"],
                spec_json=json.dumps(spec, ensure_ascii=False) if spec else "",
                answer_key_json=json.dumps(answer_key, ensure_ascii=False) if answer_key else "",
                marking_notes="",
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded template '{name}' ({version}) with {created} questions."))

