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
            "needs_review": False,
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
        note = f'Word "{root}" used in a sentence of {word_count} words — flagged for assessor review.'
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

    needs_review = flag_always or some_found or (not all_found)

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


def auto_mark_response(question, response) -> dict | None:
    """
    Attempt to auto-mark a single response.
    Returns a result dict or None if the question has no auto-mark key.
    """
    key = json.loads(question.answer_key_json or "{}")
    if not key.get("auto_mark"):
        return None

    # Match questions
    if question.kind == "match" and key.get("match"):
        return _auto_mark_match(question, response, key)

    # Sentence-using-word questions
    if key.get("sentence_word"):
        return _auto_mark_sentence_word(question, response, key)

    # Keyword-only answer questions
    if key.get("keyword_answer"):
        return _auto_mark_keyword_answer(question, response, key)

    # Standard numeric / text answer
    data = json.loads(response.response_json or "{}")
    response_text = str(data.get("answer", "")).strip()

    answers = [str(a) for a in key.get("answers", [])]
    working_keywords = key.get("working_keywords", [])
    max_marks = float(question.max_marks or 1)
    partial_marks = float(key.get("partial_marks", 0))
    flag_if_no_work = key.get("flag_if_no_working", False)

    correct = _answer_correct(response_text, answers) if answers else False
    has_working = _working_present(response_text, working_keywords) if working_keywords else True

    needs_review = False

    if not correct:
        points = 0.0
        note = "Incorrect or blank answer."
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
            if flag_if_no_work:
                needs_review = True
                note = "Correct answer. Working not detected — flagged for review."
            else:
                note = "Correct answer."
    else:
        points = max_marks
        note = "Correct answer and working detected."

    return {
        "points": points,
        "max_points": max_marks,
        "rubric_json": {
            "mode": "auto",
            "auto_marked": True,
            "needs_review": needs_review,
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
        .select_related("question")
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

        # Don't overwrite a score the assessor already set manually
        try:
            existing = response.score
            if (existing.rubric_json or {}).get("mode") == "manual":
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
