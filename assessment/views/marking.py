"""Assessor marking workflow: scoring, review queue, uploads, audit."""
import json
import logging
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from ..auto_mark import auto_mark_attempt
from ..nqf import build_question_metadata, compute_nqf_placement
from ..forms import AttemptForm
from ..models import (
    AssessmentTemplate, Attempt, Learner, Question, Response,
    Score, ScoreAuditLog, Section, WorkingSheet, WritingSubmission,
)

from ._common import (
    MarkingTotals,
    _expire_overdue_attempts, _extract_inline_choices, _question_spec, _question_answer_key, _safe_json_loads,
    _is_layout_only_question,
    effective_is_moderator, is_assessor, is_auditor, is_moderator, is_staff,
)


# ── Question helpers ──────────────────────────────────────────────────────────

def _find_rubric_candidates(question):
    for container in (_question_spec(question), _question_answer_key(question)):
        if not isinstance(container, dict):
            continue
        for key in ("rubric", "rubric_criteria", "criteria"):
            value = container.get(key)
            if isinstance(value, list):
                return value
    return []


def _normalise_rubric_item(item, idx):
    label = (
        item.get("label") or item.get("criterion") or item.get("name") or f"Criterion {idx}"
    ).strip()
    raw_max = item.get("max_points", item.get("points", item.get("max", 0)))
    try:
        max_points = float(raw_max)
    except (TypeError, ValueError):
        max_points = 0.0
    key = slugify(str(item.get("key") or label)) or f"criterion_{idx}"
    return {"key": key, "label": label, "max_points": max_points}


def _extract_rubric(question):
    return [
        _normalise_rubric_item(item, idx)
        for idx, item in enumerate(_find_rubric_candidates(question), start=1)
        if isinstance(item, dict)
    ]


def _is_markable_question(question):
    if _is_layout_only_question(question):
        return False
    if _extract_rubric(question):
        return True
    return float(question.max_marks or 0) > 0


# ── Response rendering ────────────────────────────────────────────────────────

def _render_form_fill(spec, data):
    fields = spec.get("fields", [])
    if fields:
        return "\n".join(
            f"{field.get('label') or field.get('name', '')}: {data.get(field.get('name', ''), '')}"
            for field in fields
        ).strip()
    return "\n".join(f"{k}: {v}" for k, v in data.items()).strip()


def _render_match(spec, data):
    targets = {
        str(t.get("id")): t.get("text", "")
        for t in spec.get("targets", [])
        if isinstance(t, dict)
    }
    return "\n".join(
        f"{targets.get(str(tid), str(tid))}: {word}"
        for tid, word in data.items()
    ).strip()


def _render_text(data):
    if isinstance(data, dict):
        return str(data.get("answer", "")).strip()
    return str(data).strip()


def _render_response_for_marking(question, response):
    if not response or not response.response_json:
        return ""
    spec = _question_spec(question)
    data = _safe_json_loads(response.response_json, {})
    if spec.get("layout") == "form_fill":
        return _render_form_fill(spec, data)
    if question.kind == Question.MATCH:
        return _render_match(spec, data)
    return _render_text(data)


def _build_response_display(question, response):
    """Return structured display data used by the marking template to render
    a faithful visual representation of what the learner submitted."""
    from ..models import Question as Q
    spec = _question_spec(question)
    key = _question_answer_key(question)
    data = _safe_json_loads(
        response.response_json if response else None, {}
    )

    if spec.get("layout") == "form_fill":
        return {"kind": "text", "text": _render_form_fill(spec, data)}

    if question.kind == Question.MATCH:
        targets = [
            (str(t.get("id")), t.get("text", ""))
            for t in spec.get("targets", [])
            if isinstance(t, dict)
        ]
        correct_map = {str(k): str(v) for k, v in key.get("match", {}).items()}
        rows = []
        for tid, target_text in targets:
            placed = str(data.get(tid, "")).strip()
            correct = correct_map.get(tid, "")
            if placed:
                is_correct = placed.lower() == correct.lower()
            else:
                is_correct = None
            rows.append({
                "target": target_text,
                "placed": placed,
                "correct": correct,
                "is_correct": is_correct,
            })
        return {"kind": "match", "rows": rows}

    if spec.get("kind_hint") == "mcq_or_choice":
        choices = spec.get("choices") or _extract_inline_choices(question.prompt)
        selected = str(data.get("answer", "")).strip()
        raw_kw = key.get("keyword_answer", [])
        correct_answers = {
            (k.lower() if isinstance(k, str) else str(k).lower())
            for k in (raw_kw if isinstance(raw_kw, list) else [raw_kw])
        }
        items = []
        for ch in choices:
            items.append({
                "label": ch,
                "selected": ch == selected,
                "is_correct_choice": ch.lower() in correct_answers if correct_answers else None,
            })
        return {"kind": "choice", "items": items, "selected": selected}

    text = str(data.get("answer", "")).strip() if isinstance(data, dict) else str(data).strip()
    return {"kind": "text", "text": text}


def _clamped_float(value, upper_bound):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(parsed, float(upper_bound)))


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _score_for_response(response):
    try:
        return response.score
    except Score.DoesNotExist:
        return None


def _max_points_for_question(question, rubric):
    if rubric:
        return sum(item["max_points"] for item in rubric)
    return float(question.max_marks or 0)


def _saved_criteria_from_score(score) -> dict:
    """Parse the saved per-criterion points/feedback from a score's rubric_json."""
    if not (score and isinstance(score.rubric_json, dict)):
        return {}
    return {
        str(item["key"]): item
        for item in score.rubric_json.get("criteria", [])
        if isinstance(item, dict) and item.get("key")
    }


