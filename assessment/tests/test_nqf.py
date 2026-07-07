"""
Unit tests for assessment/nqf.py.

All pure-function tests run without database access.
Integration tests (build_question_metadata, compute_nqf_placement) use @pytest.mark.django_db.
"""
import pytest

from assessment.nqf import (
    NQF_PCT_THRESHOLDS,
    NQF_POST_L4_NUM_PCT,
    NQF_QUESTION_PCT_THRESHOLDS,
    NQFPlacement,
    build_question_metadata,
    compute_group_data,
    compute_levels_from_prefix_scores,
    compute_nqf_placement,
    level_for_percentage,
    modal_level,
    placement_comment,
    section_kind,
)


# ---------------------------------------------------------------------------
# level_for_percentage
# ---------------------------------------------------------------------------

class TestLevelForPercentage:
    def test_returns_l1_at_zero(self):
        assert level_for_percentage(0, NQF_PCT_THRESHOLDS) == "L1"

    def test_returns_l1_at_39(self):
        assert level_for_percentage(39, NQF_PCT_THRESHOLDS) == "L1"

    def test_returns_l2_at_40(self):
        assert level_for_percentage(40, NQF_PCT_THRESHOLDS) == "L2"

    def test_returns_l4_at_100(self):
        assert level_for_percentage(100, NQF_PCT_THRESHOLDS) == "L4"

    def test_falls_back_to_last_band_when_above_range(self):
        # pct > 100 should not crash; last band is returned
        assert level_for_percentage(110, NQF_PCT_THRESHOLDS) == "L4"

    def test_lit_a_boundary_40_is_l1(self):
        thresholds = NQF_QUESTION_PCT_THRESHOLDS["LIT-A"]
        assert level_for_percentage(40, thresholds) == "L1"

    def test_lit_a_boundary_41_is_l2(self):
        thresholds = NQF_QUESTION_PCT_THRESHOLDS["LIT-A"]
        assert level_for_percentage(41, thresholds) == "L2"

    def test_lit_a_boundary_81_is_l4(self):
        thresholds = NQF_QUESTION_PCT_THRESHOLDS["LIT-A"]
        assert level_for_percentage(81, thresholds) == "L4"

    def test_num_a_has_only_two_levels(self):
        thresholds = NQF_QUESTION_PCT_THRESHOLDS["NUM-A"]
        assert level_for_percentage(60, thresholds) == "L1"
        assert level_for_percentage(61, thresholds) == "L2"

    def test_num_d_boundary_47_is_l4(self):
        thresholds = NQF_QUESTION_PCT_THRESHOLDS["NUM-D"]
        assert level_for_percentage(47, thresholds) == "L4"

    def test_num_d_boundary_46_is_l3(self):
        thresholds = NQF_QUESTION_PCT_THRESHOLDS["NUM-D"]
        assert level_for_percentage(46, thresholds) == "L3"


# ---------------------------------------------------------------------------
# modal_level
# ---------------------------------------------------------------------------

class TestModalLevel:
    def test_empty_list_returns_l1(self):
        assert modal_level([]) == "L1"

    def test_single_level_returned_unchanged(self):
        assert modal_level(["L3"]) == "L3"

    def test_clear_majority_wins(self):
        assert modal_level(["L2", "L2", "L3"]) == "L2"

    def test_tie_returns_lower_level(self):
        assert modal_level(["L3", "L2"]) == "L2"

    def test_three_way_tie_returns_lowest(self):
        assert modal_level(["L4", "L2", "L3"]) == "L2"

    def test_post_l4_beats_l4_when_majority(self):
        assert modal_level(["Post L4", "Post L4", "L4"]) == "Post L4"

    def test_tie_between_l4_and_post_l4_returns_l4(self):
        assert modal_level(["L4", "Post L4"]) == "L4"

    def test_all_same_level(self):
        assert modal_level(["L2", "L2", "L2"]) == "L2"


# ---------------------------------------------------------------------------
# placement_comment
# ---------------------------------------------------------------------------

class TestPlacementComment:
    def test_standard_levels_formatted_correctly(self):
        comment = placement_comment("L2", "L3")
        assert "NQF Level 2 for Literacy" in comment
        assert "NQF Level 3 for Numeracy" in comment

    def test_post_l4_numeracy_message(self):
        comment = placement_comment("L4", "Post L4")
        assert "NQF Level 4 for Literacy" in comment
        assert "Post-AET Level for Numeracy" in comment
        assert "no further AET training required" in comment

    def test_both_post_l4(self):
        comment = placement_comment("Post L4", "Post L4")
        assert comment.count("no further AET training required") == 2

    def test_l1_output(self):
        comment = placement_comment("L1", "L1")
        assert "NQF Level 1 for Literacy" in comment
        assert "NQF Level 1 for Numeracy" in comment


# ---------------------------------------------------------------------------
# section_kind
# ---------------------------------------------------------------------------

