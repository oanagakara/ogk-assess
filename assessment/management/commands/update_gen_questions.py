"""
Updates GEN question content. Idempotent — guards check before each batch.

Batch 1: GEN-D-1 reorder, 5 splits (GEN-B-5/C-1/C-7/D-2/E-4)
Batch 2: GEN-E-2 plain language, GEN-G-WRITE scale to 33 marks
Batch 3: grid_cols, full shared banks, GEN-E-5/F-1/F-8 splits
"""
import json
from django.core.management.base import BaseCommand
from django.db import transaction


_YVONNE = (
    "Yvonne Chaka Chaka was born and grew up in Dobsonville, Soweto. She began her music career "
    "in the early 1980s and released her first album in 1985. Her warm and powerful voice won the "
    "hearts of millions, and people began to call her the ‘Princess of Africa’. She became one "
    "of South Africa’s most celebrated entertainers, performing across Africa and around the world. "
    "In 2001, she was appointed a UNICEF Goodwill Ambassador. In this role, she uses her fame to "
    "fight malaria and promote better health across the African continent. She has received many "
    "awards and continues to inspire young people through music and public service."
)

_PICKNPAY = (
    "PICK N PAY LEARNERSHIP PROGRAMME 2026\n\n"
    "Pick n Pay is inviting applications for its 2026 Retail Learnership Programme. The 12-month "
    "programme is not permanent employment, but learners who qualify will develop valuable retail "
    "skills. Learners will receive a monthly stipend throughout the programme. A certificate of "
    "competence will be awarded to learners who successfully complete the programme.\n\n"
    "To apply, you must be between 18 and 35 years of age and have completed at least Grade 10. "
    "There is no fee to apply or join. Due to the high number of applications received, only "
    "shortlisted candidates will be contacted. The closing date for applications is 31 May 2026."
)

_NATALIE = (
    "Natalie du Toit was born in Cape Town in 1984. She showed exceptional talent as a swimmer "
    "from a young age and competed at a high level.\n\n"
    "In 2001, a motorcycle accident resulted in her left leg being amputated. Many people expected "
    "her to give up, but Natalie was compelled by her love for the sport and refused to surrender.\n\n"
    "With dedication and hard work, she was subsequently able to compete against able-bodied "
    "swimmers. She returned to top-level competition and continued to improve her times.\n\n"
    "In 2008, she became the first amputee swimmer to qualify for the Olympic Games, competing "
    "alongside athletes without disabilities. She later won multiple gold medals at the Paralympic "
    "Games. Natalie’s story is a powerful symbol of resilience and determination."
)


_WOMEN = (
    "On 9 August 1956, more than 20 000 women marched to the Union Buildings in Pretoria to protest "
    "against the pass laws. The march was organised by the Federation of South African Women (FEDSAW). "
    "The women were required to carry passes under apartheid law, which they strongly opposed. They "
    "carried petitions with thousands of signatures, determined to have their voices heard. Prime "
    "Minister J.G. Strijdom was absent and refused to meet with them. Despite this, the women remained "
    "orderly and observed a moment of silence with dignity before leaving. The march became a landmark "
    "event in South African history and a symbol of women's courage."
)


def _spec(passage, prompt, bank, targets, grid_cols=None):
    spec = {"prompt": prompt, "bank": bank, "targets": targets}
    if passage:
        spec = {"layout": "passage_split", "passage": passage, **spec}
    if grid_cols:
        spec["grid_cols"] = grid_cols
    return json.dumps(spec)


def _key(match_dict):
    return json.dumps({"auto_mark": True, "match": match_dict, "marks_per_match": 1})


