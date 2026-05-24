"""
Tests for assessment/renderers.py — layout dispatch and context generation.
No DB needed; uses SimpleNamespace mocks.
"""

import json
import pytest
from types import SimpleNamespace

from assessment.renderers import (
    get_renderer,
    BaseRenderer,
    TextRenderer,
    MatchRenderer,
    FormFillRenderer,
    RENDERERS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_q(kind="text", pk=1):
    return SimpleNamespace(kind=kind, pk=pk)


def make_r(response_json=None):
    return SimpleNamespace(response_json=json.dumps(response_json) if response_json else None)


def make_attempt(code="TESTCODE"):
    return SimpleNamespace(code=code)


# ── get_renderer dispatch ─────────────────────────────────────────────────────

class TestGetRenderer:
    def test_dispatches_by_layout(self):
        q = make_q(kind="text")
        renderer = get_renderer(q, {"layout": "match"}, make_r())
        assert isinstance(renderer, MatchRenderer)

    def test_dispatches_by_kind(self):
        q = make_q(kind="text")
        renderer = get_renderer(q, {}, make_r())
        assert isinstance(renderer, TextRenderer)

    def test_match_kind_dispatches_correctly(self):
        q = make_q(kind="match")
        renderer = get_renderer(q, {}, make_r())
        assert isinstance(renderer, MatchRenderer)

    def test_unknown_layout_and_kind_returns_base(self):
        q = make_q(kind="unknown")
        renderer = get_renderer(q, {"layout": "unknown_layout"}, make_r())
        assert type(renderer) is BaseRenderer

    def test_layout_takes_precedence_over_kind(self):
        q = make_q(kind="match")
        renderer = get_renderer(q, {"layout": "text"}, make_r())
        assert isinstance(renderer, TextRenderer)


# ── TextRenderer ──────────────────────────────────────────────────────────────

class TestTextRenderer:
    def _make(self, answer=""):
        q = make_q()
        r = make_r({"answer": answer})
        return TextRenderer(q, {}, r)

    def test_get_context_returns_current_answer(self):
        renderer = self._make("hello")
        # _current_answer is set in get_form; context falls back to ""
        ctx = renderer.get_context()
        assert "current_answer" in ctx

    def test_get_context_empty_when_no_form_called(self):
        renderer = self._make("hello")
        assert renderer.get_context()["current_answer"] == ""


# ── MatchRenderer ─────────────────────────────────────────────────────────────

class TestMatchRenderer:
    def _make(self, spec, matched=None, attempt_code="ATT1"):
        q = make_q(kind="match", pk=1)
        r = make_r(matched or {})
        attempt = make_attempt(attempt_code)
        return MatchRenderer(q, spec, r, attempt=attempt)

    def test_returns_match_pairs(self):
        spec = {
            "bank": ["protest", "required"],
            "targets": [{"id": "t1", "text": "An objection"}, {"id": "t2", "text": "Needed by law"}],
        }
        renderer = self._make(spec, {"t1": "protest", "t2": "required"})
        ctx = renderer.get_context()
        assert "match_pairs" in ctx
        assert len(ctx["match_pairs"]) == 2

    def test_matched_words_in_pairs(self):
        spec = {"targets": [{"id": "t1", "text": "An objection"}]}
        renderer = self._make(spec, {"t1": "protest"})
        ctx = renderer.get_context()
        assert ctx["match_pairs"][0]["word"] == "protest"

    def test_unmatched_word_is_none(self):
        spec = {"targets": [{"id": "t1", "text": "An objection"}]}
        renderer = self._make(spec, {})
        ctx = renderer.get_context()
        assert ctx["match_pairs"][0]["word"] is None

    def test_stable_shuffle_per_attempt(self):
        spec = {
            "bank": ["a", "b", "c", "d"],
            "targets": [{"id": "t1", "text": "X"}],
        }
        renderer1 = self._make(spec, attempt_code="AAA")
        renderer2 = self._make(spec, attempt_code="AAA")
        ctx1 = renderer1.get_context()
        ctx2 = renderer2.get_context()
        assert ctx1["match_pairs"] == ctx2["match_pairs"]

    def test_different_attempts_may_differ(self):
        spec = {
            "bank": ["a", "b", "c", "d", "e", "f"],
            "targets": [{"id": "t1", "text": "X"}, {"id": "t2", "text": "Y"}],
        }
        results = set()
        for code in ["AAA1", "BBB2", "CCC3", "DDD4", "EEE5"]:
            r = make_r({})
            q = make_q(kind="match", pk=1)
            renderer = MatchRenderer(q, spec, r, attempt=make_attempt(code))
            ctx = renderer.get_context()
            results.add(tuple(p["text"] for p in ctx["match_pairs"]))
        # At least two different orderings across five different attempts
        assert len(results) > 1

    def test_invalid_response_json_returns_empty_pairs(self):
        spec = {"targets": [{"id": "t1", "text": "X"}]}
        q = make_q(kind="match")
        r = SimpleNamespace(response_json="not valid json")
        renderer = MatchRenderer(q, spec, r, attempt=make_attempt())
        ctx = renderer.get_context()
        assert ctx["match_pairs"][0]["word"] is None


# ── FormFillRenderer ──────────────────────────────────────────────────────────

class TestFormFillRenderer:
    def _make(self, fields, stored=None):
        spec = {"fields": fields}
        q = make_q()
        r = make_r(stored or {})
        return FormFillRenderer(q, spec, r)

    def test_get_form_populates_field_values(self):
        renderer = self._make(
            [{"name": "first_name"}, {"name": "age"}],
            {"first_name": "Thabo", "age": "25"},
        )
        from types import SimpleNamespace as FakeRequest
        fake_request = FakeRequest(POST=None)
        renderer.get_form(fake_request)
        ctx = renderer.get_context()
        assert ctx["form_fill_values"]["first_name"] == "Thabo"

    def test_empty_response_gives_empty_values(self):
        renderer = self._make([{"name": "cell_number"}], {})
        from types import SimpleNamespace as FakeRequest
        fake_request = FakeRequest(POST=None)
        renderer.get_form(fake_request)
        ctx = renderer.get_context()
        assert ctx["form_fill_values"] == {}

    def test_get_context_before_get_form_returns_empty(self):
        renderer = self._make([{"name": "x"}])
        ctx = renderer.get_context()
        assert ctx["form_fill_values"] == {}