def _rubric_display_rows(rubric, saved_criteria) -> list:
    return [
        {
            "key": c["key"],
            "label": c["label"],
            "max_points": c["max_points"],
            "value": saved_criteria.get(c["key"], {}).get("points", ""),
            "feedback": saved_criteria.get(c["key"], {}).get("feedback", ""),
        }
        for c in rubric
    ]


def _score_notes(score) -> str:
    if score and isinstance(score.rubric_json, dict):
        return score.rubric_json.get("notes", "")
    return ""


def _build_marking_row(question, index, responses_by_qid):
    response = responses_by_qid[question.pk]
    score = _score_for_response(response)
    rubric = _extract_rubric(question)
    saved_criteria = _saved_criteria_from_score(score)
    rubric_rows = _rubric_display_rows(rubric, saved_criteria)
    max_points = _max_points_for_question(question, rubric)
    awarded = float(score.points) if score else 0.0

    return {
        "index": index,
        "question": question,
        "response": response,
        "response_text": _render_response_for_marking(question, response),
        "response_display": _build_response_display(question, response),
        "has_rubric": bool(rubric_rows),
        "rubric": rubric_rows,
        "manual_value": awarded if score and not rubric_rows else "",
        "notes": _score_notes(score),
        "score": score,
        "awarded": awarded,
        "max_points": max_points,
    }


def _save_question_score(request, attempt, question):
    response, _ = Response.objects.get_or_create(attempt=attempt, question=question)
    rubric = _extract_rubric(question)

    if rubric:
        criteria_payload = []
        total_points = 0.0
        max_points = 0.0
        for criterion in rubric:
            max_points += criterion["max_points"]
            points = _clamped_float(
                request.POST.get(f"rubric__{question.pk}__{criterion['key']}", ""),
                criterion["max_points"],
            )
            feedback = request.POST.get(
                f"rubric_feedback__{question.pk}__{criterion['key']}", ""
            ).strip()
            criteria_payload.append({
                "key": criterion["key"],
                "label": criterion["label"],
                "max_points": criterion["max_points"],
                "points": points,
                "feedback": feedback,
            })
            total_points += points
        points = total_points
        rubric_json = {
            "mode": "rubric",
            "criteria": criteria_payload,
            "notes": request.POST.get(f"notes__{question.pk}", "").strip(),
        }
    else:
        max_points = float(question.max_marks or 0)
        points = _clamped_float(request.POST.get(f"manual__{question.pk}", ""), max_points)
        rubric_json = {
            "mode": "manual",
            "notes": request.POST.get(f"notes__{question.pk}", "").strip(),
        }

    Score.objects.update_or_create(
        response=response,
        defaults={
            "assessor": request.user,
            "points": points,
            "max_points": max_points,
            "rubric_json": rubric_json,
        },
    )


def _requires_assessor_attention(score) -> bool:
    """True if the question still needs assessor input."""
    if score is None:
        return True
    if not isinstance(score.rubric_json, dict):
        return True
    return bool(score.rubric_json.get("needs_review", False))


def _review_type(score) -> str:
    """
    Classify why a question is in the review queue.

    process      — answer correct, working evidence absent; assessor verifies the working sheet.
    comprehension — auto-flagged; assessor adjudicates meaning or quality.
    manual       — no auto-mark; assessor scores from scratch.
    """
    if score is None:
        return "manual"
    rubric = score.rubric_json if isinstance(score.rubric_json, dict) else {}
    if rubric.get("auto_marked"):
        if rubric.get("verify_working"):
            return "process"
        if rubric.get("answer_found") and rubric.get("working_found") is False:
            return "process"
        return "comprehension"
    return "manual"


def _build_review_queue(markable_questions, responses_by_qid) -> list:
    """Return the subset of markable questions that still need assessor attention."""
    return [
        q for q in markable_questions
        if _requires_assessor_attention(_score_for_response(responses_by_qid[q.pk]))
    ]


def _compute_marking_totals(markable_questions, responses_by_qid) -> MarkingTotals:
    available = 0.0
    awarded = 0.0
    scored_count = 0
    for question in markable_questions:
        score = _score_for_response(responses_by_qid[question.pk])
        rubric = _extract_rubric(question)
        available += _max_points_for_question(question, rubric)
        awarded += float(score.points) if score else 0.0
        if score is not None:
            scored_count += 1
    return MarkingTotals(available=available, awarded=awarded, scored_count=scored_count)


def _fetch_responses(attempt, question_ids: list[int]) -> dict:
    return {
        r.question_id: r
        for r in Response.objects.filter(
            attempt=attempt, question_id__in=question_ids
        ).select_related("score")
    }


def _is_auto_done(score) -> bool:
    return score is not None and not _requires_assessor_attention(score)


def _build_sidebar_item(q, responses_by_qid, current_question, code) -> dict:
    score = _score_for_response(responses_by_qid[q.pk])
    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": code})
    return {
        "question": q,
        "pending": _requires_assessor_attention(score),
        "auto_done": _is_auto_done(score),
        "active": current_question and q.pk == current_question.pk,
        "url": f"{url}?qid={q.pk}",
        "review_type": _review_type(score),
    }


def _build_sidebar(markable_questions, responses_by_qid, current_question, code) -> tuple:
    all_items = [_build_sidebar_item(q, responses_by_qid, current_question, code) for q in markable_questions]
    needs_review = [i for i in all_items if not i["auto_done"]]
    spot_check = [i for i in all_items if i["auto_done"]]
    return needs_review, spot_check


