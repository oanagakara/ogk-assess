"""
Tests for assessment/auto_mark.py

Pure-logic functions use SimpleNamespace mocks (no DB).
auto_mark_attempt tests use @pytest.mark.django_db.
"""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import patch

import anthropic

from assessment.auto_mark import (
    _normalize,
    _number_in_text,
    _answer_correct,
    _working_present,
    _auto_mark_match,
    _auto_mark_sentence_word,
    _auto_mark_keyword_answer,
    _auto_mark_keyword_per_mark,
    _auto_mark_tiered_keyword,
    _create_message_with_retry,
    auto_mark_response,
    _is_blank_response,
    _zero_score,
    auto_mark_attempt,
)
from assessment.metrics import ai_marking_failures_total


def _fake_transient_error():
    """A retryable anthropic error, built without any real HTTP call."""
    import httpx
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=503, request=request)
    return anthropic.InternalServerError("transient failure", response=response, body=None)


def _fake_auth_error():
    """A non-retryable anthropic error — retrying it would never succeed."""
    import httpx
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=401, request=request)
    return anthropic.AuthenticationError("bad key", response=response, body=None)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def q(kind="text", max_marks=1, answer_key=None):
    return SimpleNamespace(
        kind=kind,
        max_marks=max_marks,
        answer_key_json=json.dumps(answer_key or {}),
        prompt="Test question",
    )


def r(answer=None, response_json=None):
    if response_json is not None:
        return SimpleNamespace(response_json=json.dumps(response_json))
    return SimpleNamespace(response_json=json.dumps({"answer": answer or ""}))


# ── _normalize ────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_lowercases(self):
        assert _normalize("Hello") == "hello"

    def test_comma_decimal_to_dot(self):
        assert _normalize("8,5") == "8.5"

    def test_rand_currency_stripped(self):
        assert _normalize("R400") == "400"
        assert _normalize("r 50") == "50"

    def test_units_stripped(self):
        assert "km" not in _normalize("10 km")
        assert "hours" not in _normalize("3 hours")
        assert "kg" not in _normalize("5kg")

    def test_percent_not_stripped_bare(self):
        # \b after % requires a word-boundary that doesn't exist at end-of-string
        # after a non-word char — so bare "100%" keeps the %. _number_in_text
        # still finds "100" correctly inside such strings.
        assert "100" in _normalize("100%")

    def test_no_mutation_on_plain_number(self):
        assert _normalize("42") == "42"


# ── _number_in_text ───────────────────────────────────────────────────────────

class TestNumberInText:
    def test_exact_match(self):
        assert _number_in_text("30", "the answer is 30") is True

    def test_no_false_substring_match(self):
        assert _number_in_text("30", "300 items") is False

    def test_currency_normalized(self):
        assert _number_in_text("400", "R400") is True

    def test_comma_decimal(self):
        assert _number_in_text("8.5", "8,5 litres") is True

    def test_not_present(self):
        assert _number_in_text("21", "the answer is 22") is False

    def test_number_at_start(self):
        assert _number_in_text("5", "5 workers") is True

    def test_number_at_end(self):
        assert _number_in_text("5", "workers: 5") is True


# ── _answer_correct ───────────────────────────────────────────────────────────

class TestAnswerCorrect:
    def test_correct_when_answer_in_list(self):
        assert _answer_correct("72", ["72"]) is True

    def test_incorrect_when_not_in_list(self):
        assert _answer_correct("71", ["72"]) is False

    def test_multiple_accepted_answers(self):
        assert _answer_correct("12", ["11", "12", "13"]) is True

    def test_empty_answers_list(self):
        assert _answer_correct("anything", []) is False


# ── _working_present ──────────────────────────────────────────────────────────

class TestWorkingPresent:
    def test_all_keywords_present(self):
        assert _working_present("multiply 4 by 18", ["multiply", "18"]) is True

    def test_missing_one_keyword(self):
        assert _working_present("multiply only", ["multiply", "18"]) is False

    def test_empty_keywords(self):
        assert _working_present("anything", []) is True

    def test_case_insensitive(self):
        assert _working_present("MULTIPLY by 18", ["multiply", "18"]) is True


# ── _auto_mark_match ──────────────────────────────────────────────────────────

