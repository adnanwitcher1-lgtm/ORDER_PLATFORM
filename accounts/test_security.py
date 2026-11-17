import time

import pyotp
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog

from . import twofactor
from .models import RecoveryCode, TwoFactorDevice, UserSession

User = get_user_model()


def current_code(secret):
    return pyotp.TOTP(secret).now()


class TwoFactorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("mgr1", password="pass12345", role="manager")

    def enable(self):
        secret = twofactor.new_secret()
        codes = twofactor.confirm_setup(self.user, secret, current_code(secret))
        return secret, codes

    def login_step1(self, client=None, password="pass12345"):
        client = client or self.client
        return client.post(reverse("accounts:login"), {"username": "mgr1", "password": password})

    # ---- setup
    def test_setup_flow_stores_encrypted_secret_and_codes(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("accounts:two_factor_setup"))
        self.assertContains(page, "<svg")
        secret = self.client.session["setup_2fa_secret"]
        bad = self.client.post(reverse("accounts:two_factor_setup"), {"code": "000000"})
        self.assertContains(bad, "not correct")
        self.assertFalse(twofactor.has_two_factor(self.user))
        ok = self.client.post(reverse("accounts:two_factor_setup"), {"code": current_code(secret)})
        self.assertContains(ok, "Recovery codes")
        self.assertTrue(twofactor.has_two_factor(self.user))
        device = TwoFactorDevice.objects.get(user=self.user)
        self.assertNotIn(secret, device.secret_encrypted)
        self.assertEqual(RecoveryCode.objects.filter(user=self.user).count(), 8)
        self.assertTrue(AuditLog.objects.filter(action="2FA enabled").exists())

    # ---- login
    def test_login_without_2fa_is_unchanged(self):
        response = self.login_step1()
        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_password_alone_does_not_log_in_when_2fa_is_on(self):
        secret, _ = self.enable()
        response = self.login_step1()
        self.assertRedirects(response, reverse("accounts:two_factor_verify"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 302)    # still anonymous

    def test_correct_code_completes_login_and_next_is_kept(self):
        secret, _ = self.enable()
        self.client.post(reverse("accounts:login") + "?next=/customers/", {"username": "mgr1", "password": "pass12345"})
        TwoFactorDevice.objects.filter(user=self.user).update(last_counter=0)
        response = self.client.post(reverse("accounts:two_factor_verify"), {"code": current_code(secret)})
        self.assertRedirects(response, "/customers/", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)

    def test_wrong_code_fails_and_locks_after_five(self):
        self.enable()
        self.login_step1()
        for _ in range(5):
            self.client.post(reverse("accounts:two_factor_verify"), {"code": "111111"})
        locked = self.client.post(reverse("accounts:two_factor_verify"), {"code": "111111"})
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="2FA failed").exists())

    def test_code_cannot_be_reused(self):
        secret, _ = self.enable()
        TwoFactorDevice.objects.filter(user=self.user).update(last_counter=0)
        code = current_code(secret)
        self.assertTrue(twofactor.verify_code(self.user, code))
        self.assertFalse(twofactor.verify_code(self.user, code))

    def test_clock_drift_of_one_step_is_accepted_two_is_not(self):
        secret, _ = self.enable()
        TwoFactorDevice.objects.filter(user=self.user).update(last_counter=0)
        totp = pyotp.TOTP(secret)
        self.assertFalse(twofactor.verify_code(self.user, totp.at(time.time() + 120)))
        self.assertTrue(twofactor.verify_code(self.user, totp.at(time.time() - 30)))

    def test_recovery_code_works_once(self):
        _, codes = self.enable()
        self.login_step1()
        response = self.client.post(reverse("accounts:two_factor_verify"), {"code": codes[0]})
        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)
        self.assertEqual(twofactor.unused_recovery_count(self.user), 7)
        other = Client()
        self.login_step1(other)
        again = other.post(reverse("accounts:two_factor_verify"), {"code": codes[0]})
        self.assertContains(again, "not correct")

    def test_pending_login_expires_and_needs_password_first(self):
        self.enable()
        self.assertRedirects(self.client.get(reverse("accounts:two_factor_verify")), reverse("accounts:login"))
        self.login_step1()
        session = self.client.session
        pending = session["pending_2fa"]; pending["at"] = time.time() - 600
        session["pending_2fa"] = pending; session.save()
        self.assertRedirects(self.client.get(reverse("accounts:two_factor_verify")), reverse("accounts:login"))

    def test_wrong_password_never_reaches_second_step(self):
        self.enable()
        response = self.login_step1(password="wrong")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("pending_2fa", self.client.session)

    # ---- manage
    def test_disable_needs_password_and_code(self):
        secret, _ = self.enable()
        self.client.force_login(self.user)
        TwoFactorDevice.objects.filter(user=self.user).update(last_counter=0)
        self.client.post(reverse("accounts:two_factor_disable"), {"password": "bad", "code": current_code(secret)})
        self.assertTrue(twofactor.has_two_factor(self.user))
        self.client.post(reverse("accounts:two_factor_disable"), {"password": "pass12345", "code": current_code(secret)})
        self.assertFalse(twofactor.has_two_factor(self.user))
        self.assertEqual(RecoveryCode.objects.filter(user=self.user).count(), 0)

    @override_settings(REQUIRE_2FA=True)
    def test_required_mode_redirects_until_setup_and_blocks_disable(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse("core:dashboard")), reverse("accounts:two_factor_setup"),
                             fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("accounts:two_factor_setup")).status_code, 200)
        self.enable()
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)
        self.client.post(reverse("accounts:two_factor_disable"), {"password": "pass12345", "code": "x"})
        self.assertTrue(twofactor.has_two_factor(self.user))

    @override_settings(REQUIRE_2FA=True)
    def test_portal_is_not_affected_by_required_mode(self):
        self.assertEqual(self.client.get("/portal/some-token/").status_code, 404)

    def test_regenerate_recovery_codes(self):
        secret, old = self.enable()
        self.client.force_login(self.user)
        TwoFactorDevice.objects.filter(user=self.user).update(last_counter=0)
        page = self.client.post(reverse("accounts:recovery_regenerate"), {"password": "pass12345", "code": current_code(secret)})
        self.assertContains(page, "Recovery codes")
        self.assertFalse(twofactor.use_recovery_code(self.user, old[0]))

    def test_admin_command_and_action_reset(self):
        self.enable()
        call_command("reset_2fa", "mgr1")
        self.assertFalse(twofactor.has_two_factor(self.user))

    def test_security_pages_need_login(self):
        for name in ("security", "two_factor_setup"):
            self.assertEqual(self.client.get(reverse(f"accounts:{name}")).status_code, 302)


class SessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("mgr1", password="pass12345", role="manager")

    def login(self):
        client = Client()
        client.post(reverse("accounts:login"), {"username": "mgr1", "password": "pass12345"})
        client.get(reverse("core:dashboard"))        # the middleware records the session on a request
        return client

    def test_sessions_are_listed_and_can_be_revoked(self):
        phone, laptop = self.login(), self.login()
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 2)
        page = laptop.get(reverse("accounts:security"))
        self.assertContains(page, "This device")
        other = UserSession.objects.exclude(session_key=laptop.session.session_key).get()
        laptop.post(reverse("accounts:session_revoke", args=[other.pk]))
        self.assertEqual(phone.get(reverse("core:dashboard")).status_code, 302)     # phone was signed out
        self.assertEqual(laptop.get(reverse("core:dashboard")).status_code, 200)

    def test_sign_out_all_others(self):
        a, b, c = self.login(), self.login(), self.login()
        a.post(reverse("accounts:session_revoke_others"))
        self.assertEqual(b.get(reverse("core:dashboard")).status_code, 302)
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 302)
        self.assertEqual(a.get(reverse("core:dashboard")).status_code, 200)

    def test_cannot_revoke_someone_elses_session(self):
        intruder = User.objects.create_user("op1", password="pass12345", role="operator")
        victim = self.login()
        row = UserSession.objects.get(user=self.user)
        attacker = Client(); attacker.force_login(intruder)
        self.assertEqual(attacker.post(reverse("accounts:session_revoke", args=[row.pk])).status_code, 404)
        self.assertEqual(victim.get(reverse("core:dashboard")).status_code, 200)

    def test_logout_removes_row_and_password_change_ends_others(self):
        a, b = self.login(), self.login()
        a.post(reverse("accounts:password_change"), {
            "old_password": "pass12345", "new_password1": "Another-Strong-1234", "new_password2": "Another-Strong-1234"})
        self.assertEqual(b.get(reverse("core:dashboard")).status_code, 302)
        self.assertEqual(a.get(reverse("core:dashboard")).status_code, 200)
        key = a.session.session_key
        self.assertTrue(UserSession.objects.filter(session_key=key).exists())
        a.post(reverse("accounts:logout"))
        self.assertFalse(UserSession.objects.filter(session_key=key).exists())