def _prev_next_urls(markable_questions, current_question, mark_url, from_tab="submitted") -> tuple:
    if not current_question:
        return None, None
    idx = markable_questions.index(current_question)
    tab_param = f"&from_tab={from_tab}" if from_tab else ""
    prev_url = f"{mark_url}?qid={markable_questions[idx - 1].pk}{tab_param}" if idx > 0 else None
    next_url = f"{mark_url}?qid={markable_questions[idx + 1].pk}{tab_param}" if idx < len(markable_questions) - 1 else None
    return prev_url, next_url


def _should_show_summary(request, pending_questions, current_question, user_navigated_explicitly: bool) -> bool:
    summary_requested = request.GET.get("summary") == "1"
    all_done = not pending_questions and not current_question
    return (summary_requested or all_done) and not user_navigated_explicitly


def _handle_finalise(request, attempt, code) -> HttpResponse:
    from django.core.cache import cache
    attempt.finalised_at = timezone.now()
    attempt.finalised_by = request.user
    attempt.save(update_fields=["finalised_at", "finalised_by"])
    cache.delete(f"nav_counts_{request.user.pk}")
    logger.info("Attempt finalised: code=%s assessor=%s", code, request.user.username)
    from_tab = request.POST.get("from_tab") or "submitted"
    return redirect(reverse("assessment:assessor_attempts") + f"?tab={from_tab}")


def _handle_save_and_redirect(request, attempt, code, current_question, markable_questions, question_ids) -> HttpResponse:
    from django.core.cache import cache
    _save_question_score(request, attempt, current_question)
    cache.delete(f"nav_counts_{request.user.pk}")
    action = request.POST.get("action", "save")
    from_tab = request.POST.get("from_tab") or "submitted"
    if action == "done":
        return redirect(reverse("assessment:assessor_attempts") + f"?tab={from_tab}")
    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": code})
    remaining = _build_review_queue(markable_questions, _fetch_responses(attempt, question_ids))
    next_question = remaining[0] if remaining else None
    if next_question and action != "summary":
        return redirect(f"{url}?qid={next_question.pk}&from_tab={from_tab}&saved=1")
    return redirect(f"{url}?summary=1&from_tab={from_tab}")


# ── Attachment helpers ────────────────────────────────────────────────────────

def _get_working_sheet(attempt):
    try:
        return attempt.working_sheet
    except WorkingSheet.DoesNotExist:
        return None


def _get_writing_submission(attempt):
    try:
        return attempt.writing_submission
    except WritingSubmission.DoesNotExist:
        return None


def _get_paper_submitted(attempt):
    """Return True if learner answered Yes to GEN-G-HANDWRITE."""
    resp = Response.objects.filter(
        attempt=attempt, question__code="GEN-G-HANDWRITE"
    ).first()
    if not resp or not resp.response_json:
        return False
    try:
        return json.loads(resp.response_json).get("answer", "").strip().lower() == "yes"
    except (ValueError, AttributeError):
        return False


def _valid_file_magic(header: bytes) -> bool:
    return (
        header[:3] == b'\xff\xd8\xff'
        or header[:8] == b'\x89PNG\r\n\x1a\n'
        or (header[:4] == b'RIFF' and header[8:12] == b'WEBP')
        or header[:5] == b'%PDF-'
    )


def _needs_working_space(question) -> bool:
    """True if this question has expected working evidence in its answer key."""
    key = json.loads(question.answer_key_json or "{}")
    return bool(
        key.get("working_keywords")
        or key.get("flag_if_no_working")
        or key.get("flag_always")
    )


# ── Scoring transparency ──────────────────────────────────────────────────────

def _key_to_criteria_lines(key: dict) -> list[str]:
    """Convert a parsed answer_key_json into plain-English criterion strings."""
    lines = []

    if key.get("match"):
        expected = key["match"]
        lines.append(f"Match question — {len(expected)} pair(s).")
        lines.append(f"Marks per correct match: {key.get('marks_per_match', 1)}.")
        for target, word in expected.items():
            lines.append(f"  {target} → \"{word}\"")
        return lines

    if key.get("sentence_word"):
        word = key["sentence_word"]
        min_w = key.get("min_words", 4)
        lines.append(f"Learner must use the word \"{word}\" (or a common inflection) in a sentence.")
        lines.append(f"Minimum sentence length: {min_w} words.")
        lines.append("Always flagged for assessor review.")
        return lines

    if key.get("keyword_answer"):
        kws = key["keyword_answer"]
        partial = key.get("partial_marks", 0)
        lines.append(f"All of the following must appear in the response:")
        for kw in kws:
            lines.append(f"  • \"{kw}\"")
        if partial:
            lines.append(f"Partial marks ({partial}) if only some keywords found.")
        if key.get("flag_always"):
            lines.append("Always flagged for assessor review.")
        return lines

    if key.get("keyword_per_mark"):
        kws = key["keyword_per_mark"]
        mpp = key.get("marks_per_keyword", 1)
        lines.append(f"Each keyword found independently awards {mpp} mark(s):")
        for kw in kws:
            lines.append(f"  • \"{kw}\"")
        if key.get("flag_always"):
            lines.append("Always flagged for assessor review.")
        return lines

    if key.get("tiered_keyword"):
        lines.append("Tiered scoring — first matching tier wins:")
        for i, tier in enumerate(key["tiered_keyword"], 1):
            tier_marks = tier.get("marks", 0)
            parts = [f"Tier {i} → {tier_marks} mark(s)"]
            if tier.get("require_all"):
                parts.append(f"require ALL of: {', '.join(repr(k) for k in tier['require_all'])}")
            if tier.get("require_any"):
                parts.append(f"require ANY of: {', '.join(repr(k) for k in tier['require_any'])}")
            if tier.get("require_not"):
                parts.append(f"exclude if: {', '.join(repr(k) for k in tier['require_not'])}")
            if tier.get("min_words"):
                parts.append(f"min words: {tier['min_words']}")
            lines.append("  " + " | ".join(parts))
        no_match = key.get("no_match_note", "No criteria met → 0 marks.")
        lines.append(f"No tier matched: {no_match}")
        if key.get("flag_always"):
            lines.append("Always flagged for assessor review.")
        return lines

    answers = key.get("answers", [])
    if answers:
        lines.append(f"Accepted answer(s): {', '.join(repr(str(a)) for a in answers)}")
    working_kws = key.get("working_keywords", [])
    if working_kws:
        lines.append(f"Working evidence required (all must appear): {', '.join(repr(k) for k in working_kws)}")
        partial = key.get("partial_marks", 0)
        if partial:
            lines.append(f"Partial marks ({partial}) if answer correct but working absent.")
    if key.get("flag_always"):
        lines.append("Always flagged for assessor review.")
    elif key.get("flag_if_no_working"):
        lines.append("Flagged for review if working evidence absent.")
    return lines