class TestAutoMarkMatch:
    def _make(self, response_data, expected, marks_per=1, max_marks=None, flag_always=False):
        question = q(kind="match", max_marks=max_marks or len(expected))
        response = SimpleNamespace(response_json=json.dumps(response_data))
        key = {"match": expected, "marks_per_match": marks_per, "flag_always": flag_always}
        return _auto_mark_match(question, response, key)

    def test_all_correct(self):
        result = self._make({"t1": "Salary", "t2": "Leave"}, {"t1": "Salary", "t2": "Leave"})
        assert result["points"] == 2.0
        assert result["rubric_json"]["needs_review"] is False

    def test_partial_correct(self):
        result = self._make({"t1": "Salary", "t2": "Wrong"}, {"t1": "Salary", "t2": "Leave"})
        assert result["points"] == 1.0

    def test_none_correct(self):
        result = self._make({"t1": "X", "t2": "Y"}, {"t1": "Salary", "t2": "Leave"})
        assert result["points"] == 0.0

    def test_case_insensitive(self):
        result = self._make({"t1": "salary"}, {"t1": "Salary"})
        assert result["points"] == 1.0

    def test_flag_always_sets_needs_review(self):
        result = self._make({"t1": "Salary"}, {"t1": "Salary"}, flag_always=True)
        assert result["rubric_json"]["needs_review"] is True

    def test_points_capped_at_max_marks(self):
        result = self._make(
            {"t1": "A", "t2": "B", "t3": "C"},
            {"t1": "A", "t2": "B", "t3": "C"},
            marks_per=2, max_marks=4
        )
        assert result["points"] == 4.0


# ── _auto_mark_sentence_word ──────────────────────────────────────────────────

class TestAutoMarkSentenceWord:
    def _mark(self, answer, root="learn", min_words=4, max_marks=1):
        question = q(max_marks=max_marks)
        response = r(answer)
        key = {"sentence_word": root, "min_words": min_words}
        return _auto_mark_sentence_word(question, response, key)

    def test_word_used_in_adequate_sentence(self):
        result = self._mark("I learn something new every day")
        assert result["points"] == 1.0

    def test_inflected_form_accepted(self):
        result = self._mark("I am learning something new today at work")
        assert result["points"] == 1.0

    def test_word_not_used(self):
        result = self._mark("I do something every day at work")
        assert result["points"] == 0.0

    def test_sentence_too_short(self):
        result = self._mark("I learn now")  # 3 words < min_words=4
        assert result["points"] == 0.0

    def test_always_flags_for_review(self):
        result = self._mark("I learn something new every day")
        assert result["rubric_json"]["needs_review"] is True


# ── _auto_mark_keyword_answer ─────────────────────────────────────────────────

class TestAutoMarkKeywordAnswer:
    def _mark(self, answer, keywords, max_marks=2, partial_marks=0, flag_always=False):
        question = q(max_marks=max_marks)
        response = r(answer)
        key = {"keyword_answer": keywords, "partial_marks": partial_marks, "flag_always": flag_always}
        return _auto_mark_keyword_answer(question, response, key)

    def test_all_keywords_found(self):
        result = self._mark("Office Administration learnership", ["Office Administration", "learnership"])
        assert result["points"] == 2.0
        assert result["rubric_json"]["needs_review"] is False

    def test_partial_keywords_found(self):
        result = self._mark("learnership only", ["Office Administration", "learnership"], partial_marks=1)
        assert result["points"] == 1.0
        assert result["rubric_json"]["needs_review"] is True

    def test_no_keywords_found(self):
        result = self._mark("nothing relevant here", ["Office Administration", "learnership"])
        assert result["points"] == 0.0
        assert result["rubric_json"]["needs_review"] is False

    def test_flag_always_sets_review_even_when_all_found(self):
        result = self._mark("Office Administration learnership", ["Office Administration", "learnership"], flag_always=True)
        assert result["rubric_json"]["needs_review"] is True

    def test_case_insensitive_matching(self):
        result = self._mark("office administration LEARNERSHIP", ["Office Administration", "learnership"])
        assert result["points"] == 2.0


# ── _auto_mark_keyword_per_mark ───────────────────────────────────────────────

class TestAutoMarkKeywordPerMark:
    def _mark(self, answer, keywords, max_marks=3, marks_per=1, flag_always=False):
        question = q(max_marks=max_marks)
        response = r(answer)
        key = {"keyword_per_mark": keywords, "marks_per_keyword": marks_per, "flag_always": flag_always}
        return _auto_mark_keyword_per_mark(question, response, key)

    def test_each_keyword_scores(self):
        result = self._mark("respect honesty teamwork", ["respect", "honesty", "teamwork"])
        assert result["points"] == 3.0

    def test_partial_keywords(self):
        result = self._mark("respect only", ["respect", "honesty", "teamwork"])
        assert result["points"] == 1.0

    def test_none_found(self):
        result = self._mark("nothing here", ["respect", "honesty"])
        assert result["points"] == 0.0

    def test_points_capped_at_max_marks(self):
        result = self._mark("a b c d", ["a", "b", "c", "d"], max_marks=2)
        assert result["points"] == 2.0

    def test_flag_always(self):
        result = self._mark("respect", ["respect"], flag_always=True)
        assert result["rubric_json"]["needs_review"] is True


