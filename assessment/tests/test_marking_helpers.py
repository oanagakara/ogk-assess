"""
Unit tests for pure helper functions in assessment/views/marking.py
and assessment/views/_common.py.
No HTTP calls needed — functions are imported and called directly.
"""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from assessment.views.marking import (
    _normalise_rubric_item,
    _extract_rubric,
    _find_rubric_candidates,
    _clamped_float,
    _render_text,
    _render_form_fill,
    _render_match,
    _render_response_for_marking,
    _build_response_display,
    _requires_assessor_attention,
    _review_type,
    _score_notes,
    _saved_criteria_from_score,
    _rubric_display_rows,
    _max_points_for_question,
    _is_auto_done,
    _is_markable_question,
)
from assessment.views._common import (
    _safe_json_loads,
    _question_spec,
    _question_answer_key,
    _is_layout_only_question,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_question(kind="text", max_marks=2, spec_json=None, answer_key_json=None, prompt="Test?"):
    return SimpleNamespace(
        kind=kind,
        max_marks=max_marks,
        spec_json=json.dumps(spec_json or {}),
        answer_key_json=json.dumps(answer_key_json or {}),
        prompt=prompt,
        MATCH="match",
    )


def make_score(points=2, max_points=2, rubric_json=None):
    return SimpleNamespace(
        points=points,
        max_points=max_points,
        rubric_json=rubric_json or {"mode": "manual", "needs_review": False},
    )


def make_response(response_json=None):
    return SimpleNamespace(
        response_json=json.dumps(response_json) if response_json is not None else None,
        score=None,
    )


# ── _safe_json_loads ──────────────────────────────────────────────────────────

class TestSafeJsonLoads:
    def test_valid_json(self):
        assert _safe_json_loads('{"a": 1}', {}) == {"a": 1}

    def test_none_returns_default(self):
        assert _safe_json_loads(None, {}) == {}

    def test_invalid_json_returns_default(self):
        assert _safe_json_loads("not json", []) == []

    def test_empty_string_returns_default(self):
        assert _safe_json_loads("", {}) == {}


# ── _question_spec / _question_answer_key ─────────────────────────────────────

class TestQuestionAccessors:
    def test_spec_parses_json(self):
        q = make_question(spec_json={"layout": "form_fill"})
        assert _question_spec(q)["layout"] == "form_fill"

    def test_answer_key_parses_json(self):
        q = make_question(answer_key_json={"auto_mark": True})
        assert _question_answer_key(q)["auto_mark"] is True

    def test_missing_spec_returns_empty(self):
        q = SimpleNamespace(spec_json=None, answer_key_json=None)
        assert _question_spec(q) == {}

    def test_missing_answer_key_returns_empty(self):
        q = SimpleNamespace(spec_json=None, answer_key_json=None)
        assert _question_answer_key(q) == {}


# ── _is_layout_only_question ──────────────────────────────────────────────────

class TestIsLayoutOnlyQuestion:
    def test_info_only(self):
        q = make_question(spec_json={"layout": "info_only"})
        assert _is_layout_only_question(q) is True

    def test_passage_only(self):
        q = make_question(spec_json={"layout": "passage_only"})
        assert _is_layout_only_question(q) is True

    def test_info_only_hyphenated(self):
        q = make_question(spec_json={"layout": "info-only"})
        assert _is_layout_only_question(q) is True

    def test_normal_question(self):
        q = make_question(spec_json={"layout": "text"})
        assert _is_layout_only_question(q) is False

    def test_no_layout(self):
        q = make_question(spec_json={})
        assert _is_layout_only_question(q) is False


# ── _normalise_rubric_item ────────────────────────────────────────────────────

class TestNormaliseRubricItem:
    def test_extracts_label(self):
        item = {"label": "MOTIVATION", "max_points": 3, "key": "motivation"}
        result = _normalise_rubric_item(item, 1)
        assert result["label"] == "MOTIVATION"

    def test_falls_back_to_criterion(self):
        item = {"criterion": "SKILLS", "max_points": 2}
        result = _normalise_rubric_item(item, 1)
        assert result["label"] == "SKILLS"

    def test_falls_back_to_index_label(self):
        item = {"max_points": 1}
        result = _normalise_rubric_item(item, 3)
        assert result["label"] == "Criterion 3"

    def test_max_points_float(self):
        item = {"label": "X", "max_points": "3"}
        result = _normalise_rubric_item(item, 1)
        assert result["max_points"] == 3.0

    def test_invalid_max_points_defaults_to_zero(self):
        item = {"label": "X", "max_points": "bad"}
        result = _normalise_rubric_item(item, 1)
        assert result["max_points"] == 0.0

    def test_key_slugified(self):
        item = {"label": "My Criterion", "max_points": 1}
        result = _normalise_rubric_item(item, 1)
        assert result["key"] == "my-criterion"

    def test_explicit_key_used(self):
        item = {"label": "X", "key": "motivation", "max_points": 1}
        result = _normalise_rubric_item(item, 1)
        assert result["key"] == "motivation"


# ── _extract_rubric ───────────────────────────────────────────────────────────

class TestExtractRubric:
    def test_extracts_from_answer_key(self):
        q = make_question(answer_key_json={
            "criteria": [
                {"key": "motivation", "label": "MOTIVATION", "max_points": 3},
                {"key": "skills", "label": "SKILLS", "max_points": 2},
            ]
        })
        rubric = _extract_rubric(q)
        assert len(rubric) == 2
        assert rubric[0]["label"] == "MOTIVATION"

    def test_returns_empty_when_no_rubric(self):
        q = make_question(answer_key_json={"auto_mark": True})
        assert _extract_rubric(q) == []

    def test_non_dict_items_skipped(self):
        q = make_question(answer_key_json={
            "criteria": ["not a dict", {"label": "X", "max_points": 1}]
        })
        rubric = _extract_rubric(q)
        assert len(rubric) == 1


# ── _clamped_float ────────────────────────────────────────────────────────────

class TestClampedFloat:
    def test_within_bounds(self):
        assert _clamped_float("2", 3) == 2.0

    def test_clamps_at_upper(self):
        assert _clamped_float("5", 3) == 3.0

    def test_clamps_at_zero(self):
        assert _clamped_float("-1", 3) == 0.0

    def test_invalid_string_gives_zero(self):
        assert _clamped_float("abc", 3) == 0.0

    def test_none_gives_zero(self):
        assert _clamped_float(None, 3) == 0.0

    def test_exact_upper_bound(self):
        assert _clamped_float("3", 3) == 3.0


# ── _render_text / _render_form_fill / _render_match ─────────────────────────

class TestRenderText:
    def test_extracts_answer(self):
        assert _render_text({"answer": "hello"}) == "hello"

    def test_strips_whitespace(self):
        assert _render_text({"answer": "  hi  "}) == "hi"

    def test_empty_dict(self):
        assert _render_text({}) == ""

    def test_non_dict(self):
        assert _render_text("raw") == "raw"


class TestRenderFormFill:
    def test_uses_field_labels(self):
        spec = {"fields": [{"name": "first_name", "label": "First Name"}, {"name": "age", "label": "Age"}]}
        data = {"first_name": "Thabo", "age": "25"}
        result = _render_form_fill(spec, data)
        assert "First Name: Thabo" in result
        assert "Age: 25" in result

    def test_falls_back_to_field_name(self):
        spec = {"fields": [{"name": "cell"}]}
        data = {"cell": "0821234567"}
        result = _render_form_fill(spec, data)
        assert "cell: 0821234567" in result

    def test_no_fields_uses_raw_dict(self):
        spec = {}
        result = _render_form_fill(spec, {"key": "value"})
        assert "key: value" in result


class TestRenderMatch:
    def test_uses_target_text(self):
        spec = {"targets": [{"id": "t1", "text": "A public objection"}]}
        data = {"t1": "protest"}
        result = _render_match(spec, data)
        assert "A public objection: protest" in result

    def test_falls_back_to_tid_when_no_target(self):
        spec = {"targets": []}
        data = {"t1": "protest"}
        result = _render_match(spec, data)
        assert "t1: protest" in result


# ── _requires_assessor_attention ─────────────────────────────────────────────

class TestRequiresAssessorAttention:
    def test_none_score_requires_attention(self):
        assert _requires_assessor_attention(None) is True

    def test_needs_review_true(self):
        score = make_score(rubric_json={"needs_review": True})
        assert _requires_assessor_attention(score) is True

    def test_needs_review_false(self):
        score = make_score(rubric_json={"needs_review": False})
        assert _requires_assessor_attention(score) is False

    def test_non_dict_rubric_requires_attention(self):
        score = SimpleNamespace(rubric_json="not a dict")
        assert _requires_assessor_attention(score) is True

    def test_missing_needs_review_key_defaults_false(self):
        score = make_score(rubric_json={"mode": "auto"})
        assert _requires_assessor_attention(score) is False


# ── _review_type ──────────────────────────────────────────────────────────────

class TestReviewType:
    def test_none_score_is_manual(self):
        assert _review_type(None) == "manual"

    def test_manual_score_is_manual(self):
        score = make_score(rubric_json={"mode": "manual", "auto_marked": False})
        assert _review_type(score) == "manual"

    def test_verify_working_is_process(self):
        score = make_score(rubric_json={"auto_marked": True, "verify_working": True})
        assert _review_type(score) == "process"

    def test_answer_found_no_working_is_process(self):
        score = make_score(rubric_json={
            "auto_marked": True, "verify_working": False,
            "answer_found": True, "working_found": False
        })
        assert _review_type(score) == "process"

    def test_auto_flagged_is_comprehension(self):
        score = make_score(rubric_json={"auto_marked": True})
        assert _review_type(score) == "comprehension"


# ── _score_notes ──────────────────────────────────────────────────────────────

class TestScoreNotes:
    def test_returns_notes(self):
        score = make_score(rubric_json={"notes": "Good work"})
        assert _score_notes(score) == "Good work"

    def test_none_score(self):
        assert _score_notes(None) == ""

    def test_non_dict_rubric(self):
        score = SimpleNamespace(rubric_json="plain string")
        assert _score_notes(score) == ""

    def test_missing_notes_key(self):
        score = make_score(rubric_json={"mode": "auto"})
        assert _score_notes(score) == ""


# ── _saved_criteria_from_score ────────────────────────────────────────────────

class TestSavedCriteriaFromScore:
    def test_extracts_by_key(self):
        score = make_score(rubric_json={
            "criteria": [{"key": "motivation", "points": 2, "feedback": "Good"}]
        })
        result = _saved_criteria_from_score(score)
        assert "motivation" in result
        assert result["motivation"]["points"] == 2

    def test_none_score_returns_empty(self):
        assert _saved_criteria_from_score(None) == {}

    def test_missing_criteria_key(self):
        score = make_score(rubric_json={"mode": "manual"})
        assert _saved_criteria_from_score(score) == {}

    def test_items_without_key_skipped(self):
        score = make_score(rubric_json={"criteria": [{"points": 1}]})
        assert _saved_criteria_from_score(score) == {}


# ── _rubric_display_rows ──────────────────────────────────────────────────────

class TestRubricDisplayRows:
    def test_merges_saved_values(self):
        rubric = [{"key": "motivation", "label": "MOTIVATION", "max_points": 3}]
        saved = {"motivation": {"points": 2, "feedback": "Good effort"}}
        rows = _rubric_display_rows(rubric, saved)
        assert rows[0]["value"] == 2
        assert rows[0]["feedback"] == "Good effort"

    def test_defaults_when_not_saved(self):
        rubric = [{"key": "skills", "label": "SKILLS", "max_points": 3}]
        rows = _rubric_display_rows(rubric, {})
        assert rows[0]["value"] == ""
        assert rows[0]["feedback"] == ""


# ── _max_points_for_question ──────────────────────────────────────────────────

class TestMaxPointsForQuestion:
    def test_uses_rubric_sum_when_present(self):
        rubric = [{"max_points": 3}, {"max_points": 2}]
        q = make_question(max_marks=10)
        assert _max_points_for_question(q, rubric) == 5.0

    def test_falls_back_to_max_marks(self):
        q = make_question(max_marks=4)
        assert _max_points_for_question(q, []) == 4.0

    def test_none_max_marks(self):
        q = make_question(max_marks=None)
        assert _max_points_for_question(q, []) == 0.0


# ── _is_auto_done ─────────────────────────────────────────────────────────────

class TestIsAutoDone:
    def test_none_score_not_done(self):
        assert _is_auto_done(None) is False

    def test_needs_review_not_done(self):
        score = make_score(rubric_json={"needs_review": True})
        assert _is_auto_done(score) is False

    def test_no_review_needed_is_done(self):
        score = make_score(rubric_json={"needs_review": False})
        assert _is_auto_done(score) is True


# ── _is_markable_question ─────────────────────────────────────────────────────

class TestIsMarkableQuestion:
    def test_layout_only_not_markable(self):
        q = make_question(spec_json={"layout": "info_only"}, max_marks=0)
        assert _is_markable_question(q) is False

    def test_question_with_marks_is_markable(self):
        q = make_question(max_marks=2)
        assert _is_markable_question(q) is True

    def test_zero_marks_not_markable(self):
        q = make_question(max_marks=0)
        assert _is_markable_question(q) is False

    def test_rubric_makes_markable_even_with_zero_max_marks(self):
        q = make_question(
            max_marks=0,
            answer_key_json={"criteria": [{"key": "x", "label": "X", "max_points": 3}]}
        )
        assert _is_markable_question(q) is True