# ── Views ─────────────────────────────────────────────────────────────────────

@login_required
@user_passes_test(is_assessor)
def assessor_mark_attempt(request, code: str):
    _expire_overdue_attempts()

    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )

    markable_questions = [
        q for q in
        Question.objects.filter(section__template=attempt.template, is_active=True)
        .select_related("section")
        .order_by("section__order", "order", "code")
        if _is_markable_question(q)
    ]

    question_ids = [q.pk for q in markable_questions]
    Response.objects.bulk_create(
        [Response(attempt=attempt, question_id=qid, response_json="") for qid in question_ids],
        ignore_conflicts=True,
    )
    responses_by_qid = _fetch_responses(attempt, question_ids)

    # Defensively auto-mark any responses the auto-marker missed.
    # Only check questions with auto_mark:true — manual questions (e.g. essays)
    # are intentionally left unscored here so they land in the review queue.
    has_unscored_auto = any(
        _score_for_response(responses_by_qid[q.pk]) is None
        for q in markable_questions
        if _question_answer_key(q).get("auto_mark")
    )
    if has_unscored_auto:
        auto_mark_attempt(attempt)
        responses_by_qid = _fetch_responses(attempt, question_ids)

    pending_questions = _build_review_queue(markable_questions, responses_by_qid)
    questions_by_pk = {q.pk: q for q in markable_questions}

    try:
        requested_qid = int(request.GET.get("qid", 0))
    except (TypeError, ValueError):
        requested_qid = 0

    current_question = (
        questions_by_pk.get(requested_qid)
        or (pending_questions[0] if pending_questions else None)
    )

    finalised = bool(attempt.finalised_at)
    can_mark = (not finalised and is_assessor(request.user)) or effective_is_moderator(request)

    if request.method == "POST":
        if not can_mark:
            return HttpResponseForbidden("This attempt has been finalised.")
        action = request.POST.get("action", "save")
        if action == "finalise":
            return _handle_finalise(request, attempt, code)
        if current_question:
            return _handle_save_and_redirect(request, attempt, code, current_question, markable_questions, question_ids)

    totals = _compute_marking_totals(markable_questions, responses_by_qid)
    user_navigated_explicitly = bool(
        requested_qid and current_question and questions_by_pk.get(requested_qid) == current_question
    )
    show_summary = _should_show_summary(request, pending_questions, current_question, user_navigated_explicitly)

    summary_rows = None
    if show_summary:
        summary_rows = [
            _build_marking_row(q, idx + 1, responses_by_qid)
            for idx, q in enumerate(markable_questions)
        ]

    current_row = (
        _build_marking_row(current_question, markable_questions.index(current_question) + 1, responses_by_qid)
        if current_question and not show_summary
        else None
    )

    sidebar_questions, sidebar_spot = _build_sidebar(markable_questions, responses_by_qid, current_question, code)

    from_tab = request.GET.get("from_tab") or "submitted"
    mark_url = reverse("assessment:assessor_mark_attempt", kwargs={"code": code})
    prev_url, next_url = _prev_next_urls(
        markable_questions,
        current_question if not show_summary else None,
        mark_url,
        from_tab,
    )

    q_meta = build_question_metadata(
        Question.objects.filter(section__template=attempt.template, is_active=True).select_related("section")
    )
    nqf = compute_nqf_placement(attempt, q_meta)

    return render(
        request,
        "assessment/assessor_mark_attempt.html",
        {
            "attempt": attempt,
            "nqf": nqf,
            "row": current_row,
            "saved": request.GET.get("saved") == "1",
            "total_awarded": totals.awarded,
            "total_available": totals.available,
            "scored_count": totals.scored_count,
            "pending_count": len(pending_questions),
            "total_questions": len(markable_questions),
            "show_summary": show_summary,
            "summary_rows": summary_rows,
            "sidebar_questions": sidebar_questions,
            "sidebar_spot": sidebar_spot,
            "prev_url": prev_url,
            "next_url": next_url,
            "from_tab": from_tab,
            "working_sheet": _get_working_sheet(attempt),
            "writing_submission": _get_writing_submission(attempt),
            "paper_submitted": _get_paper_submitted(attempt),
            "is_finalised": finalised,
            "can_mark": can_mark,
            "can_unlock": finalised and effective_is_moderator(request) and attempt.template.moderation_mode == AssessmentTemplate.MODERATION_FULL,
        },
    )