# ── _auto_mark_tiered_keyword ─────────────────────────────────────────────────

class TestAutoMarkTieredKeyword:
    def _mark(self, answer, tiers, max_marks=3, flag_always=False):
        question = q(max_marks=max_marks)
        response = r(answer)
        key = {"tiered_keyword": tiers, "flag_always": flag_always}
        return _auto_mark_tiered_keyword(question, response, key)

    def test_first_tier_matches(self):
        tiers = [
            {"marks": 3, "require_all": ["communicate", "professional"]},
            {"marks": 1, "require_any": ["communicate"]},
        ]
        result = self._mark("I communicate in a professional manner", tiers)
        assert result["points"] == 3.0

    def test_second_tier_matches_when_first_fails(self):
        tiers = [
            {"marks": 3, "require_all": ["communicate", "professional"]},
            {"marks": 1, "require_any": ["communicate"]},
        ]
        result = self._mark("I communicate clearly", tiers)
        assert result["points"] == 1.0

    def test_no_tier_matches_returns_zero(self):
        tiers = [{"marks": 3, "require_all": ["professional"]}]
        result = self._mark("nothing relevant", tiers)
        assert result["points"] == 0.0

    def test_require_not_skips_tier(self):
        tiers = [{"marks": 3, "require_any": ["good"], "require_not": ["bad"]}]
        result = self._mark("good but also bad", tiers)
        assert result["points"] == 0.0

    def test_min_words_skips_short_response(self):
        tiers = [{"marks": 3, "require_any": ["good"], "min_words": 5}]
        result = self._mark("good", tiers)
        assert result["points"] == 0.0

    def test_min_words_passes_adequate_response(self):
        tiers = [{"marks": 3, "require_any": ["good"], "min_words": 3}]
        result = self._mark("this is good enough response", tiers)
        assert result["points"] == 3.0

    def test_require_all_partial_fails(self):
        tiers = [{"marks": 3, "require_all": ["alpha", "beta"]}]
        result = self._mark("only alpha here", tiers)
        assert result["points"] == 0.0

    def test_flag_always_sets_review(self):
        tiers = [{"marks": 3, "require_any": ["good"]}]
        result = self._mark("good answer here", tiers, flag_always=True)
        assert result["rubric_json"]["needs_review"] is True


# ── auto_mark_response ────────────────────────────────────────────────────────

class TestAutoMarkResponse:
    def test_returns_none_when_auto_mark_false(self):
        question = q(answer_key={"auto_mark": False})
        response = r("some answer")
        assert auto_mark_response(question, response) is None

    def test_returns_none_when_no_auto_mark_key(self):
        question = q(answer_key={})
        response = r("some answer")
        assert auto_mark_response(question, response) is None

    def test_dispatches_to_match(self):
        question = q(kind="match", max_marks=2, answer_key={
            "auto_mark": True, "match": {"t1": "Salary"}, "marks_per_match": 2
        })
        response = SimpleNamespace(response_json=json.dumps({"t1": "Salary"}))
        result = auto_mark_response(question, response)
        assert result["points"] == 2.0

    def test_dispatches_to_sentence_word(self):
        question = q(max_marks=1, answer_key={"auto_mark": True, "sentence_word": "learn"})
        response = r("I learn something new every day at work")
        result = auto_mark_response(question, response)
        assert result is not None
        assert result["rubric_json"]["needs_review"] is True

    def test_dispatches_to_keyword_answer(self):
        question = q(max_marks=1, answer_key={"auto_mark": True, "keyword_answer": ["teamwork"]})
        response = r("teamwork is essential")
        result = auto_mark_response(question, response)
        assert result["points"] == 1.0

    def test_dispatches_to_keyword_per_mark(self):
        question = q(max_marks=2, answer_key={"auto_mark": True, "keyword_per_mark": ["a", "b"]})
        response = r("a and b are here")
        result = auto_mark_response(question, response)
        assert result["points"] == 2.0

    def test_dispatches_to_tiered_keyword(self):
        question = q(max_marks=3, answer_key={
            "auto_mark": True,
            "tiered_keyword": [{"marks": 3, "require_any": ["good"]}]
        })
        response = r("this is a good answer")
        result = auto_mark_response(question, response)
        assert result["points"] == 3.0

    def test_standard_correct_answer(self):
        question = q(max_marks=2, answer_key={"auto_mark": True, "answers": ["72"]})
        response = r("72")
        result = auto_mark_response(question, response)
        assert result["points"] == 2.0
        assert result["rubric_json"]["answer_found"] is True

    def test_standard_incorrect_answer(self):
        question = q(max_marks=2, answer_key={"auto_mark": True, "answers": ["72"]})
        response = r("99")
        result = auto_mark_response(question, response)
        assert result["points"] == 0.0
        assert result["rubric_json"]["answer_found"] is False

    def test_correct_answer_missing_working_with_partial_marks(self):
        question = q(max_marks=2, answer_key={
            "auto_mark": True, "answers": ["72"],
            "working_keywords": ["multiply"], "partial_marks": 1
        })
        response = r("72")  # correct but no working keyword
        result = auto_mark_response(question, response)
        assert result["points"] == 1.0
        assert result["rubric_json"]["needs_review"] is True

    def test_correct_answer_with_working(self):
        question = q(max_marks=2, answer_key={
            "auto_mark": True, "answers": ["72"], "working_keywords": ["multiply"]
        })
        response = r("multiply 8 by 9 = 72")
        result = auto_mark_response(question, response)
        assert result["points"] == 2.0

    def test_flag_always_sets_needs_review_on_incorrect(self):
        question = q(max_marks=1, answer_key={
            "auto_mark": True, "answers": ["5"], "flag_always": True
        })
        response = r("wrong")
        result = auto_mark_response(question, response)
        assert result["rubric_json"]["needs_review"] is True


