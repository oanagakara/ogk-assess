from django.urls import path
from . import views

app_name = "assessment"

urlpatterns = [
    path("", views.home, name="home"),
    path("start/", views.start, name="start"),
    path("attempt/<str:code>/details/", views.attempt_details, name="attempt_details"),
    path("attempt/<str:code>/q/<int:n>/", views.attempt_question, name="attempt_question"),
    path("attempt/<str:code>/submit/", views.attempt_submit, name="attempt_submit"),
    path("attempt/<str:code>/instructions/", views.attempt_instructions, name="attempt_instructions"),
    path("attempt/<str:code>/submit/", views.attempt_submit, name="attempt_submit"),
    path("attempt/<str:code>/submitted/", views.attempt_submitted, name="attempt_submitted"),
    path("assessor/attempts/", views.assessor_attempts, name="assessor_attempts"),
    path("assessor/attempts/", views.assessor_attempts, name="assessor_attempts"),
    path("assessor/attempts/new/", views.assessor_new_attempt, name="assessor_new_attempt"),
    path("assessor/", views.assessor_dashboard, name="assessor_dashboard"),
    path("assessor/attempts/<str:code>/mark/", views.assessor_mark_attempt, name="assessor_mark_attempt"),
]