@login_required
@user_passes_test(is_moderator)
def assessor_unlock_attempt(request, code: str):
    if request.method != "POST":
        return HttpResponseForbidden()
    attempt = get_object_or_404(Attempt, code=code)
    if attempt.template.moderation_mode != AssessmentTemplate.MODERATION_FULL:
        return HttpResponseForbidden("This template is audit-only and cannot be unlocked.")
    from django.core.cache import cache
    attempt.finalised_at = None
    attempt.finalised_by = None
    attempt.save(update_fields=["finalised_at", "finalised_by"])
    cache.delete(f"nav_counts_{request.user.pk}")
    logger.warning("Attempt unlocked: code=%s moderator=%s", code, request.user.username)
    return redirect(reverse("assessment:assessor_mark_attempt", kwargs={"code": code}))


@login_required
@user_passes_test(is_moderator)
def assessor_moderation(request):
    attempts = (
        Attempt.objects
        .filter(finalised_at__isnull=False, moderated_at__isnull=True)
        .select_related("learner", "template", "finalised_by")
        .order_by("-finalised_at")
    )
    return render(request, "assessment/assessor_moderation.html", {
        "attempts": attempts,
    })


@login_required
@user_passes_test(is_moderator)
def assessor_approve_moderation(request, code: str):
    if request.method != "POST":
        return HttpResponseForbidden()
    attempt = get_object_or_404(Attempt, code=code, finalised_at__isnull=False)
    from django.core.cache import cache
    attempt.moderated_at = timezone.now()
    attempt.moderated_by = request.user
    attempt.save(update_fields=["moderated_at", "moderated_by"])
    cache.delete(f"nav_counts_{request.user.pk}")
    logger.info("Attempt moderation approved: code=%s moderator=%s", code, request.user.username)
    return redirect("assessment:assessor_moderation")


@login_required
@user_passes_test(is_auditor)
def assessor_archive(request):
    attempts = (
        Attempt.objects
        .filter(moderated_at__isnull=False)
        .select_related("learner", "template", "finalised_by", "moderated_by")
        .order_by("-moderated_at")
    )
    return render(request, "assessment/assessor_archive.html", {
        "attempts": attempts,
    })


@login_required
@user_passes_test(is_moderator)
def assessor_activity_report(request):
    """Per-assessor activity breakdown: finalisations, moderations, manual marks."""
    from django.db.models import Count, Min, Max
    from django.db.models.functions import TruncDate

    q_from = (request.GET.get("from") or "").strip()
    q_to   = (request.GET.get("to")   or "").strip()

    def _apply_date_filter(qs, field):
        if q_from:
            try:
                qs = qs.filter(**{f"{field}__date__gte": q_from})
            except Exception:
                pass
        if q_to:
            try:
                qs = qs.filter(**{f"{field}__date__lte": q_to})
            except Exception:
                pass
        return qs

    # ── Finalisations ──
    fin_qs = _apply_date_filter(
        Attempt.objects.filter(finalised_at__isnull=False), "finalised_at"
    )
    fin_rows = list(
        fin_qs
        .values("finalised_by__username", "finalised_by__first_name", "finalised_by__last_name")
        .annotate(count=Count("pk"), first_on=Min("finalised_at"), last_on=Max("finalised_at"))
        .order_by("-count")
    )

    # ── Moderations ──
    mod_qs = _apply_date_filter(
        Attempt.objects.filter(moderated_at__isnull=False), "moderated_at"
    )
    mod_by_user = {
        r["moderated_by__username"]: r["count"]
        for r in mod_qs.values("moderated_by__username").annotate(count=Count("pk"))
    }

    # ── Manual marks (first-time scores entered by a human) ──
    marks_qs = _apply_date_filter(
        ScoreAuditLog.objects.filter(mode="manual", action="created"), "changed_at"
    )
    marks_by_user = {
        r["changed_by__username"]: r["count"]
        for r in marks_qs.values("changed_by__username")
                         .annotate(count=Count("score__response__attempt", distinct=True))
    }

    # ── Unified per-assessor summary ──
    def _name(r):
        fn = (r.get("finalised_by__first_name") or "").strip()
        ln = (r.get("finalised_by__last_name")  or "").strip()
        return f"{fn} {ln}".strip() or r.get("finalised_by__username") or "System"

    assessors = [
        {
            "username":      r["finalised_by__username"] or "",
            "name":          _name(r),
            "finalisations": r["count"],
            "moderations":   mod_by_user.get(r["finalised_by__username"], 0),
            "marks":         marks_by_user.get(r["finalised_by__username"], 0),
            "first_on":      r["first_on"],
            "last_on":       r["last_on"],
        }
        for r in fin_rows
    ]

    total_fin    = sum(a["finalisations"] for a in assessors)
    total_mod    = sum(a["moderations"]   for a in assessors)
    total_marks  = sum(a["marks"]         for a in assessors)
    assessor_count = sum(1 for a in assessors if a["username"])
    period_from  = min((a["first_on"] for a in assessors), default=None)
    period_to    = max((a["last_on"]  for a in assessors), default=None)

    # ── Chart: grouped bar — per assessor × activity type ──
    assessor_names = json.dumps([a["name"]          for a in assessors])
    fin_data       = json.dumps([a["finalisations"] for a in assessors])
    mod_data       = json.dumps([a["moderations"]   for a in assessors])
    marks_data     = json.dumps([a["marks"]         for a in assessors])

    # ── Chart: single line — finalisations + moderations + assessed attempts since inception (unfiltered) ──
    from collections import defaultdict
    inception_by_day: dict[str, int] = defaultdict(int)
    for r in (Attempt.objects.filter(finalised_at__isnull=False)
              .annotate(day=TruncDate("finalised_at")).values("day").annotate(count=Count("pk"))):
        inception_by_day[str(r["day"])] += r["count"]
    for r in (Attempt.objects.filter(moderated_at__isnull=False)
              .annotate(day=TruncDate("moderated_at")).values("day").annotate(count=Count("pk"))):
        inception_by_day[str(r["day"])] += r["count"]
    for r in (ScoreAuditLog.objects.filter(mode="manual", action="created")
              .annotate(day=TruncDate("changed_at")).values("day")
              .annotate(count=Count("score__response__attempt", distinct=True))):
        inception_by_day[str(r["day"])] += r["count"]

    inception_dates  = sorted(inception_by_day.keys())
    inception_labels = json.dumps(inception_dates)
    inception_data   = json.dumps([inception_by_day[d] for d in inception_dates])

    return render(request, "assessment/assessor_activity_report.html", {
        "assessors":       assessors,
        "total_fin":       total_fin,
        "total_mod":       total_mod,
        "total_marks":     total_marks,
        "assessor_count":  assessor_count,
        "period_from":     period_from,
        "period_to":       period_to,
        "q_from":          q_from,
        "q_to":            q_to,
        "assessor_names":  assessor_names,
        "fin_data":        fin_data,
        "mod_data":        mod_data,
        "marks_data":      marks_data,
        "inception_labels": inception_labels,
        "inception_data":   inception_data,
        "has_data":        bool(assessors),
        "has_daily":       bool(inception_dates),
    })