class TestSectionKind:
    def test_literacy_section(self):
        assert section_kind("Literacy Section") == "literacy"

    def test_literacy_case_insensitive(self):
        assert section_kind("literacy reading") == "literacy"

    def test_numeracy_section(self):
        assert section_kind("Numeracy Section") == "numeracy"

    def test_mathematics_is_numeracy(self):
        assert section_kind("Mathematics Skills") == "numeracy"

    def test_unknown_section_is_other(self):
        assert section_kind("General Knowledge") == "other"

    def test_section_with_both_literacy_and_maths_is_numeracy(self):
        # "MATH" takes precedence over "LITERACY" per the implementation
        assert section_kind("Literacy and Mathematics") == "numeracy"


# ---------------------------------------------------------------------------
# compute_levels_from_prefix_scores
# ---------------------------------------------------------------------------

class TestComputeLevelsFromPrefixScores:
    def test_all_zeros_returns_l1_l1(self):
        scores = {
            "LIT-A": [0.0, 10.0],
            "NUM-A": [0.0, 15.0],
        }
        lit, num = compute_levels_from_prefix_scores(scores)
        assert lit == "L1"
        assert num == "L1"

    def test_perfect_literacy_score_returns_l4(self):
        # 100% on all LIT prefixes → minimum is L4
        scores = {
            "LIT-A": [10.0, 10.0],
            "LIT-B": [14.0, 14.0],
            "LIT-C": [20.0, 20.0],
        }
        lit, num = compute_levels_from_prefix_scores(scores)
        assert lit == "L4"

    def test_low_writing_averaged_into_total(self):
        # Perfect on all parts, poor writing (25%) — total 102/108 = 94% → L4
        # Previously min_level would have returned L1; now total percentage is used
        scores = {
            "GEN-A": [11.0, 11.0],
            "GEN-B": [10.0, 10.0],
            "GEN-C": [17.0, 17.0],
            "GEN-D": [12.0, 12.0],
            "GEN-E": [30.0, 30.0],
            "GEN-F": [20.0, 20.0],
            "GEN-G": [2.0,   8.0],   # 25% — total: 102/108 = 94% → L4
        }
        prefix_domain = {p: "literacy" for p in scores}
        lit, _ = compute_levels_from_prefix_scores(scores, prefix_domain)
        assert lit == "L4"

    def test_post_l4_triggered_when_all_num_prefixes_at_87_pct(self):
        # Each NUM prefix at exactly NQF_POST_L4_NUM_PCT (87%)
        scores = {
            "NUM-A": [13.05, 15.0],  # 87%
            "NUM-B": [13.05, 15.0],
            "NUM-C": [13.05, 15.0],
            "NUM-D": [13.05, 15.0],
        }
        _, num = compute_levels_from_prefix_scores(scores)
        assert num == "Post L4"

    def test_post_l4_not_triggered_when_one_prefix_below_threshold(self):
        scores = {
            "NUM-A": [13.05, 15.0],  # 87%
            "NUM-B": [13.05, 15.0],  # 87%
            "NUM-C": [13.05, 15.0],  # 87%
            "NUM-D": [8.0,   15.0],  # 53% — below threshold
        }
        _, num = compute_levels_from_prefix_scores(scores)
        assert num != "Post L4"

    def test_post_l4_not_triggered_when_num_prefix_missing(self):
        # Only three NUM prefixes present — gate requires all four
        scores = {
            "NUM-A": [15.0, 15.0],
            "NUM-B": [15.0, 15.0],
            "NUM-C": [15.0, 15.0],
            # NUM-D absent
        }
        _, num = compute_levels_from_prefix_scores(scores)
        assert num != "Post L4"

    def test_empty_prefix_scores_returns_l1_na(self):
        lit, num = compute_levels_from_prefix_scores({})
        assert lit == "L1"
        assert num == "N/A"  # no numeracy scored → N/A, not L1

    def test_only_lit_prefixes_num_defaults_to_na(self):
        scores = {"LIT-A": [8.0, 10.0]}
        lit, num = compute_levels_from_prefix_scores(scores)
        assert lit != "L1"  # 80% → L3 for LIT-A
        assert num == "N/A"  # no numeracy scored → N/A

    def test_unknown_prefix_uses_fallback_thresholds(self):
        # LIT-D uses NQF_PCT_THRESHOLDS fallback (not in the question table)
        scores = {"LIT-D": [6.0, 10.0]}  # 60% → L3 on fallback table
        lit, _ = compute_levels_from_prefix_scores(scores)
        assert lit == "L3"


# ---------------------------------------------------------------------------
# compute_group_data
# ---------------------------------------------------------------------------

