from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from django.conf import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
SCOPES = ["openid", "email", YOUTUBE_SCOPE, ANALYTICS_SCOPE]


class OAuthError(Exception):
    """Error that is safe to show to the user. It never contains secrets."""


class NeedsReauth(OAuthError):
    """The refresh token is no longer valid (revoked or expired)."""


@dataclass(frozen=True)
class RefreshResult:
    ok: bool
    needs_reauth: bool
    message: str


def is_configured():
    return all([
        getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", ""),
        getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", ""),
        getattr(settings, "GOOGLE_OAUTH_REDIRECT_URI", ""),
        getattr(settings, "TOKEN_ENCRYPTION_KEY", ""),
    ])


def build_auth_url(state, login_hint=""):
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # needed for a refresh token
        "prompt": "consent",
        "state": state,
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code):
    try:
        response = requests.post(TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=10)
    except requests.RequestException:
        raise OAuthError("Could not reach Google. Please try again.")
    if response.status_code != 200:
        raise OAuthError("Google did not accept the authorization code. Please connect again.")
    return response.json()


def fetch_userinfo(access_token):
    try:
        response = requests.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10
        )
    except requests.RequestException:
        raise OAuthError("Could not get the account information from Google.")
    if response.status_code != 200:
        raise OAuthError("Could not get the account information from Google.")
    return response.json()


def check_refresh_token(refresh_token):
    """Whether the refresh token is still valid. This performs no YouTube action."""
    try:
        response = requests.post(TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }, timeout=10)
    except requests.RequestException:
        return RefreshResult(False, False, "Network error: could not reach Google.")

    if response.status_code == 200:
        return RefreshResult(True, False, "")

    try:
        error = response.json().get("error", "")
    except ValueError:
        error = ""
    if error == "invalid_grant":
        return RefreshResult(False, True, "Authorization has expired or been revoked.")
    return RefreshResult(False, False, f"Google error (HTTP {response.status_code}).")


def revoke_token(token):
    try:
        response = requests.post(REVOKE_URL, data={"token": token}, timeout=10)
    except requests.RequestException:
        return False
    return response.status_code == 200


def refresh_access_token(refresh_token):
    """A short-lived access token for read-only YouTube calls."""
    try:
        response = requests.post(TOKEN_URL, data={
            "refresh_token": refresh_token,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }, timeout=10)
    except requests.RequestException:
        raise OAuthError("Network error: could not reach Google.")
    if response.status_code == 200:
        token = response.json().get("access_token", "")
        if token:
            return token
        raise OAuthError("Google did not return an access token.")
    try:
        error = response.json().get("error", "")
    except ValueError:
        error = ""
    if error == "invalid_grant":
        raise NeedsReauth("Authorization has expired or been revoked.")
    raise OAuthError(f"Google error (HTTP {response.status_code}).")