@login_required
@user_passes_test(is_moderator)
def assessor_activity_detail(request, username: str):
    from collections import defaultdict
    from django.contrib.auth import get_user_model
    from django.db.models import Count, Max
    from django.db.models.functions import TruncDate
    from django.shortcuts import get_object_or_404

    User = get_user_model()
    assessor = get_object_or_404(User, username=username)

    q_from = (request.GET.get("from") or "").strip()
    q_to   = (request.GET.get("to")   or "").strip()

    def _apply_date_filter(qs, field):
        if q_from:
            try: qs = qs.filter(**{f"{field}__date__gte": q_from})
            except Exception: pass
        if q_to:
            try: qs = qs.filter(**{f"{field}__date__lte": q_to})
            except Exception: pass
        return qs

    fin_qs  = _apply_date_filter(
        Attempt.objects.filter(finalised_by=assessor, finalised_at__isnull=False), "finalised_at"
    )
    mod_qs  = _apply_date_filter(
        Attempt.objects.filter(moderated_by=assessor, moderated_at__isnull=False), "moderated_at"
    )
    marks_qs = _apply_date_filter(
        ScoreAuditLog.objects.filter(changed_by=assessor, mode="manual"), "changed_at"
    )

    total_fin     = fin_qs.count()
    total_mod     = mod_qs.count()
    total_marked  = marks_qs.filter(action="created").values("score__response__attempt").distinct().count()
    total_creates = marks_qs.filter(action="created").count()
    total_updates = marks_qs.filter(action="updated").count()
    correction_pct = round(total_updates / total_creates * 100, 1) if total_creates else 0

    # Per-attempt table: finalised attempts with marking lag
    attempts = list(
        fin_qs.select_related("learner").order_by("-finalised_at")
    )
    attempt_ids = [a.pk for a in attempts]
    last_mark_by_attempt = {
        r["score__response__attempt_id"]: r["last_mark"]
        for r in ScoreAuditLog.objects.filter(
            changed_by=assessor, mode="manual",
            score__response__attempt_id__in=attempt_ids,
        ).values("score__response__attempt_id").annotate(last_mark=Max("changed_at"))
    }

    attempt_rows = []
    for a in attempts:
        last_mark = last_mark_by_attempt.get(a.pk)
        lag_min = None
        if last_mark and a.finalised_at:
            delta = a.finalised_at - last_mark
            lag_min = max(0, int(delta.total_seconds() / 60))
        if lag_min is None:
            lag_str = "—"
        elif lag_min < 1:
            lag_str = "< 1 min"
        elif lag_min < 60:
            lag_str = f"{lag_min} min"
        else:
            h, m = divmod(lag_min, 60)
            lag_str = f"{h}h {m}m" if m else f"{h}h"

        attempt_rows.append({
            "code":         a.code,
            "learner":      f"{a.learner.first_names} {a.learner.surname}" if a.learner else "—",
            "finalised_at": a.finalised_at,
            "moderated":    a.moderated_at is not None,
            "lag_str":      lag_str,
        })

    # Timeline: activity per day since inception for this assessor (unfiltered)
    daily: dict[str, int] = defaultdict(int)
    for r in (Attempt.objects.filter(finalised_by=assessor, finalised_at__isnull=False)
              .annotate(day=TruncDate("finalised_at")).values("day").annotate(count=Count("pk"))):
        daily[str(r["day"])] += r["count"]
    for r in (Attempt.objects.filter(moderated_by=assessor, moderated_at__isnull=False)
              .annotate(day=TruncDate("moderated_at")).values("day").annotate(count=Count("pk"))):
        daily[str(r["day"])] += r["count"]

    daily_dates  = sorted(daily.keys())
    daily_labels = json.dumps(daily_dates)
    daily_data   = json.dumps([daily[d] for d in daily_dates])

    return render(request, "assessment/assessor_activity_detail.html", {
        "assessor":       assessor,
        "total_fin":      total_fin,
        "total_mod":      total_mod,
        "total_marked":   total_marked,
        "correction_pct": correction_pct,
        "attempt_rows":   attempt_rows,
        "daily_labels":   daily_labels,
        "daily_data":     daily_data,
        "has_daily":      bool(daily_dates),
        "q_from":         q_from,
        "q_to":           q_to,
    })


