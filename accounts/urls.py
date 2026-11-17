from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.StaffLoginView.as_view(), name="login"),
    path("login/verify/", views.two_factor_verify, name="two_factor_verify"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("security/", views.security_overview, name="security"),
    path("security/2fa/setup/", views.two_factor_setup, name="two_factor_setup"),
    path("security/2fa/disable/", views.two_factor_disable, name="two_factor_disable"),
    path("security/2fa/recovery/", views.recovery_regenerate, name="recovery_regenerate"),
    path("security/sessions/<int:pk>/revoke/", views.session_revoke, name="session_revoke"),
    path("security/sessions/revoke-others/", views.session_revoke_others, name="session_revoke_others"),
    path("security/password/", views.StaffPasswordChangeView.as_view(), name="password_change"),
]
