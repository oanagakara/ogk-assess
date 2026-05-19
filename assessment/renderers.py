import json
import random
from .forms import TextResponseForm, MatchResponseForm


class BaseRenderer:
    template = "assessment/question.html"

    def __init__(self, question, spec, response, attempt=None):
        self.question = question
        self.spec = spec
        self.response = response
        self.attempt = attempt

    def _rng(self):
        """Seeded RNG — stable per attempt+question, varies across attempts."""
        seed = f"{self.attempt.code}:{self.question.pk}" if self.attempt else None
        return random.Random(seed)

    def get_form(self, request):
        return None

    def save(self, request, form):
        pass

    def get_context(self):
        return {}


RENDERERS = {}


def register_renderer(name):
    def decorator(cls):
        RENDERERS[name] = cls
        return cls

    return decorator


def get_renderer(question, spec, response, attempt=None):
    layout = spec.get("layout")

    if layout in RENDERERS:
        return RENDERERS[layout](question, spec, response, attempt=attempt)

    if question.kind in RENDERERS:
        return RENDERERS[question.kind](question, spec, response, attempt=attempt)

    return BaseRenderer(question, spec, response, attempt=attempt)


@register_renderer("text")
class TextRenderer(BaseRenderer):

    def get_form(self, request):
        existing = ""
        if self.response.response_json:
            try:
                existing = json.loads(self.response.response_json).get("answer", "")
            except (json.JSONDecodeError, AttributeError):
                existing = ""
        self._current_answer = existing
        return TextResponseForm(
            request.POST or None,
            initial={"answer": existing},
        )

    def save(self, request, form):
        if form.is_valid():
            ans = form.cleaned_data.get("answer", "")
        else:
            ans = request.POST.get("answer", "")
        self.response.response_json = json.dumps({"answer": ans}, ensure_ascii=False)
        self.response.save(update_fields=["response_json"])

    def get_context(self):
        return {"current_answer": getattr(self, "_current_answer", "")}


@register_renderer("match")
class MatchRenderer(BaseRenderer):

    def get_form(self, request):
        existing = self.response.response_json or "{}"
        return MatchResponseForm(
            request.POST or None,
            initial={"response_json": existing},
        )

    def save(self, request, form):
        if form.is_valid():
            raw = form.cleaned_data.get("response_json", "")
        else:
            raw = request.POST.get("response_json", "")
        self.response.response_json = raw
        self.response.save(update_fields=["response_json"])

    def get_context(self):
        rng = self._rng()
        if isinstance(self.spec.get("bank"), list):
            rng.shuffle(self.spec["bank"])
        targets = self.spec.get("targets", [])
        if isinstance(targets, list):
            rng.shuffle(targets)

        matched = {}
        if self.response.response_json:
            try:
                matched = json.loads(self.response.response_json)
            except (json.JSONDecodeError, ValueError):
                matched = {}

        pairs = [
            {"text": t.get("text", ""), "word": matched.get(str(t.get("id", "")))}
            for t in targets
        ]
        return {"match_pairs": pairs}


@register_renderer("long_division")
class LongDivisionRenderer(BaseRenderer):

    def get_form(self, request):
        existing = ""
        if self.response.response_json:
            try:
                existing = json.loads(self.response.response_json).get("answer", "")
            except (json.JSONDecodeError, AttributeError):
                existing = ""
        self._current_answer = existing
        return None

    def save(self, request, form):
        ans = request.POST.get("answer", "").strip()
        self.response.response_json = json.dumps({"answer": ans}, ensure_ascii=False)
        self.response.save(update_fields=["response_json"])

    def get_context(self):
        return {"current_answer": getattr(self, "_current_answer", "")}


@register_renderer("form_fill")
class FormFillRenderer(BaseRenderer):

    def get_form(self, request):
        form_fill_values = {}
        if self.response.response_json:
            try:
                loaded = json.loads(self.response.response_json)
                if isinstance(loaded, dict):
                    form_fill_values = loaded
            except (json.JSONDecodeError, ValueError):
                pass
        for field in self.spec.get("fields", []):
            field["value"] = form_fill_values.get(field["name"], "")
        self._form_fill_values = form_fill_values
        return None

    def save(self, request, form):
        out = {}
        for f in self.spec.get("fields", []):
            out[f["name"]] = (request.POST.get(f"ff_{f['name']}", "") or "").strip()
        self.response.response_json = json.dumps(out, ensure_ascii=False)
        self.response.save(update_fields=["response_json"])

    def get_context(self):
        return {"form_fill_values": getattr(self, "_form_fill_values", {})}
