from datetime import date
from django import forms

from django.core.exceptions import ValidationError

from .models import Learner, Attempt, AssessmentTemplate, ExamSession

INPUT = "input input-bordered w-full"
SELECT = "select select-bordered w-full"


class StartForm(forms.Form):
    code = forms.CharField(
        max_length=32,
        label="Assessment code",
        widget=forms.TextInput(attrs={"class": "input input-bordered w-full", "autocomplete": "off"}),
    )


class LearnerForm(forms.ModelForm):
    class Meta:
        model = Learner
        fields = ["first_names", "surname", "id_number", "dob", "gender", "demographic"]
        widgets = {
            "first_names": forms.TextInput(attrs={"class": INPUT}),
            "surname": forms.TextInput(attrs={"class": INPUT}),
            "id_number": forms.TextInput(attrs={"class": INPUT}),
            "dob": forms.DateInput(attrs={"class": INPUT, "type": "date", "min": "1920-01-01", "max": "2015-12-31"}),
            "gender": forms.Select(attrs={"class": SELECT}),
            "demographic": forms.Select(attrs={"class": SELECT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["id_number"].required = False

    def clean(self):
        cleaned_data = super().clean()

        first_names = (cleaned_data.get("first_names") or "").strip()
        surname = (cleaned_data.get("surname") or "").strip()
        id_number = (cleaned_data.get("id_number") or "").strip()
        dob = cleaned_data.get("dob")

        if first_names.lower() == "temp":
            self.add_error("first_names", "Please enter your real first name(s).")

        if surname.lower() == "learner":
            self.add_error("surname", "Please enter your real surname.")

        # Normalise empty string to None so multiple blank entries don't collide on the unique constraint
        cleaned_data["id_number"] = id_number or None

        if dob and not id_number:
            if dob < date(1920, 1, 1) or dob > date(2015, 12, 31):
                self.add_error("dob", "Date of birth must be between 1920 and 2015.")

        if id_number:
            if len(id_number) != 13 or not id_number.isdigit():
                self.add_error("id_number", "Enter a valid 13-digit South African ID number.")
                return cleaned_data

            yy = int(id_number[0:2])
            mm = int(id_number[2:4])
            dd = int(id_number[4:6])

            today = date.today()
            current_yy = today.year % 100

            # SA ID birth year encoded as 2 digits (YYMMDD).
            # Infer century: if YY <= current 2-digit year, assume 2000s; otherwise 1900s.
            century = 2000 if yy <= current_yy else 1900
            full_year = century + yy

            try:
                id_dob = date(full_year, mm, dd)
            except ValueError:
                self.add_error("id_number", "The ID number contains an invalid date of birth.")
                return cleaned_data

            if dob and dob != id_dob:
                self.add_error(
                    "dob",
                    "Date of birth does not match the ID number.",
                )

        return cleaned_data



class HonestyForm(forms.Form):
    popia_accept = forms.BooleanField(
        label="I have read and understood the personal information notice above."
    )
    honesty_name = forms.CharField(max_length=200, label="Type your full name to sign")
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
                "autocomplete": "off",
                "spellcheck": "false",
                "autocorrect": "off",
                "autocapitalize": "off",
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


class ExamSessionForm(forms.ModelForm):

    template = forms.ModelChoiceField(
        queryset=AssessmentTemplate.objects.order_by("-created_at"),
        widget=forms.Select(attrs={"class": SELECT}),
        empty_label="Select template",
    )

    class Meta:
        model = ExamSession
        fields = ["template"]
