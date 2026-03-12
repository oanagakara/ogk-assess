from django import forms
from .models import Learner, Attempt, AssessmentTemplate

INPUT = "input input-bordered w-full"
SELECT = "select select-bordered w-full"


class StartForm(forms.Form):
    code = forms.CharField(max_length=32, label="Assessment code")


class LearnerForm(forms.ModelForm):
    class Meta:
        model = Learner
        fields = ["first_names", "surname", "id_number", "dob", "gender", "demographic"]
        widgets = {
            "first_names": forms.TextInput(attrs={"class": INPUT}),
            "surname": forms.TextInput(attrs={"class": INPUT}),
            "id_number": forms.TextInput(attrs={"class": INPUT}),
            "dob": forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "gender": forms.Select(attrs={"class": SELECT}),
            "demographic": forms.Select(attrs={"class": SELECT}),
        }


class HonestyForm(forms.Form):
    honesty_name = forms.CharField(max_length=200, label="Type your full name please?")
    accept = forms.BooleanField(
        label="I declare and commit that I will submit my own work during this assessment."
    )


class TextResponseForm(forms.Form):
    answer = forms.CharField(
        required=False,
        label="Your answer",
        widget=forms.Textarea(
            attrs={
                "class": "textarea textarea-bordered w-full",
                "rows": 6,
                "placeholder": "Type your answer here...",
            }
        ),
    )


class MatchResponseForm(forms.Form):
    # Allow skipping. We'll still store whatever is posted (even empty).
    response_json = forms.CharField(widget=forms.HiddenInput(), required=False)

class AttemptForm(forms.ModelForm):

    template = forms.ModelChoiceField(
        queryset=AssessmentTemplate.objects.order_by("-created_at"),
        widget=forms.Select(attrs={"class": "select select-bordered w-full"}),
        empty_label="Select Template"
    )

    class Meta:
        model = Attempt
        fields = ["template"]
