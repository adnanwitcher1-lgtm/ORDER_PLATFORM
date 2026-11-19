from django.db import transaction
from django.utils import timezone
from django.urls import reverse
from notifications.models import Notification
from notifications.services import managers_and_admins, notify
from . import oauth
from .crypto import TokenCryptoError
from .models import ConnectionEvent, GoogleAccountConnection

S = GoogleAccountConnection.Status


class ServiceError(Exception):
    """Error that is safe to show to the user."""


def log_event(connection, actor, action, note=""):
    ConnectionEvent.objects.create(connection=connection, actor=actor, action=action, note=note)


def _authorized_channel(access_token):
    """(channel id, title) of the channel chosen on Google's screen; ("", "") if it cannot be read."""
    from ytchannels import resolver
    try:
        items = resolver.fetch_mine(access_token)
    except Exception:    # best effort: never block a connection because of this lookup
        return "", ""
    return (items[0]["id"], items[0].get("title", "")) if items else ("", "")


def _find_connection(sub, channel_id):
    rows = GoogleAccountConnection.objects.filter(google_sub=sub)
    if channel_id:
        # same channel, or an older record made before channels were tracked
        return rows.filter(youtube_channel_id=channel_id).first() or rows.filter(youtube_channel_id="").first()
    return rows.first()


def complete_connection(code, pending, user):
    """Save the account after the OAuth callback. Returns (connection, action)."""
    tokens = oauth.exchange_code(code)
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    granted = (tokens.get("scope") or "").split()

    if oauth.YOUTUBE_SCOPE not in granted:
        raise ServiceError("The YouTube read-only permission was not granted. Allow all permissions on the consent screen.")
    if not access or not refresh:
        raise ServiceError(
            "Google did not return a refresh token. Remove this app's access at myaccount.google.com/permissions and connect again."
        )

    info = oauth.fetch_userinfo(access)
    sub = info.get("sub")
    email = info.get("email", "")
    if not sub:
        raise ServiceError("The Google account could not be identified.")

    channel_id, channel_title = _authorized_channel(access)

    reauth_pk = pending.get("reauth_pk")
    target = None
    if reauth_pk:
        target = GoogleAccountConnection.objects.filter(pk=reauth_pk).first()
        if target is None or target.google_sub != sub:
            raise ServiceError(
                "You signed in with the wrong Google account. Choose the account you want to reconnect."
            )
        if target.youtube_channel_id and channel_id and target.youtube_channel_id != channel_id:
            raise ServiceError(
                "You chose a different YouTube channel on Google's screen. Choose the same channel as before, "
                "or use Connect to add the other channel as a new connection."
            )

    now = timezone.now()
    with transaction.atomic():
        connection = target or _find_connection(sub, channel_id)
        created = connection is None
        if created:
            connection = GoogleAccountConnection(google_sub=sub)
            connection.label = pending.get("label") or channel_title or email
        elif not reauth_pk:
            connection.label = pending.get("label") or connection.label
        if channel_id:
            connection.youtube_channel_id = channel_id

        if pending.get("notes"):
            connection.notes = pending["notes"]
        connection.google_email = email
        connection.scopes = " ".join(granted)
        connection.set_refresh_token(refresh)
        connection.status = S.CONNECTED
        connection.last_error = ""
        connection.last_checked_at = now
        connection.connected_by = user
        connection.connected_at = now
        connection.disconnected_by = None
        connection.disconnected_at = None
        connection.save()

        action = "Connected" if created else ("Reauthorized" if reauth_pk else "Reconnected")
        log_event(
            connection, user, action,
            f"{email} - authorization confirmed by {user.display_name}",
        )
    return connection, action


def check_connection(connection, user=None):
    if connection.status in (S.DISCONNECTED, S.DISABLED) or not connection.has_token:
        raise ServiceError("A disconnected or disabled account cannot be checked.")

    result = oauth.check_refresh_token(connection.get_refresh_token())

    if result.ok:
        recovered = connection.status != S.CONNECTED
        connection.status = S.CONNECTED
        connection.last_checked_at = timezone.now()
        connection.last_error = ""
        connection.save(update_fields=["status", "last_checked_at", "last_error", "updated_at"])
        if recovered:
            log_event(connection, user, "Check passed", "The connection is healthy again.")
    elif result.needs_reauth:
        connection.status = S.NEEDS_REAUTH
        connection.last_error = result.message
        connection.save(update_fields=["status", "last_error", "updated_at"])
        log_event(connection, user, "Reauthorization needed", result.message)
        notify(
            managers_and_admins(), Notification.Kind.REAUTH,
            f"Google account '{connection.label}' needs reauthorization.",
            link=reverse("google:detail", args=[connection.pk]), dedupe=True,
        )
    else:
        connection.last_error = result.message
        connection.save(update_fields=["last_error", "updated_at"])
        log_event(connection, user, "Check failed", result.message)
    return result


def disconnect_connection(connection, user, note=""):
    """Revoke the token at Google (best effort) and delete the local copy."""
    revoked = False
    if connection.has_token:
        try:
            revoked = oauth.revoke_token(connection.get_refresh_token())
        except TokenCryptoError:
            revoked = False

    connection.encrypted_refresh_token = ""
    connection.status = S.DISCONNECTED
    connection.last_error = ""
    connection.disconnected_by = user
    connection.disconnected_at = timezone.now()
    connection.save()

    detail = "Token revoked at Google and deleted locally." if revoked else (
        "Local token deleted, but revocation at Google was not confirmed. "
        "Remove the app's access at myaccount.google.com/permissions."
    )
    log_event(connection, user, "Disconnected", f"{detail} {note}".strip())
    return revoked


def list_channels(connection):
    """The YouTube channels the connected account can read (what YouTube returns for this authorization).

    Raises ServiceError with a message that is safe to show.
    """
    from ytchannels import resolver
    from ytchannels.insights import InsightError

    if connection.status != S.CONNECTED or not connection.has_token:
        raise ServiceError("Only a connected account can list its channels.")
    try:
        access_token = oauth.refresh_access_token(connection.get_refresh_token())
        return resolver.fetch_mine(access_token)
    except oauth.NeedsReauth:
        raise ServiceError("The authorization has expired. Reauthorize this account.")
    except (oauth.OAuthError, InsightError, TokenCryptoError) as exc:
        raise ServiceError(str(exc))