@login_required
@user_passes_test(is_auditor)
def assessor_auditor_reopen(request, code: str):
    if request.method != "POST":
        return HttpResponseForbidden()
    attempt = get_object_or_404(Attempt, code=code, moderated_at__isnull=False)
    if attempt.template.moderation_mode != AssessmentTemplate.MODERATION_FULL:
        return HttpResponseForbidden("Audit-only templates cannot be re-opened.")
    from django.core.cache import cache
    attempt.moderated_at = None
    attempt.moderated_by = None
    attempt.save(update_fields=["moderated_at", "moderated_by"])
    cache.delete(f"nav_counts_{request.user.pk}")
    logger.warning("Attempt re-opened from archive: code=%s auditor=%s", code, request.user.username)
    return redirect("assessment:assessor_archive")


@login_required
@user_passes_test(is_assessor)
def assessor_auto_marked_attempt(request, code: str):
    """Read-only view of all scored questions not requiring assessor input."""
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )

    markable_questions = [
        q for q in
        Question.objects.filter(section__template=attempt.template, is_active=True)
        .select_related("section")
        .order_by("section__order", "order", "code")
        if _is_markable_question(q)
    ]

    question_ids = [q.pk for q in markable_questions]
    Response.objects.bulk_create(
        [Response(attempt=attempt, question_id=qid, response_json="") for qid in question_ids],
        ignore_conflicts=True,
    )
    responses_by_qid = _fetch_responses(attempt, question_ids)

    rows = []
    for question in markable_questions:
        score = _score_for_response(responses_by_qid[question.pk])
        if score is None or _requires_assessor_attention(score):
            continue
        rubric = _extract_rubric(question)
        rows.append({
            "question": question,
            "response_text": _render_response_for_marking(question, responses_by_qid[question.pk]),
            "score": score,
            "awarded": float(score.points),
            "max_points": _max_points_for_question(question, rubric),
            "notes": _score_notes(score),
            "mode": score.rubric_json.get("mode", "") if isinstance(score.rubric_json, dict) else "",
        })

    review_count = len(markable_questions) - len(rows)

    return render(request, "assessment/assessor_auto_marked_attempt.html", {
        "attempt": attempt,
        "rows": rows,
        "review_count": review_count,
    })


@login_required
@user_passes_test(is_assessor)
def assessor_new_attempt(request):
    import uuid as _uuid
    latest_template = AssessmentTemplate.objects.order_by("-created_at").first()

    if latest_template is None:
        return render(
            request,
            "assessment/assessor_new_attempt.html",
            {"error": "No assessment template exists yet."},
        )

    if request.method == "POST":
        form = AttemptForm(request.POST)

        if form.is_valid():
            learner = Learner.objects.create(
                first_names="Temp",
                surname="Learner",
                id_number=_uuid.uuid4().hex[:13],
            )

            attempt = form.save(commit=False)
            attempt.learner = learner
            attempt.save()

            logger.info(
                "Attempt code created: code=%s template=%r assessor=%s",
                attempt.code, attempt.template.name, request.user.username,
            )
            return render(
                request,
                "assessment/assessor_new_attempt.html",
                {
                    "attempt": attempt,
                    "form": form,
                    "template": attempt.template,
                },
            )
    else:
        form = AttemptForm(initial={"template": latest_template})

    return render(
        request,
        "assessment/assessor_new_attempt.html",
        {
            "form": form,
            "template": latest_template,
        },
    )


@login_required
@user_passes_test(is_assessor)
def assessor_review_queue(request):
    return redirect(reverse("assessment:assessor_attempts") + "?tab=submitted")


# ── Working sheet and writing submission uploads ───────────────────────────────

@login_required
@user_passes_test(is_assessor)
def assessor_working_sheet_upload(request, code: str):
    """Upload or replace the working sheet scan for an attempt."""
    import base64

    attempt = get_object_or_404(Attempt.objects.select_related("learner"), code=code)

    if request.method != "POST":
        return redirect("assessment:assessor_mark_attempt", code=code)

    uploaded_file = request.FILES.get("working_sheet")
    if not uploaded_file:
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=1")

    allowed_types = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
    if uploaded_file.content_type not in allowed_types:
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=2")

    # M-5: Validate magic bytes — reject files that lie about their content-type.
    header = uploaded_file.read(12)
    uploaded_file.seek(0)
    if not _valid_file_magic(header):
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=2")

    encoded = base64.b64encode(uploaded_file.read()).decode("utf-8")

    WorkingSheet.objects.update_or_create(
        attempt=attempt,
        defaults={
            "uploaded_by": request.user,
            "content_type": uploaded_file.content_type,
            "original_filename": uploaded_file.name,
            "data": encoded,
        },
    )

    url = reverse("assessment:assessor_mark_attempt", kwargs={"code": code})
    return redirect(f"{url}?ws_saved=1")


@login_required
@user_passes_test(is_assessor)
def assessor_working_sheet_image(request, code: str):
    """Serve the stored working sheet image/PDF."""
    import base64

    attempt = get_object_or_404(Attempt, code=code)
    sheet = get_object_or_404(WorkingSheet, attempt=attempt)
    data = base64.b64decode(sheet.data)
    response = HttpResponse(data, content_type=sheet.content_type)
    safe_name = Path(sheet.original_filename or f"working_sheet_{code}").name
    response["Content-Disposition"] = f'inline; filename="{safe_name}"'
    return response


