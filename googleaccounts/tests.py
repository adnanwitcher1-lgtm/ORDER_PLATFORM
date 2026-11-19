from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from . import oauth, services
from .crypto import decrypt_token, encrypt_token
from .models import GoogleAccountConnection

S = GoogleAccountConnection.Status
TEST_KEY = Fernet.generate_key().decode()
OAUTH_SETTINGS = dict(
    TOKEN_ENCRYPTION_KEY=TEST_KEY,
    GOOGLE_OAUTH_CLIENT_ID="test-client-id",
    GOOGLE_OAUTH_CLIENT_SECRET="test-client-secret",
    GOOGLE_OAUTH_REDIRECT_URI="http://127.0.0.1:8000/google-accounts/callback/",
)
GOOD_TOKENS = {
    "access_token": "acc",
    "refresh_token": "refresh-token-123",
    "scope": f"openid email {oauth.YOUTUBE_SCOPE}",
}
GOOD_INFO = {"sub": "sub-1", "email": "ali@example.com"}


@override_settings(**OAUTH_SETTINGS)
class CryptoTests(SimpleTestCase):
    def test_roundtrip(self):
        self.assertEqual(decrypt_token(encrypt_token("secret-value")), "secret-value")

    def test_ciphertext_is_not_plain(self):
        self.assertNotIn("secret-value", encrypt_token("secret-value"))


@override_settings(**OAUTH_SETTINGS)
class AuthUrlTests(SimpleTestCase):
    def test_auth_url_parameters(self):
        url = oauth.build_auth_url("my-state", "ali@example.com")
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        self.assertIn("state=my-state", url)
        self.assertIn("youtube.readonly", url)
        self.assertIn("login_hint=ali%40example.com", url)


@override_settings(**OAUTH_SETTINGS)
class GoogleBaseTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")
        self.operator = User.objects.create_user("op1", password="pass12345", role="operator")
        self.sales = User.objects.create_user("sales1", password="pass12345", role="sales")

    def connect(self, tokens=None, info=None, pending=None, channels=()):
        with patch("googleaccounts.oauth.exchange_code", return_value=tokens or GOOD_TOKENS), \
             patch("googleaccounts.oauth.fetch_userinfo", return_value=info or GOOD_INFO), \
             patch("ytchannels.resolver.fetch_mine", return_value=list(channels)):
            return services.complete_connection("code", pending or {"label": "Ali main"}, self.manager)


class ConnectionServiceTests(GoogleBaseTest):
    def test_connect_creates_encrypted_connection(self):
        connection, action = self.connect()
        self.assertEqual(action, "Connected")
        self.assertEqual(connection.status, S.CONNECTED)
        self.assertEqual(connection.google_email, "ali@example.com")
        self.assertNotIn("refresh-token-123", connection.encrypted_refresh_token)
        self.assertEqual(connection.get_refresh_token(), "refresh-token-123")
        self.assertTrue(connection.events.filter(action="Connected").exists())

    def test_reconnect_same_account_does_not_duplicate(self):
        self.connect()
        _, action = self.connect(pending={"label": "Ali new"})
        self.assertEqual(action, "Reconnected")
        self.assertEqual(GoogleAccountConnection.objects.count(), 1)

    def test_missing_youtube_scope_rejected(self):
        tokens = dict(GOOD_TOKENS, scope="openid email")
        with self.assertRaises(services.ServiceError):
            self.connect(tokens=tokens)
        self.assertEqual(GoogleAccountConnection.objects.count(), 0)

    def test_missing_refresh_token_rejected(self):
        tokens = {k: v for k, v in GOOD_TOKENS.items() if k != "refresh_token"}
        with self.assertRaises(services.ServiceError):
            self.connect(tokens=tokens)

    def test_reauth_wrong_account_rejected(self):
        connection, _ = self.connect()
        with self.assertRaises(services.ServiceError):
            self.connect(
                info={"sub": "other-sub", "email": "other@example.com"},
                pending={"label": "x", "reauth_pk": connection.pk},
            )

    def test_check_ok(self):
        connection, _ = self.connect()
        with patch("googleaccounts.oauth.check_refresh_token", return_value=oauth.RefreshResult(True, False, "")):
            result = services.check_connection(connection, self.manager)
        connection.refresh_from_db()
        self.assertTrue(result.ok)
        self.assertEqual(connection.status, S.CONNECTED)

    def test_check_needs_reauth(self):
        connection, _ = self.connect()
        result = oauth.RefreshResult(False, True, "Authorization has expired or been revoked.")
        with patch("googleaccounts.oauth.check_refresh_token", return_value=result):
            services.check_connection(connection, self.manager)
        connection.refresh_from_db()
        self.assertEqual(connection.status, S.NEEDS_REAUTH)
        self.assertTrue(connection.events.filter(action="Reauthorization needed").exists())

    def test_check_transient_error_keeps_status(self):
        connection, _ = self.connect()
        result = oauth.RefreshResult(False, False, "Network error")
        with patch("googleaccounts.oauth.check_refresh_token", return_value=result):
            services.check_connection(connection, self.manager)
        connection.refresh_from_db()
        self.assertEqual(connection.status, S.CONNECTED)
        self.assertEqual(connection.last_error, "Network error")

    def test_disconnect_deletes_token_and_revokes(self):
        connection, _ = self.connect()
        with patch("googleaccounts.oauth.revoke_token", return_value=True) as revoke:
            revoked = services.disconnect_connection(connection, self.manager)
        connection.refresh_from_db()
        revoke.assert_called_once_with("refresh-token-123")
        self.assertTrue(revoked)
        self.assertEqual(connection.status, S.DISCONNECTED)
        self.assertEqual(connection.encrypted_refresh_token, "")
        self.assertEqual(connection.disconnected_by, self.manager)


class GoogleViewTests(GoogleBaseTest):
    def test_operator_can_view_list(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("google:list")).status_code, 200)

    def test_operator_cannot_connect(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("google:connect")).status_code, 403)

    def test_sales_cannot_view_list(self):
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse("google:list")).status_code, 403)

    def test_callback_with_bad_state_creates_nothing(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("google:callback"), {"state": "bad", "code": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(GoogleAccountConnection.objects.count(), 0)