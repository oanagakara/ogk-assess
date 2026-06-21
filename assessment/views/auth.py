"""Authentication and registration views: invite, register, role switching."""
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User, Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from ..models import AssessorInvite

from ._common import effective_is_moderator, is_assessor, is_auditor, is_moderator


@login_required
@user_passes_test(is_assessor)
def set_active_role(request):
    if request.method != "POST":
        return redirect("assessment:assessor_dashboard")
    role = request.POST.get("role", "")
    if role == "assessor" and is_moderator(request.user):
        request.session["active_role"] = "assessor"
    elif role == "moderator" and is_auditor(request.user):
        request.session["active_role"] = "moderator"
    elif is_moderator(request.user):
        request.session.pop("active_role", None)
    logger.info("Role switched: user=%s active_role=%s", request.user.username, request.session.get("active_role", "full"))
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts=request.get_host(),
        require_https=request.is_secure(),
    ):
        next_url = reverse("assessment:assessor_dashboard")
    return redirect(next_url)


def register(request, token):
    if request.user.is_authenticated:
        return redirect("assessment:assessor_dashboard")

    try:
        invite = AssessorInvite.objects.get(token=token)
    except AssessorInvite.DoesNotExist:
        return render(request, "registration/register_invalid.html", status=404)

    if not invite.is_valid:
        return render(request, "registration/register_invalid.html", status=410)

    errors = []
    form_data = {}

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password1 = request.POST.get("password1", "")
        password2 = request.POST.get("password2", "")

        form_data = {"username": username, "email": email}

        if not username:
            errors.append("Username is required.")
        elif User.objects.filter(username=username).exists():
            errors.append("That username is already taken.")
        if not email:
            errors.append("Email address is required.")
        elif User.objects.filter(email=email).exists():
            errors.append("An account with that email already exists.")
        if password1 != password2:
            errors.append("Passwords do not match.")
        else:
            try:
                validate_password(password1)
            except ValidationError as e:
                errors.extend(e.messages)

        if not errors:
            user = User.objects.create_user(username=username, email=email, password=password1)
            group, _ = Group.objects.get_or_create(name=invite.role)
            user.groups.add(group)
            invite.used_at = timezone.now()
            invite.used_by = user
            invite.save(update_fields=["used_at", "used_by"])
            logger.info(
                "User registered via invite: username=%s role=%s invited_by=%s",
                username, invite.role, invite.created_by.username,
            )
            from django.contrib.auth import login as auth_login
            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("assessment:assessor_dashboard")

    return render(request, "registration/register.html", {
        "errors": errors,
        "form_data": form_data,
        "token": token,
    })


@login_required
@user_passes_test(is_moderator)
def generate_invite(request):
    invite = None
    can_invite_moderator = effective_is_moderator(request)
    if request.method == "POST":
        role = request.POST.get("role", AssessorInvite.ROLE_ASSESSOR)
        if role == AssessorInvite.ROLE_MODERATOR and not can_invite_moderator:
            role = AssessorInvite.ROLE_ASSESSOR
        invite = AssessorInvite.objects.create(
            created_by=request.user,
            role=role,
            expires_at=timezone.now() + timedelta(hours=48),
        )
        logger.info("Invite generated: role=%s created_by=%s token=%s", role, request.user.username, invite.token)
    return render(request, "assessment/generate_invite.html", {
        "invite": invite,
        "can_invite_moderator": can_invite_moderator,
    })