# ── _is_blank_response ────────────────────────────────────────────────────────

class TestIsBlankResponse:
    def test_empty_json(self):
        assert _is_blank_response(SimpleNamespace(response_json="{}")) is True

    def test_null_json(self):
        assert _is_blank_response(SimpleNamespace(response_json=None)) is True

    def test_blank_answer(self):
        assert _is_blank_response(r("")) is True

    def test_whitespace_only(self):
        assert _is_blank_response(r("   ")) is True

    def test_non_blank(self):
        assert _is_blank_response(r("something")) is False

    def test_match_response_non_blank(self):
        resp = SimpleNamespace(response_json=json.dumps({"t1": "Salary", "t2": "Leave"}))
        assert _is_blank_response(resp) is False

    def test_match_response_blank_values(self):
        resp = SimpleNamespace(response_json=json.dumps({"t1": "", "t2": ""}))
        assert _is_blank_response(resp) is True


# ── _zero_score ───────────────────────────────────────────────────────────────

class TestZeroScore:
    def test_zero_points(self):
        result = _zero_score(q(max_marks=3))
        assert result["points"] == 0.0
        assert result["max_points"] == 3.0

    def test_auto_marked_flag(self):
        result = _zero_score(q(max_marks=1))
        assert result["rubric_json"]["auto_marked"] is True

    def test_no_review_needed(self):
        result = _zero_score(q(max_marks=1))
        assert result["rubric_json"]["needs_review"] is False


# ── auto_mark_attempt (DB) ───────────────────────────────────────────────────

