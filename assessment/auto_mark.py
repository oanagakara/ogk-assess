"""
Auto-marking for numeracy and literacy questions.

Answer key formats (stored in question.answer_key_json):

Numeric / text answer:
{
    "auto_mark": true,
    "answers": ["72"],           # acceptable answers — found anywhere in response
    "working_keywords": [],      # all must appear for full marks (working evidence)
    "partial_marks": 0,          # marks if answer correct but working missing
    "flag_if_no_working": false  # flag for review if working absent (single-mark q)
}

Keyword-only answer (answer IS defined by presence of keywords):
{
    "auto_mark": true,
    "keyword_answer": ["Office Administration", "learnership"],
    "partial_marks": 0,          # marks if only SOME keywords present
    "flag_always": false         # always flag for assessor review
}

Match question (kind == "match"):
{
    "auto_mark": true,
    "match": {"t1": "Salary", "t2": "Interview", "t3": "Contract", "t4": "Apply"},
    "marks_per_match": 1
}
"""

import json
import os
import re


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, replace comma-decimal with dot, strip currency/unit noise."""
    t = text.lower()
    # Replace comma used as decimal separator (e.g. "8,5") with dot
    t = re.sub(r'(\d),(\d)', r'\1.\2', t)
    # Strip common units / currency symbols
    t = re.sub(r'[rR]\s*(?=\d)', '', t)   # R400 → 400
    t = re.sub(r'\s*(hours?|km|kg|m|%)\b', '', t)
    return t


def _number_in_text(number_str: str, text: str) -> bool:
    """
    Return True if number_str (e.g. "21") appears as a standalone number in text.
    Uses word-boundary logic so "30" does not match "300".
    """
    norm_text = _normalize(text)
    norm_num  = _normalize(number_str)

    # Build pattern: not preceded or followed by a digit or dot
    pattern = r'(?<![.\d])' + re.escape(norm_num) + r'(?![.\d])'
    return bool(re.search(pattern, norm_text))


def _answer_correct(response_text: str, answers: list) -> bool:
    for ans in answers:
        if _number_in_text(str(ans), response_text):
            return True
    return False


def _working_present(response_text: str, keywords: list) -> bool:
    norm = _normalize(response_text)
    return all(_normalize(kw) in norm for kw in keywords)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _auto_mark_match(question, response, key) -> dict:
    """Mark a drag-and-drop match question."""
    data = json.loads(response.response_json or '{}')
    expected = key.get("match", {})
    marks_per = float(key.get("marks_per_match", 1))
    max_marks = float(question.max_marks or len(expected))
    flag_always = key.get("flag_always", False)

    correct_count = sum(
        1 for target_id, word in expected.items()
        if data.get(target_id, '').strip().lower() == word.strip().lower()
    )
    points = min(correct_count * marks_per, max_marks)
    note = f"{correct_count}/{len(expected)} matches correct."

    return {
        "points": points,
        "max_points": max_marks,
        "rubric_json": {
            "mode": "auto",
            "auto_marked": True,
            "needs_review": flag_always,
            "notes": note,
        },
    }


def _auto_mark_sentence_word(question, response, key) -> dict:
    """
    Mark a 'write a sentence using the word' question.
    Awards the mark if the target word (or a common inflection) appears
    and the response is at least min_words long.
    Always flags for assessor review.
    """
    data = json.loads(response.response_json or "{}")
    response_text = str(data.get("answer", "")).strip()
    max_marks = float(question.max_marks or 1)

    root = key.get("sentence_word", "").lower()
    min_words = key.get("min_words", 4)

    word_count = len(response_text.split())
    pattern = r"\b" + re.escape(root) + r"(s|es|ed|ing|er|ers)?\b"
    word_used = bool(re.search(pattern, response_text.lower()))

    if word_used and word_count >= min_words:
        points = max_marks
        note = f'Word "{root}" used in a sentence of {word_count} words.'
    elif not word_used:
        points = 0.0
        note = f'Target word "{root}" not detected in response.'
    else:
        points = 0.0
        note = f"Response too short ({word_count} words) — may not be a complete sentence."

    return {
        "points": points,
        "max_points": max_marks,
        "rubric_json": {
            "mode": "auto",
            "auto_marked": True,
            "needs_review": True,
            "notes": note,
        },
    }


def _auto_mark_keyword_answer(question, response, key) -> dict:
    """Mark a question where the answer is defined by keyword presence."""
    data = json.loads(response.response_json or "{}")
    response_text = str(data.get("answer", "")).strip()

    keywords = key.get("keyword_answer", [])
    max_marks = float(question.max_marks or 1)
    partial_marks = float(key.get("partial_marks", 0))
    flag_always = key.get("flag_always", False)

    found = [kw for kw in keywords if kw.lower() in response_text.lower()]
    all_found = len(found) == len(keywords)
    some_found = 0 < len(found) < len(keywords)

    if all_found:
        points = max_marks
        note = f"All required terms found ({', '.join(found)})."
    elif some_found:
        points = partial_marks
        note = (
            f"Partial: found {', '.join(found)}; "
            f"missing {', '.join(kw for kw in keywords if kw not in found)}."
        )
    else:
        points = 0.0
        note = f"None of the required terms ({', '.join(keywords)}) detected."

    # Review needed if: always-flag set, OR partial match (ambiguous), OR all found but flag_always.
    # A zero-keyword match is unambiguously wrong — no review needed.
    needs_review = flag_always or some_found

    return {
        "points": points,
        "max_points": max_marks,
        "rubric_json": {
            "mode": "auto",
            "auto_marked": True,
            "needs_review": needs_review,
            "notes": note,
        },
    }


def _auto_mark_keyword_per_mark(question, response, key) -> dict:
    """
    Each listed keyword found independently awards one mark (or marks_per_keyword).
    Scoring is additive and unambiguous — no review unless flag_always is set.
    """
    data = json.loads(response.response_json or "{}")
    response_text = str(data.get("answer", "")).strip().lower()
    max_marks = float(question.max_marks or 0)
    marks_per = float(key.get("marks_per_keyword", 1))
    flag_always = key.get("flag_always", False)

    keywords = key.get("keyword_per_mark", [])
    found = [kw for kw in keywords if kw.lower() in response_text]
    points = min(len(found) * marks_per, max_marks)

    note = (
        f"Found: {', '.join(found)}. {points}/{max_marks} marks."
        if found else "None of the expected terms found."
    )
    return {
        "points": points,
        "max_points": max_marks,
        "rubric_json": {"mode": "auto", "auto_marked": True, "needs_review": flag_always, "notes": note},
    }


def _auto_mark_tiered_keyword(question, response, key) -> dict:
    """
    Tiered keyword scoring. Tiers are evaluated in order; the first matching tier wins.

    Each tier:
      marks       — points to award if this tier matches
      require_all — list of phrases; ALL must appear in the response
      require_any — list of phrases; AT LEAST ONE must appear
      require_not — list of phrases; if ANY appear, this tier is skipped
      min_words   — minimum word count the response must reach (optional)

    If no tier matches, zero marks are awarded.
    """
    data = json.loads(response.response_json or "{}")
    response_text = str(data.get("answer", "")).strip()
    response_lower = response_text.lower()
    word_count = len(response_text.split())
    max_marks = float(question.max_marks or 0)
    flag_always = key.get("flag_always", False)

    for tier in key.get("tiered_keyword", []):
        tier_marks = float(tier.get("marks", 0))
        min_words = int(tier.get("min_words", 0))
        require_all = tier.get("require_all", [])
        require_any = tier.get("require_any", [])
        require_not = tier.get("require_not", [])

        if min_words and word_count < min_words:
            continue
        if require_all and not all(phrase.lower() in response_lower for phrase in require_all):
            continue
        if require_any and not any(phrase.lower() in response_lower for phrase in require_any):
            continue
        if require_not and any(phrase.lower() in response_lower for phrase in require_not):
            continue

        note = tier.get("note") or f"{tier_marks}/{max_marks} marks awarded."
        return {
            "points": tier_marks,
            "max_points": max_marks,
            "rubric_json": {"mode": "auto", "auto_marked": True, "needs_review": flag_always, "notes": note},
        }

    no_match_note = key.get("no_match_note") or "No criteria met — zero marks awarded."
    return {
        "points": 0.0,
        "max_points": max_marks,
        "rubric_json": {"mode": "auto", "auto_marked": True, "needs_review": flag_always, "notes": no_match_note},
    }


def _auto_mark_ai_rubric(question, response, key) -> dict:
    """
    AI-suggested marking for written responses. Always sets needs_review=True.
    Calls Claude with the rubric and learner text; pre-fills per-criterion scores.
    """
    import anthropic

    data = json.loads(response.response_json or "{}")
    response_text = str(data.get("answer", "")).strip()
    criteria = key.get("criteria", [])
    max_marks = float(question.max_marks or 0)

    if not response_text:
        return _zero_score(question)

    rubric_lines = "\n".join(
        f"- {c['label']} (0–{c['max_points']} marks): {c.get('description', '')}"
        for c in criteria
    )
    criteria_template = "\n".join(
        f'    "{c["key"]}": {{"score": <int 0–{int(c["max_points"])}>, "note": "<reason>"}}'
        for c in criteria
    )

    prompt = (
        f"You are marking a written response for an adult learner placement assessment.\n\n"
        f"TASK PROMPT: {question.prompt}\n\n"
        f"RUBRIC:\n{rubric_lines}\n\n"
        f"LEARNER RESPONSE:\n{response_text}\n\n"
        f"Score each criterion. Respond ONLY with a JSON object:\n"
        f"{{\n"
        f'  "criteria": {{\n{criteria_template}\n  }},\n'
        f'  "summary": "<1–2 sentence overall comment>"\n'
        f"}}"
    )

    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )

        content = msg.content[0].text.strip()
        if content.startswith("```"):
            content = re.sub(r"^```[^\n]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        ai_result = json.loads(content)

        criteria_payload = []
        total_points = 0.0
        for c in criteria:
            ck = c["key"]
            max_c = float(c.get("max_points", 0))
            ai_score = float(ai_result.get("criteria", {}).get(ck, {}).get("score", 0))
            pts = max(0.0, min(ai_score, max_c))
            note = ai_result.get("criteria", {}).get(ck, {}).get("note", "")
            criteria_payload.append({
                "key": ck,
                "label": c["label"],
                "max_points": max_c,
                "points": pts,
                "feedback": note,
            })
            total_points += pts

        return {
            "points": total_points,
            "max_points": max_marks,
            "rubric_json": {
                "mode": "ai",
                "auto_marked": True,
                "needs_review": True,
                "criteria": criteria_payload,
                "notes": ai_result.get("summary", "AI-suggested scores — please review."),
            },
        }

    except Exception as exc:
        return {
            "points": 0.0,
            "max_points": max_marks,
            "rubric_json": {
                "mode": "ai",
                "auto_marked": True,
                "needs_review": True,
                "notes": f"AI marking unavailable ({exc}). Please mark manually.",
            },
        }


def auto_mark_response(question, response) -> dict | None:
    """
    Attempt to auto-mark a single response.
    Returns a result dict or None if the question has no auto-mark key.
    """
    key = json.loads(question.answer_key_json or "{}")
    if not key.get("auto_mark"):
        return None

    # AI rubric marking for written questions
    if key.get("ai_rubric"):
        return _auto_mark_ai_rubric(question, response, key)

    # Match questions
    if question.kind == "match" and key.get("match"):
        return _auto_mark_match(question, response, key)

    # Sentence-using-word questions
    if key.get("sentence_word"):
        return _auto_mark_sentence_word(question, response, key)

    # Keyword-only answer questions
    if key.get("keyword_answer"):
        return _auto_mark_keyword_answer(question, response, key)

    # Per-keyword independent scoring
    if key.get("keyword_per_mark"):
        return _auto_mark_keyword_per_mark(question, response, key)

    # Tiered keyword scoring
    if key.get("tiered_keyword"):
        return _auto_mark_tiered_keyword(question, response, key)

    # Standard numeric / text answer
    data = json.loads(response.response_json or "{}")
    response_text = str(data.get("answer", "")).strip()

    answers = [str(a) for a in key.get("answers", [])]
    working_keywords = key.get("working_keywords", [])
    max_marks = float(question.max_marks or 1)
    partial_marks = float(key.get("partial_marks", 0))
    flag_always = key.get("flag_always", False)
    flag_if_no_work = key.get("flag_if_no_working", False)

    correct = _answer_correct(response_text, answers) if answers else False
    has_working = _working_present(response_text, working_keywords) if working_keywords else True

    needs_review = False

    if not correct:
        points = 0.0
        needs_review = flag_always
        note = (
            "Incorrect answer — verify against working sheet."
            if flag_always else "Incorrect or blank answer."
        )
    elif working_keywords and not has_working:
        if partial_marks > 0:
            points = partial_marks
            needs_review = True
            note = (
                f"Correct answer found but expected working "
                f"({', '.join(working_keywords)}) not detected. "
                f"Awarded {partial_marks}/{max_marks} — flagged for review."
            )
        else:
            points = max_marks
            needs_review = flag_always or flag_if_no_work
            note = "Correct answer. Verify working sheet." if needs_review else "Correct answer."
    else:
        points = max_marks
        needs_review = flag_always
        note = "Correct answer. Verify working sheet." if flag_always else "Correct answer."

    return {
        "points": points,
        "max_points": max_marks,
        "rubric_json": {
            "mode": "auto",
            "auto_marked": True,
            "needs_review": needs_review,
            "verify_working": flag_always,
            "answer_found": correct,
            "working_found": has_working,
            "notes": note,
        },
    }


def _is_blank_response(response) -> bool:
    """True if the learner submitted no meaningful answer."""
    data = json.loads(response.response_json or "{}")
    if not data:
        return True
    # Flatten all string values and check if any contain non-whitespace
    values = data.values() if isinstance(data, dict) else [data]
    return not any(str(v).strip() for v in values)


def _zero_score(question) -> dict:
    return {
        "points": 0.0,
        "max_points": float(question.max_marks or 0),
        "rubric_json": {
            "mode": "auto",
            "auto_marked": True,
            "needs_review": False,
            "notes": "No response submitted.",
        },
    }


def auto_mark_attempt(attempt) -> int:
    """
    Auto-mark all responses in an attempt.
    Blank responses receive 0 marks with no review flag.
    Responses to questions without an answer key are left unscored unless blank.
    Skips responses that already have a manually set score.
    Returns the number of questions auto-marked.
    """
    from .models import Response, Score

    responses = (
        Response.objects
        .filter(attempt=attempt)
        .select_related("question", "score")
    )

    count = 0
    for response in responses:
        question = response.question

        if _is_blank_response(response):
            result = _zero_score(question)
        else:
            result = auto_mark_response(question, response)
            if result is None:
                continue

        # Don't overwrite existing scores — manual OR already auto-marked
        try:
            existing = response.score
            if (existing.rubric_json or {}).get("mode") in ("manual", "auto", "ai"):
                continue
        except Score.DoesNotExist:
            pass

        Score.objects.update_or_create(
            response=response,
            defaults={
                "assessor": None,
                "points": result["points"],
                "max_points": result["max_points"],
                "rubric_json": result["rubric_json"],
            },
        )
        count += 1

    return count
