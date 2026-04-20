"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import os
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from assessment import views as assessment_views

handler400 = assessment_views.handler400
handler403 = assessment_views.handler403
handler404 = assessment_views.handler404
handler500 = assessment_views.handler500

_ADMIN_PATH = os.environ.get("ADMIN_URL_PREFIX", "_platform-admin") + "/"

urlpatterns = [
    path(_ADMIN_PATH, admin.site.urls),
    path("accounts", RedirectView.as_view(url="/accounts/login/", permanent=False)),
    path("accounts/logout", RedirectView.as_view(url="accounts/login/", permanent=False)),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("assessment.urls")),
]