class Command(BaseCommand):
    help = "Update GEN question content and splits. Safe to re-run — skips if already applied."

    def handle(self, *args, **options):
        from assessment.models import Question
        import json as _json

        splits_done = Question.objects.filter(code="GEN-B-5B").exists()
        e2_done = "She lost her leg in an accident" in _json.loads(
            Question.objects.get(code="GEN-E-2").spec_json
        ).get("bank", [])
        write_done = Question.objects.get(code="GEN-G-WRITE").max_marks == 33
        grid_done = (
            Question.objects.filter(code="GEN-E-5A").exists()
            and Question.objects.filter(code="GEN-F-8B").exists()
        )

        if splits_done and e2_done and write_done and grid_done:
            self.stdout.write("update_gen_questions: already applied, skipping.")
            return

        with transaction.atomic():
            if not splits_done:
                self._update_gen_d1()
                self._split("GEN-B-5")
                self._split("GEN-C-1")
                self._split("GEN-C-7")
                self._split("GEN-D-2")
                self._split("GEN-E-4")
                self._reorder()
            if not e2_done:
                self._update_gen_e2()
            if not write_done:
                self._update_gen_g_write()
            if not grid_done:
                self._apply_grids()
                self._update_e4_banks()
                self._split_e5()
                self._split_f1()
                self._split_f8()
                self._reorder()

        self.stdout.write(self.style.SUCCESS("update_gen_questions: done."))

    # ── GEN-G-WRITE ───────────────────────────────────────────────────────────

    def _update_gen_g_write(self):
        from assessment.models import Question
        import json as _json
        q = Question.objects.get(code="GEN-G-WRITE")
        q.max_marks = 33
        q.marking_notes = (
            "Score each criterion independently. "
            "MOTIVATION (0-12): strength of reason, elaboration, personal context. "
            "SKILLS (0-12): specificity and relevance of skills named. "
            "LANGUAGE (0-9): sentence quality, coherence, and length (6-8 sentences)."
        )
        q.answer_key_json = _json.dumps({
            "auto_mark": False,
            "criteria": [
                {
                    "key": "motivation",
                    "label": "MOTIVATION",
                    "max_points": 12,
                    "description": (
                        "10-12=clearly stated, fully elaborated, strong personal context; "
                        "7-9=stated with good elaboration and some personal context; "
                        "4-6=partially stated, limited elaboration; "
                        "1-3=generic or unexplained; 0=none or off-topic"
                    ),
                },
                {
                    "key": "skills",
                    "label": "SKILLS",
                    "max_points": 12,
                    "description": (
                        "10-12=two or more specific skills named with strong personal relevance and explanation; "
                        "7-9=skills named with good elaboration; "
                        "4-6=skills named with limited elaboration; "
                        "1-3=vague skills; 0=none"
                    ),
                },
                {
                    "key": "language",
                    "label": "LANGUAGE",
                    "max_points": 9,
                    "description": (
                        "8-9=complete coherent sentences, exactly 6-8, minimal errors; "
                        "5-7=mostly coherent, meets length requirement; "
                        "2-4=some coherence issues or length not met; "
                        "0-1=too brief or largely incoherent"
                    ),
                },
            ],
        })
        q.save()

    # ── GEN-E-2 ──────────────────────────────────────────────────────────────

    def _update_gen_e2(self):
        from assessment.models import Question
        q = Question.objects.get(code="GEN-E-2")
        q.spec_json = _spec(
            passage=_NATALIE,
            prompt="Drag the cause that matches each effect.",
            bank=[
                "She lost her leg in an accident",
                "She did not give up",
                "She trained hard every day",
                "She qualified for the Olympics",
                "Her story inspired others",
            ],
            targets=[
                {"id": "t1", "text": "She had to learn a new way to swim."},
                {"id": "t2", "text": "She returned to the pool and kept competing."},
                {"id": "t3", "text": "She could race against swimmers without disabilities."},
                {"id": "t4", "text": "She became the first amputee to compete at the Olympic Games."},
                {"id": "t5", "text": "People with disabilities felt they could reach their goals too."},
            ],
        )
        q.answer_key_json = _key({
            "t1": "She lost her leg in an accident",
            "t2": "She did not give up",
            "t3": "She trained hard every day",
            "t4": "She qualified for the Olympics",
            "t5": "Her story inspired others",
        })
        q.save()

    # ── GEN-D-1 ──────────────────────────────────────────────────────────────

    def _update_gen_d1(self):
        from assessment.models import Question
        q = Question.objects.get(code="GEN-D-1")
        q.spec_json = _spec(
            passage=None,
            prompt="Drag the number that shows where each sentence belongs in the paragraph.",
            bank=["1", "2", "3", "4", "5", "6"],
            targets=[
                {"id": "t1", "text": "Shoprite is one of the largest retail companies in Africa."},
                {"id": "t2", "text": "The company offers a learnership programme for young South Africans."},
                {"id": "t3", "text": "Successful candidates receive training in customer service and stock management."},
                {"id": "t4", "text": "At the end of the programme, learners write a final assessment."},
                {"id": "t5", "text": "Those who pass may be offered permanent employment at Shoprite."},
                {"id": "t6", "text": "Applicants must have completed Grade 10 and be between 18 and 35 years old."},
            ],
        )
        q.answer_key_json = _key({"t1": "1", "t2": "2", "t3": "3", "t4": "5", "t5": "6", "t6": "4"})
        q.save()

    # ── Splits ────────────────────────────────────────────────────────────────

    def _split(self, code):
        from assessment.models import Question

        SPLITS = {
            "GEN-B-5": dict(
                passage=_YVONNE,
                prompt="Drag the correct connective to complete each sentence.",
                a_bank=["because", "when", "and"],
                a_targets=[
                    {"id": "t1", "text": "She became famous _____ her voice was powerful."},
                    {"id": "t2", "text": "She was already performing _____ she was still a teenager."},
                    {"id": "t3", "text": "She received many awards _____ continued to inspire others."},
                ],
                a_key={"t1": "because", "t2": "when", "t3": "and"},
                b_bank=["but", "so", "while"],
                b_targets=[
                    {"id": "t1", "text": "She wanted to rest, _____ she kept performing for her fans."},
                    {"id": "t2", "text": "She cared deeply about health, _____ she became a UNICEF Ambassador."},
                    {"id": "t3", "text": "She was singing on stage _____ her fans cheered loudly."},
                ],
                b_key={"t1": "but", "t2": "so", "t3": "while"},
            ),
            "GEN-C-1": dict(
                passage=_PICKNPAY,
                prompt="Drag the correct word to complete each sentence.",
                a_bank=["permanent", "qualify", "stipend"],
                a_targets=[
                    {"id": "t1", "text": "The programme is not _____ employment."},
                    {"id": "t2", "text": "Learners who _____ will develop valuable retail skills."},
                    {"id": "t3", "text": "Learners receive a monthly _____ throughout the programme."},
                ],
                a_key={"t1": "permanent", "t2": "qualify", "t3": "stipend"},
                b_bank=["certificate", "closing", "shortlisted"],
                b_targets=[
                    {"id": "t1", "text": "A _____ of competence is awarded on completion."},
                    {"id": "t2", "text": "The _____ date for applications is 31 May 2026."},
                    {"id": "t3", "text": "Only _____ candidates will be contacted."},
                ],
                b_key={"t1": "certificate", "t2": "closing", "t3": "shortlisted"},
            ),
            "GEN-C-7": dict(
                passage=None,
                prompt="Drag the correct word to complete each sentence.",
                a_bank=["There", "teach", "flour"],
                a_targets=[
                    {"id": "t1", "text": "_____ are many people waiting to apply for the learnership."},
                    {"id": "t2", "text": "She will _____ the learners how to serve customers."},
                    {"id": "t3", "text": "Add the _____ to make the dough for the bread rolls."},
                ],
                a_key={"t1": "There", "t2": "teach", "t3": "flour"},
                b_bank=["want", "where", "is"],
                b_targets=[
                    {"id": "t1", "text": "I _____ to complete this programme and find a job."},
                    {"id": "t2", "text": "Do you know _____ the training centre is located?"},
                    {"id": "t3", "text": "The office _____ open every weekday from 8 am."},
                ],
                b_key={"t1": "want", "t2": "where", "t3": "is"},
            ),
            "GEN-D-2": dict(
                passage=None,
                prompt="Drag the digital time that matches each written description.",
                a_bank=["07h30", "14h45", "21h20"],
                a_targets=[
                    {"id": "t1", "text": "Half past seven in the morning"},
                    {"id": "t2", "text": "Quarter to three in the afternoon"},
                    {"id": "t3", "text": "Twenty minutes past nine at night"},
                ],
                a_key={"t1": "07h30", "t2": "14h45", "t3": "21h20"},
                b_bank=["12h00", "05h55", "16h10"],
                b_targets=[
                    {"id": "t1", "text": "Midday"},
                    {"id": "t2", "text": "Five minutes to six in the morning"},
                    {"id": "t3", "text": "Ten minutes past four in the afternoon"},
                ],
                b_key={"t1": "12h00", "t2": "05h55", "t3": "16h10"},
            ),
            "GEN-E-4": dict(
                passage=_NATALIE,
                prompt="Drag the correct modal verb phrase to complete each sentence about Natalie.",
                a_bank=["refused to", "had to", "was able to"],
                a_targets=[
                    {"id": "t1", "text": "Natalie _____ give up swimming, so she kept training."},
                    {"id": "t2", "text": "After losing her leg, she _____ learn to swim in a completely new way."},
                    {"id": "t3", "text": "Through years of training, she _____ compete against swimmers without disabilities."},
                ],
                a_key={"t1": "refused to", "t2": "had to", "t3": "was able to"},
                b_bank=["managed to", "wanted to", "chose to"],
                b_targets=[
                    {"id": "t1", "text": "She _____ qualify for the 2008 Olympic Games, becoming the first amputee to do so."},
                    {"id": "t2", "text": "She always _____ represent South Africa on the world stage."},
                    {"id": "t3", "text": "After the Olympics, she _____ compete in the Paralympic Games as well."},
                ],
                b_key={"t1": "managed to", "t2": "wanted to", "t3": "chose to"},
            ),
        }

        s = SPLITS[code]
        q = Question.objects.get(code=code)

        # Update original to part A (first 3)
        q.spec_json = _spec(s["passage"], s["prompt"], s["a_bank"], s["a_targets"])
        q.answer_key_json = _key(s["a_key"])
        q.max_marks = 3
        q.save()

        # Create part B (last 3) — order will be fixed in _reorder
        Question.objects.create(
            section=q.section,
            order=q.order + 1,  # temporary; _reorder will correct this
            code=f"{code}B",
            prompt=q.prompt,
            kind=q.kind,
            max_marks=3,
            spec_json=_spec(s["passage"], s["prompt"], s["b_bank"], s["b_targets"]),
            answer_key_json=_key(s["b_key"]),
            marking_notes="",
        )

    # ── Batch 3: grid cols, shared banks, E-5 / F-1 / F-8 splits ─────────────

    def _apply_grids(self):
        """Add grid_cols=1 to questions that need a single-column answer grid."""
        import json as _json
        from assessment.models import Question
        for code in ["GEN-B-5", "GEN-B-5B", "GEN-C-1", "GEN-E-4", "GEN-E-4B"]:
            q = Question.objects.get(code=code)
            spec = _json.loads(q.spec_json)
            spec["grid_cols"] = 1
            q.spec_json = _json.dumps(spec)
            q.save()

    def _update_e4_banks(self):
        """Give GEN-E-4 and GEN-E-4B the full 6-word bank as distractors."""
        import json as _json
        from assessment.models import Question
        full_bank = ["refused to", "had to", "was able to", "managed to", "wanted to", "chose to"]
        for code in ["GEN-E-4", "GEN-E-4B"]:
            q = Question.objects.get(code=code)
            spec = _json.loads(q.spec_json)
            spec["bank"] = full_bank
            q.spec_json = _json.dumps(spec)
            q.save()

    def _split_e5(self):
        from assessment.models import Question
        full_bank = ["at", "for", "in", "against", "from", "through", "with"]
        q = Question.objects.get(code="GEN-E-5")
        # Rename to GEN-E-5A with first 3 targets
        q.code = "GEN-E-5A"
        q.max_marks = 3
        q.spec_json = _spec(
            _NATALIE,
            "Drag the correct preposition to complete each sentence.",
            full_bank,
            [
                {"id": "t1", "text": "Natalie was born _____ Cape Town."},
                {"id": "t2", "text": "She trained _____ years to reach her goals."},
                {"id": "t3", "text": "She competed _____ able-bodied swimmers."},
            ],
            grid_cols=1,
        )
        q.answer_key_json = _key({"t1": "in", "t2": "for", "t3": "against"})
        q.save()
        # Create GEN-E-5B with last 4 targets
        Question.objects.create(
            section=q.section, order=q.order + 1, code="GEN-E-5B",
            prompt=q.prompt, kind=q.kind, max_marks=4,
            spec_json=_spec(
                _NATALIE,
                "Drag the correct preposition to complete each sentence.",
                full_bank,
                [
                    {"id": "t1", "text": "She won medals _____ the Paralympic Games."},
                    {"id": "t2", "text": "She showed talent _____ a young age."},
                    {"id": "t3", "text": "She achieved success _____ hard work and determination."},
                    {"id": "t4", "text": "She swam _____ a prosthetic limb."},
                ],
                grid_cols=1,
            ),
            answer_key_json=_key({"t1": "at", "t2": "from", "t3": "through", "t4": "with"}),
            marking_notes="",
        )

    def _split_f1(self):
        from assessment.models import Question
        full_bank = ["organised", "protest", "petition", "orderly", "dignity", "symbol"]
        q = Question.objects.get(code="GEN-F-1")
        q.max_marks = 3
        q.spec_json = _spec(
            _WOMEN,
            "Match each word to its correct meaning.",
            full_bank,
            [
                {"id": "t1", "text": "A formal written request signed by many people"},
                {"id": "t2", "text": "To plan and coordinate an activity"},
                {"id": "t3", "text": "A public expression of disagreement or disapproval"},
            ],
        )
        q.answer_key_json = _key({"t1": "petition", "t2": "organised", "t3": "protest"})
        q.save()
        Question.objects.create(
            section=q.section, order=q.order + 1, code="GEN-F-1B",
            prompt=q.prompt, kind=q.kind, max_marks=3,
            spec_json=_spec(
                _WOMEN,
                "Match each word to its correct meaning.",
                full_bank,
                [
                    {"id": "t1", "text": "Calm and well-behaved"},
                    {"id": "t2", "text": "A feeling of pride and self-respect"},
                    {"id": "t3", "text": "Something that represents a bigger idea or group"},
                ],
            ),
            answer_key_json=_key({"t1": "orderly", "t2": "dignity", "t3": "symbol"}),
            marking_notes="",
        )

    def _split_f8(self):
        from assessment.models import Question
        full_bank = ["protest", "required", "determined", "orderly", "observed", "landmark", "absent", "symbol"]
        q = Question.objects.get(code="GEN-F-8")
        q.max_marks = 4
        q.spec_json = _spec(
            _WOMEN,
            "Find the word in the passage that matches each meaning.",
            full_bank,
            [
                {"id": "t1", "text": "A public objection or expression of disapproval"},
                {"id": "t2", "text": "Needed; expected by law or rule"},
                {"id": "t3", "text": "Showing firm purpose; not willing to give up"},
                {"id": "t4", "text": "Calm and controlled in behaviour"},
            ],
            grid_cols=1,
        )
        q.answer_key_json = _key({"t1": "protest", "t2": "required", "t3": "determined", "t4": "orderly"})
        q.save()
        Question.objects.create(
            section=q.section, order=q.order + 1, code="GEN-F-8B",
            prompt=q.prompt, kind=q.kind, max_marks=4,
            spec_json=_spec(
                _WOMEN,
                "Find the word in the passage that matches each meaning.",
                full_bank,
                [
                    {"id": "t1", "text": "Watched solemnly or took part in a ceremony"},
                    {"id": "t2", "text": "Important and historically significant"},
                    {"id": "t3", "text": "Not present; not there"},
                    {"id": "t4", "text": "Something that stands for a larger idea or meaning"},
                ],
                grid_cols=1,
            ),
            answer_key_json=_key({"t1": "observed", "t2": "landmark", "t3": "absent", "t4": "symbol"}),
            marking_notes="",
        )

    # ── Re-order ──────────────────────────────────────────────────────────────

    def _reorder(self):
        from assessment.models import Question

        sec5_order = [
            "GEN-A-1", "GEN-A-2",
            "GEN-B-READ", "GEN-B-1", "GEN-B-2", "GEN-B-3", "GEN-B-4",
            "GEN-B-5", "GEN-B-5B",
            "GEN-C-READ",
            "GEN-C-1", "GEN-C-1B",
            "GEN-C-2", "GEN-C-3", "GEN-C-4", "GEN-C-5", "GEN-C-6",
            "GEN-C-7", "GEN-C-7B",
            "GEN-D-1",
            "GEN-D-2", "GEN-D-2B",
        ]
        sec6_order = [
            "GEN-E-READ",
            "GEN-E-1", "GEN-E-1B",
            "GEN-E-2", "GEN-E-3",
            "GEN-E-4", "GEN-E-4B",
            "GEN-E-5A", "GEN-E-5B",
            "GEN-F-READ", "GEN-F-1", "GEN-F-1B",
            "GEN-F-2", "GEN-F-3", "GEN-F-4", "GEN-F-5", "GEN-F-6", "GEN-F-7",
            "GEN-F-8", "GEN-F-8B",
            "GEN-G-WRITE", "GEN-G-HANDWRITE",
        ]

        for i, code in enumerate(sec5_order, start=1):
            Question.objects.filter(code=code).update(order=i)
        for i, code in enumerate(sec6_order, start=1):
            Question.objects.filter(code=code).update(order=i)
