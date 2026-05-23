"""
NQF (National Qualifications Framework) placement computation.

Derives the learner's AET placement level (L1–L4 or Post-L4) for literacy
and numeracy from scored assessment responses.

Configuration source: NQF placement specification v1.0.5 (literacy) & v1.0.6 (numeracy).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum


class NQFLevel(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    POST_L4 = "Post L4"
    NA = "N/A"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NQF_DISPLAY_GROUPS: dict[str, list[dict]] = {
    "literacy": [
        {"label": "Reading & Comprehension", "prefixes": ["LIT-A", "LIT-B", "LIT-C"]},
        {"label": "Writing",                 "prefixes": ["LIT-D"]},
    ],
    "numeracy": [
        {"label": "Basic Numeracy",          "prefixes": ["NUM-A", "NUM-B"]},
        {"label": "Mathematics Skills",      "prefixes": ["NUM-C", "NUM-D"]},
    ],
    "gen_literacy": [
        {"label": "NQF Level 1–2", "prefixes": ["GEN-A", "GEN-B", "GEN-C", "GEN-D"]},
        {"label": "NQF Level 3–4", "prefixes": ["GEN-E", "GEN-F"]},
    ],
}

# Per-prefix percentage thresholds.
# Derivation: upper bound of level L = floor(upper_mark / memo_max * 100).
NQF_QUESTION_PCT_THRESHOLDS: dict[str, list[tuple[int, int, NQFLevel]]] = {
    # LIT-A  Q1 memo /10  — boundaries at marks 4, 6, 8
    "LIT-A": [(0, 40, NQFLevel.L1), (41, 60, NQFLevel.L2), (61, 80, NQFLevel.L3), (81, 100, NQFLevel.L4)],
    # LIT-B  Q2 memo /14  — boundaries at marks 4, 8, 11
    "LIT-B": [(0, 28, NQFLevel.L1), (29, 57, NQFLevel.L2), (58, 78, NQFLevel.L3), (79, 100, NQFLevel.L4)],
    # LIT-C  Q3 memo /20  — boundaries at marks 7, 12, 15
    "LIT-C": [(0, 35, NQFLevel.L1), (36, 60, NQFLevel.L2), (61, 75, NQFLevel.L3), (76, 100, NQFLevel.L4)],
    # NUM-A  Q1 memo /15  — boundary at mark 9
    "NUM-A": [(0, 60, NQFLevel.L1), (61, 100, NQFLevel.L2)],
    # NUM-B  Q2 memo /15  — boundaries at marks 4, 9
    "NUM-B": [(0, 26, NQFLevel.L1), (27, 60, NQFLevel.L2), (61, 100, NQFLevel.L3)],
    # NUM-C  Q3 memo /15  — boundaries at marks 3, 6, 12
    "NUM-C": [(0, 20, NQFLevel.L1), (21, 40, NQFLevel.L2), (41, 80, NQFLevel.L3), (81, 100, NQFLevel.L4)],
    # NUM-D  Q4 memo /15  — boundaries at marks 2, 4, 7, 12
    "NUM-D": [(0, 13, NQFLevel.L1), (14, 26, NQFLevel.L2), (27, 46, NQFLevel.L3), (47, 100, NQFLevel.L4)],
}

# Fallback thresholds for rubric questions (e.g. LIT-D) with no defined table.
NQF_PCT_THRESHOLDS: list[tuple[int, int, NQFLevel]] = [
    (0,  39, NQFLevel.L1),
    (40, 59, NQFLevel.L2),
    (60, 79, NQFLevel.L3),
    (80, 100, NQFLevel.L4),
]

# Post-L4 numeracy gate: all four NUM prefixes must reach this percentage (≈ 13/15).
NQF_POST_L4_NUM_PCT = 87

_NUM_PREFIXES = frozenset({"NUM-A", "NUM-B", "NUM-C", "NUM-D"})
_LEVEL_ORDER = {
    NQFLevel.L1: 1,
    NQFLevel.L2: 2,
    NQFLevel.L3: 3,
    NQFLevel.L4: 4,
    NQFLevel.POST_L4: 5,
}


# ---------------------------------------------------------------------------
# Pure helpers — fully unit-testable without database access
# ---------------------------------------------------------------------------

def level_for_percentage(pct: float, thresholds: list[tuple[int, int, str]]) -> str:
    """Return the NQF level label for a percentage using (lo, hi, label) bands."""
    for lo, hi, label in thresholds:
        if lo <= pct <= hi:
            return label
    return thresholds[-1][2]


def modal_level(levels: list[str]) -> NQFLevel:
    """
    Most frequently achieved level across a set of per-question levels.
    On a tie, return the lower (more conservative) level.
    """
    if not levels:
        return NQFLevel.L1
    counts = Counter(levels)
    max_count = max(counts.values())
    tied = sorted(
        [lv for lv, c in counts.items() if c == max_count],
        key=lambda lv: _LEVEL_ORDER.get(lv, 99),
    )
    return tied[0]


def min_level(levels: list[str]) -> NQFLevel:
    """
    Lowest achieved level (conservative placement).
    Used for literacy: a weak writing score gates the overall placement.
    """
    if not levels:
        return NQFLevel.L1
    return min(levels, key=lambda lv: _LEVEL_ORDER.get(lv, 99))


def placement_comment(lit_level: str, num_level: str) -> str:
    """Human-readable placement summary for the report."""
    return f"{_describe_level(lit_level, 'Literacy')}. {_describe_level(num_level, 'Numeracy')}."


def section_kind(section_title: str) -> str:
    """Map a section title to 'literacy', 'numeracy', or 'other'."""
    t = section_title.upper()
    if "LITERACY" in t and "MATH" not in t and "NUMER" not in t:
        return "literacy"
    if "NUMER" in t or "MATH" in t:
        return "numeracy"
    return "other"


def _describe_level(level: NQFLevel, subject: str) -> str:
    if level == NQFLevel.NA:
        return f"{subject} not assessed"
    if level == NQFLevel.POST_L4:
        return f"Post-AET Level for {subject} — no further AET training required"
    return f"NQF Level {int(level[1])} for {subject}"


# ---------------------------------------------------------------------------
# Computation — operates on plain dicts, no ORM coupling
# ---------------------------------------------------------------------------

def compute_levels_from_prefix_scores(
    prefix_scores: dict[str, list[float]],
    prefix_domain: dict[str, str] | None = None,
) -> tuple[str, str]:
    """
    Return (lit_level, num_level) from a prefix → [awarded, max] mapping.
    Applies the Post-L4 numeracy override when all four NUM prefixes qualify.
    prefix_domain maps prefix → "literacy"/"numeracy" for non-LIT/NUM prefixes.
    """
    lit_q_levels: list[str] = []
    num_q_levels: list[str] = []
    num_prefix_pcts: dict[str, float] = {}
    _domain = prefix_domain or {}

    for prefix, (awarded, maximum) in prefix_scores.items():
        pct = round(awarded / maximum * 100) if maximum else 0
        thresholds = NQF_QUESTION_PCT_THRESHOLDS.get(prefix, NQF_PCT_THRESHOLDS)
        level = level_for_percentage(pct, thresholds)

        domain = _domain.get(prefix, "")
        if prefix.startswith("LIT") or domain == "literacy":
            lit_q_levels.append(level)
        elif prefix.startswith("NUM") or domain == "numeracy":
            num_q_levels.append(level)
            num_prefix_pcts[prefix] = pct

    lit_level = min_level(lit_q_levels) if lit_q_levels else NQFLevel.L1
    num_level = modal_level(num_q_levels) if num_q_levels else NQFLevel.NA

    if (
        _NUM_PREFIXES <= num_prefix_pcts.keys()
        and all(num_prefix_pcts[p] >= NQF_POST_L4_NUM_PCT for p in _NUM_PREFIXES)
    ):
        num_level = NQFLevel.POST_L4

    return lit_level, num_level


def compute_group_data(prefix_scores: dict[str, list[float]], groups: list[dict]) -> list[dict]:
    """Build the display-group breakdown (label, awarded, max, pct, level) for a subject."""
    result = []
    for g in groups:
        awarded = sum(prefix_scores.get(p, [0.0, 0.0])[0] for p in g["prefixes"])
        maximum = sum(prefix_scores.get(p, [0.0, 0.0])[1] for p in g["prefixes"])
        pct = round(awarded / maximum * 100) if maximum else 0

        g_levels = []
        for p in g["prefixes"]:
            pa, pm = prefix_scores.get(p, [0.0, 0.0])
            pp = round(pa / pm * 100) if pm else 0
            th = NQF_QUESTION_PCT_THRESHOLDS.get(p, NQF_PCT_THRESHOLDS)
            g_levels.append(level_for_percentage(pp, th))

        result.append({
            "label": g["label"],
            "awarded": awarded,
            "max": maximum,
            "pct": pct,
            "level": min_level(g_levels) if g_levels else NQFLevel.L1,
        })
    return result


# ---------------------------------------------------------------------------
# ORM helpers
# ---------------------------------------------------------------------------

def build_question_metadata(questions) -> dict[int, dict]:
    """
    Build a pk → metadata mapping from a Question queryset.
    Call once per result page; pass the mapping to compute_nqf_placement().
    """
    return {
        q.pk: {
            "prefix": "-".join(q.code.split("-")[:2]),
            "max": float(q.max_marks or 0),
            "section_title": q.section.title,
        }
        for q in questions
    }


def _accumulate_scores(
    responses, q_meta: dict[int, dict]
) -> tuple[dict[str, list[float]], dict[str, list[float]], dict[str, str]]:
    """Walk attempt responses and sum (awarded, max) per prefix and section kind.

    Returns (prefix_scores, section_scores, prefix_domain) where prefix_domain
    maps each prefix to its section domain ("literacy", "numeracy", or "other").
    """
    prefix_scores: dict[str, list[float]] = {}
    section_scores: dict[str, list[float]] = {}
    prefix_domain: dict[str, str] = {}

    for response in responses:
        meta = q_meta.get(response.question_id)
        if not meta:
            continue

        prefix = meta["prefix"]
        sk = section_kind(meta["section_title"])
        prefix_domain.setdefault(prefix, sk)

        try:
            pts = float(response.score.points)
        except AttributeError:
            continue  # no Score record yet — exclude from placement
        except (TypeError, ValueError):
            pts = 0.0
        mx = meta["max"]

        prefix_scores.setdefault(prefix, [0.0, 0.0])
        prefix_scores[prefix][0] += pts
        prefix_scores[prefix][1] += mx

        section_scores.setdefault(sk, [0.0, 0.0])
        section_scores[sk][0] += pts
        section_scores[sk][1] += mx

    return prefix_scores, section_scores, prefix_domain


# ---------------------------------------------------------------------------
# Public result type + entry point
# ---------------------------------------------------------------------------

@dataclass
class NQFPlacement:
    attempt: object
    learner: object
    literacy_groups: list
    numeracy_groups: list
    lit_total: dict
    num_total: dict
    lit_level: NQFLevel
    num_level: NQFLevel
    comment: str


def _lit_display_groups(prefix_scores: dict) -> list[dict]:
    if any(p.startswith("GEN") for p in prefix_scores):
        return NQF_DISPLAY_GROUPS["gen_literacy"]
    return NQF_DISPLAY_GROUPS["literacy"]


def _num_display_groups(prefix_scores: dict) -> list[dict]:
    if any(p.startswith("GEN") for p in prefix_scores):
        return []
    return NQF_DISPLAY_GROUPS["numeracy"]


def compute_nqf_placement(attempt, q_meta: dict[int, dict]) -> NQFPlacement:
    """
    Compute the NQF placement for a single attempt.

    Args:
        attempt: Attempt model instance.
        q_meta:  Question metadata mapping from build_question_metadata().

    Returns:
        NQFPlacement with level labels and group breakdowns for the report.
    """
    responses = attempt.response_set.select_related("score").all()
    prefix_scores, section_scores, prefix_domain = _accumulate_scores(responses, q_meta)

    lit_level, num_level = compute_levels_from_prefix_scores(prefix_scores, prefix_domain)

    lit = section_scores.get("literacy", [0.0, 0.0])
    num = section_scores.get("numeracy", [0.0, 0.0])

    return NQFPlacement(
        attempt=attempt,
        learner=attempt.learner,
        literacy_groups=compute_group_data(prefix_scores, _lit_display_groups(prefix_scores)),
        numeracy_groups=compute_group_data(prefix_scores, _num_display_groups(prefix_scores)),
        lit_total={
            "awarded": lit[0],
            "max": lit[1],
            "pct": round(lit[0] / lit[1] * 100) if lit[1] else 0,
        },
        num_total={
            "awarded": num[0],
            "max": num[1],
            "pct": round(num[0] / num[1] * 100) if num[1] else 0,
        },
        lit_level=lit_level,
        num_level=num_level,
        comment=placement_comment(lit_level, num_level),
    )