class TestComputeGroupData:
    def _lit_groups(self):
        from assessment.nqf import NQF_DISPLAY_GROUPS
        return NQF_DISPLAY_GROUPS["literacy"]

    def test_empty_prefix_scores_produces_zero_groups(self):
        groups = compute_group_data({}, self._lit_groups())
        for g in groups:
            assert g["awarded"] == 0
            assert g["pct"] == 0

    def test_group_labels_preserved(self):
        groups = compute_group_data({}, self._lit_groups())
        labels = [g["label"] for g in groups]
        assert "Reading & Comprehension" in labels
        assert "Writing" in labels

    def test_awarded_sums_across_prefixes(self):
        scores = {
            "LIT-A": [8.0, 10.0],
            "LIT-B": [10.0, 14.0],
            "LIT-C": [12.0, 20.0],
        }
        groups = compute_group_data(scores, self._lit_groups())
        reading_group = next(g for g in groups if g["label"] == "Reading & Comprehension")
        assert reading_group["awarded"] == 30.0
        assert reading_group["max"] == 44.0

    def test_pct_rounds_to_integer(self):
        scores = {"LIT-A": [1.0, 3.0]}  # 33.33...%
        groups = compute_group_data(scores, self._lit_groups())
        reading_group = next(g for g in groups if g["label"] == "Reading & Comprehension")
        assert isinstance(reading_group["pct"], int)
        assert reading_group["pct"] == 33


# ---------------------------------------------------------------------------
# Integration: build_question_metadata + compute_nqf_placement
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestComputeNQFPlacement:
    @pytest.fixture
    def setup_attempt(self, tenant):
        from assessment.models import (
            AssessmentTemplate,
            Attempt,
            Learner,
            Question,
            Response,
            Score,
            Section,
        )

        template = AssessmentTemplate.objects.create(tenant=tenant, name="Test Template")

        lit_section = Section.objects.create(
            template=template, title="Literacy Section", order=1
        )
        num_section = Section.objects.create(
            template=template, title="Numeracy Section", order=2
        )

        q_lit = Question.objects.create(
            section=lit_section, order=1, code="LIT-A-1",
            prompt="Read and answer.", kind="text", max_marks=10,
        )
        q_num = Question.objects.create(
            section=num_section, order=1, code="NUM-A-1",
            prompt="Calculate.", kind="text", max_marks=15,
        )

        learner = Learner.objects.create(
            tenant=tenant, first_names="Test", surname="Learner", id_number="0001010000000"
        )
        attempt = Attempt.objects.create(template=template, learner=learner)

        r_lit = Response.objects.create(attempt=attempt, question=q_lit, response_json='{"answer":"x"}')
        r_num = Response.objects.create(attempt=attempt, question=q_num, response_json='{"answer":"y"}')

        Score.objects.create(response=r_lit, points=8.0, max_points=10.0)
        Score.objects.create(response=r_num, points=10.0, max_points=15.0)

        all_questions = Question.objects.filter(
            section__template=template
        ).select_related("section")
        q_meta = build_question_metadata(all_questions)

        return attempt, q_meta

    def test_returns_nqf_placement_instance(self, setup_attempt):
        attempt, q_meta = setup_attempt
        result = compute_nqf_placement(attempt, q_meta)
        assert isinstance(result, NQFPlacement)

    def test_learner_attached_to_placement(self, setup_attempt):
        attempt, q_meta = setup_attempt
        result = compute_nqf_placement(attempt, q_meta)
        assert result.learner == attempt.learner

    def test_lit_total_reflects_scores(self, setup_attempt):
        attempt, q_meta = setup_attempt
        result = compute_nqf_placement(attempt, q_meta)
        assert result.lit_total["awarded"] == 8.0
        assert result.lit_total["max"] == 10.0
        assert result.lit_total["pct"] == 80

    def test_num_total_reflects_scores(self, setup_attempt):
        attempt, q_meta = setup_attempt
        result = compute_nqf_placement(attempt, q_meta)
        assert result.num_total["awarded"] == 10.0
        assert result.num_total["max"] == 15.0
        assert result.num_total["pct"] == 67

    def test_comment_is_non_empty_string(self, setup_attempt):
        attempt, q_meta = setup_attempt
        result = compute_nqf_placement(attempt, q_meta)
        assert isinstance(result.comment, str)
        assert len(result.comment) > 0

    def test_unscored_responses_excluded_from_placement(self, tenant):
        from assessment.models import (
            AssessmentTemplate, Attempt, Learner,
            Question, Response, Section,
        )
        template = AssessmentTemplate.objects.create(tenant=tenant, name="No Score Template")
        section = Section.objects.create(
            template=template, title="Numeracy Section", order=1
        )
        q = Question.objects.create(
            section=section, order=1, code="NUM-B-1",
            prompt="Q", kind="text", max_marks=15,
        )
        learner = Learner.objects.create(
            tenant=tenant, first_names="A", surname="B", id_number="0001010000001"
        )
        attempt = Attempt.objects.create(template=template, learner=learner)
        Response.objects.create(attempt=attempt, question=q, response_json='{"answer":""}')
        # No Score created — response must be excluded from placement

        q_meta = build_question_metadata(
            Question.objects.filter(section__template=template).select_related("section")
        )
        result = compute_nqf_placement(attempt, q_meta)
        assert result.num_total["awarded"] == 0.0
        assert result.num_level == "N/A"  # no scored numeracy → N/A