@pytest.mark.django_db
class TestAutoMarkAttempt:
    @pytest.fixture
    def setup(self):
        from django.contrib.auth import get_user_model
        from assessment.models import (
            AssessmentTemplate, Attempt, Learner, Question, Response, Section
        )
        template = AssessmentTemplate.objects.create(name="T", version="v1")
        section = Section.objects.create(template=template, title="S1", order=1)
        learner = Learner.objects.create(
            first_names="Test", surname="Learner", id_number="0001010000081"
        )
        attempt = Attempt.objects.create(
            template=template, learner=learner, status=Attempt.SUBMITTED
        )
        return SimpleNamespace(
            template=template, section=section, learner=learner, attempt=attempt,
            Question=Question, Response=Response,
        )

    def test_marks_auto_markable_responses(self, setup):
        from assessment.models import Score
        question = setup.Question.objects.create(
            section=setup.section, order=1, code="Q1",
            kind="text", max_marks=2,
            answer_key_json=json.dumps({"auto_mark": True, "answers": ["42"]}),
        )
        setup.Response.objects.create(
            attempt=setup.attempt, question=question,
            response_json=json.dumps({"answer": "42"}),
        )
        count = auto_mark_attempt(setup.attempt)
        assert count == 1
        score = Score.objects.get(response__attempt=setup.attempt)
        assert score.points == 2

    def test_blank_response_gets_zero(self, setup):
        from assessment.models import Score
        question = setup.Question.objects.create(
            section=setup.section, order=1, code="Q2",
            kind="text", max_marks=2,
            answer_key_json=json.dumps({"auto_mark": True, "answers": ["42"]}),
        )
        setup.Response.objects.create(
            attempt=setup.attempt, question=question,
            response_json=json.dumps({"answer": ""}),
        )
        auto_mark_attempt(setup.attempt)
        score = Score.objects.get(response__attempt=setup.attempt)
        assert score.points == 0

    def test_blank_manual_question_left_unscored(self, setup):
        from assessment.models import Score
        question = setup.Question.objects.create(
            section=setup.section, order=1, code="Q3",
            kind="text", max_marks=8,
            answer_key_json=json.dumps({"auto_mark": False}),
        )
        setup.Response.objects.create(
            attempt=setup.attempt, question=question,
            response_json=json.dumps({"answer": ""}),
        )
        count = auto_mark_attempt(setup.attempt)
        assert count == 0
        assert not Score.objects.filter(response__attempt=setup.attempt).exists()

    def test_existing_score_not_overwritten(self, setup):
        from assessment.models import Score
        question = setup.Question.objects.create(
            section=setup.section, order=1, code="Q4",
            kind="text", max_marks=2,
            answer_key_json=json.dumps({"auto_mark": True, "answers": ["42"]}),
        )
        response = setup.Response.objects.create(
            attempt=setup.attempt, question=question,
            response_json=json.dumps({"answer": "42"}),
        )
        Score.objects.create(
            response=response, points=1, max_points=2,
            rubric_json={"mode": "manual", "auto_marked": False, "needs_review": False},
        )
        auto_mark_attempt(setup.attempt)
        score = Score.objects.get(response=response)
        assert score.points == 1  # unchanged

    def test_returns_count_of_marked_questions(self, setup):
        for i in range(3):
            question = setup.Question.objects.create(
                section=setup.section, order=i + 1, code=f"QC{i}",
                kind="text", max_marks=1,
                answer_key_json=json.dumps({"auto_mark": True, "answers": ["1"]}),
            )
            setup.Response.objects.create(
                attempt=setup.attempt, question=question,
                response_json=json.dumps({"answer": "1"}),
            )
        count = auto_mark_attempt(setup.attempt)
        assert count == 3


class TestCreateMessageWithRetry:
    """_create_message_with_retry: transient-error retry with backoff."""

    def test_succeeds_first_try_no_retry(self):
        client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: "ok"))
        with patch("assessment.auto_mark.time.sleep") as sleep:
            result = _create_message_with_retry(client, model="x")
        assert result == "ok"
        sleep.assert_not_called()

    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def create(**kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _fake_transient_error()
            return "ok"

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        with patch("assessment.auto_mark.time.sleep") as sleep:
            result = _create_message_with_retry(client, model="x")
        assert result == "ok"
        assert calls["n"] == 3
        assert sleep.call_count == 2  # slept between attempts 1→2 and 2→3

    def test_exhausts_retries_and_raises_last_error(self):
        client = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(_fake_transient_error()))
        )
        with patch("assessment.auto_mark.time.sleep"):
            with pytest.raises(anthropic.InternalServerError):
                _create_message_with_retry(client, model="x")

    def test_non_retryable_error_raised_immediately_no_retry(self):
        calls = {"n": 0}

        def create(**kw):
            calls["n"] += 1
            raise _fake_auth_error()

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        with patch("assessment.auto_mark.time.sleep") as sleep:
            with pytest.raises(anthropic.AuthenticationError):
                _create_message_with_retry(client, model="x")
        assert calls["n"] == 1  # no retry attempted
        sleep.assert_not_called()


class TestAiRubricFailureTelemetry:
    """_auto_mark_ai_rubric's except path: metric + log on final failure."""

    def test_failure_increments_metric_and_falls_back_to_manual_review(self):
        question = SimpleNamespace(pk=1, prompt="Explain X", max_marks=4)
        response = SimpleNamespace(response_json=json.dumps({"answer": "some text"}))
        key = {"criteria": [{"key": "c1", "label": "Clarity", "max_points": 4}]}

        before = ai_marking_failures_total.labels(error_type="AuthenticationError")._value.get()

        with patch("assessment.auto_mark.anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.side_effect = _fake_auth_error()
            from assessment.auto_mark import _auto_mark_ai_rubric
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                result = _auto_mark_ai_rubric(question, response, key)

        after = ai_marking_failures_total.labels(error_type="AuthenticationError")._value.get()
        assert after == before + 1
        assert result["points"] == 0.0
        assert result["rubric_json"]["needs_review"] is True
        assert "AI marking unavailable" in result["rubric_json"]["notes"]