@login_required
@user_passes_test(is_assessor)
def assessor_writing_submission_upload(request, code: str):
    """Upload or replace the handwritten essay scan for an attempt."""
    import base64

    attempt = get_object_or_404(Attempt.objects.select_related("learner"), code=code)

    if request.method != "POST":
        return redirect("assessment:assessor_mark_attempt", code=code)

    uploaded_file = request.FILES.get("writing_submission")
    if not uploaded_file:
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=1")

    allowed_types = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
    if uploaded_file.content_type not in allowed_types:
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=2")

    header = uploaded_file.read(12)
    uploaded_file.seek(0)
    if not _valid_file_magic(header):
        return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}?ws_error=2")

    encoded = base64.b64encode(uploaded_file.read()).decode("utf-8")

    WritingSubmission.objects.update_or_create(
        attempt=attempt,
        defaults={
            "uploaded_by": request.user,
            "content_type": uploaded_file.content_type,
            "original_filename": uploaded_file.name,
            "data": encoded,
        },
    )

    q = Question.objects.filter(section__template=attempt.template, code="GEN-G-WRITE").first()
    qid_param = f"?qid={q.pk}" if q else ""
    return redirect(f"{reverse('assessment:assessor_mark_attempt', kwargs={'code': code})}{qid_param}&ws_saved=1")


@login_required
@user_passes_test(is_assessor)
def assessor_writing_submission_image(request, code: str):
    """Serve the stored handwritten essay image/PDF."""
    import base64

    attempt = get_object_or_404(Attempt, code=code)
    submission = get_object_or_404(WritingSubmission, attempt=attempt)
    data = base64.b64decode(submission.data)
    response = HttpResponse(data, content_type=submission.content_type)
    safe_name = Path(submission.original_filename or f"writing_{code}").name
    response["Content-Disposition"] = f'inline; filename="{safe_name}"'
    return response


@login_required
@user_passes_test(is_assessor)
def assessor_working_sheet_print(request, code: str):
    """Printable working sheet for a specific attempt."""
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template", "session"),
        code=code,
    )
    _WORKING_SHEET_CODES = {
        "NUM-A-4", "NUM-B-1", "NUM-B-2", "NUM-B-3",
        "NUM-C-1", "NUM-C-3", "NUM-D-2",
    }
    working_questions = list(
        Question.objects.filter(
            section__template=attempt.template,
            is_active=True,
            code__in=_WORKING_SHEET_CODES,
        ).order_by("section__order", "order")
    )
    essay_question = (
        Question.objects
        .filter(section__template=attempt.template, is_active=True, code="GEN-G-WRITE")
        .first()
    )
    essay_prompt = ""
    essay_criteria = []
    if essay_question:
        spec = json.loads(essay_question.spec_json or "{}")
        key = json.loads(essay_question.answer_key_json or "{}")
        essay_prompt = spec.get("prompt", essay_question.prompt)
        essay_criteria = [
            {"label": c["label"], "max_points": c["max_points"]}
            for c in key.get("criteria", [])
        ]
    return render(request, "assessment/working_sheet_print.html", {
        "attempt": attempt,
        "working_questions": working_questions,
        "essay_question": essay_question,
        "essay_prompt": essay_prompt,
        "essay_criteria": essay_criteria,
    })


# ── Scoring transparency ──────────────────────────────────────────────────────

@login_required
@user_passes_test(is_staff)
def assessor_scoring_breakdown(request, code: str):
    """Staff-only: how each question was scored — criteria, response, auto-marker decision."""
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )

    questions = (
        Question.objects
        .filter(section__template=attempt.template, is_active=True)
        .select_related("section")
        .order_by("section__order", "order", "code")
    )

    question_ids = [q.pk for q in questions]
    responses_by_qid = {
        r.question_id: r
        for r in Response.objects.filter(
            attempt=attempt, question_id__in=question_ids
        ).select_related("score")
    }

    rows = []
    for question in questions:
        response = responses_by_qid.get(question.pk)
        key = json.loads(question.answer_key_json or "{}")
        auto_markable = bool(key.get("auto_mark"))

        score = None
        rubric = {}
        if response:
            score = getattr(response, "score", None)
            if score:
                rubric = score.rubric_json if isinstance(score.rubric_json, dict) else {}

        criteria_lines = _key_to_criteria_lines(key) if auto_markable else []

        rows.append({
            "question": question,
            "response_text": _render_response_for_marking(question, response) if response else "",
            "auto_markable": auto_markable,
            "criteria_lines": criteria_lines,
            "score": score,
            "awarded": float(score.points) if score else None,
            "max_marks": float(question.max_marks or 0),
            "notes": rubric.get("notes", ""),
            "needs_review": rubric.get("needs_review", False),
            "mode": rubric.get("mode", ""),
            "marking_notes": question.marking_notes or "",
        })

    return render(request, "assessment/assessor_scoring_breakdown.html", {
        "attempt": attempt,
        "rows": rows,
    })


@login_required
@user_passes_test(is_auditor)
def assessor_score_audit_log(request, code: str):
    """Per-attempt score audit log — every score creation and change."""
    attempt = get_object_or_404(
        Attempt.objects.select_related("learner", "template"),
        code=code,
    )
    entries = (
        ScoreAuditLog.objects
        .filter(score__response__attempt=attempt)
        .select_related("score__response__question", "changed_by")
        .order_by("changed_at")
    )
    return render(request, "assessment/assessor_score_audit_log.html", {
        "attempt": attempt,
        "entries": entries,
    })


