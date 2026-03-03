from django.contrib import admin

# Register your models here.
from .models import AssessmentTemplate, Section, Question, Learner, Attempt, Response

admin.site.register([AssessmentTemplate, Section, Question, Learner, Attempt, Response])

