from datetime import date
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from customers.models import Customer
from googleaccounts.models import GoogleAccountConnection

from . import analytics
from .insights import InsightError
from .models import Channel, ChannelAnalytics

SETTINGS = dict(TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), GOOGLE_OAUTH_CLIENT_ID="i",
                GOOGLE_OAUTH_CLIENT_SECRET="s", GOOGLE_OAUTH_REDIRECT_URI="http://x/cb/")
UC = "UC" + "c" * 22
HEAD = [{"name": n} for n in ("views", "estimatedMinutesWatched", "averageViewDuration", "subscribersGained", "subscribersLost")]


class R:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


def fake_get(url, params=None, headers=None, timeout=None):
    if "day" in (params.get("dimensions") or ""):
        return R({"columnHeaders": [{"name": "day"}] + HEAD, "rows": [["2026-11-01", 10, 60, 30, 30, 5]]})
    return R({"columnHeaders": HEAD, "rows": [[1000, 3000, 90, 50, 8]]})


@override_settings(**SETTINGS)
class AnalyticsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mgr", password="x", role=User.Role.MANAGER)
        self.customer = Customer.objects.create(name="C")
        self.conn = GoogleAccountConnection(label="L", google_email="a@b.c", google_sub="s1",
                                            scopes="openid youtube.readonly yt-analytics.readonly")
        self.conn.set_refresh_token("r")
        self.conn.save()
        self.channel = Channel.objects.create(customer=self.customer, name="Ch", url="https://www.youtube.com/@c",
                                              youtube_channel_id=UC, google_connection=self.conn)
        self.client.force_login(self.user)

    def run_fetch(self, **kw):
        with patch("googleaccounts.oauth.refresh_access_token", return_value="tok"), \
                patch("requests.get", side_effect=fake_get):
            return analytics.fetch(self.channel, today=date(2026, 11, 19), **kw)

    def test_totals_and_exact_net(self):
        data = self.run_fetch(since=date(2026, 11, 5))
        self.assertEqual(data["totals"]["watch_hours"], 50.0)
        self.assertEqual(data["totals"]["subs_net"], 42)
        self.assertEqual(data["period"]["end"], "2026-11-16")     # 3 days of delay
        self.assertEqual(data["since"]["subs_net"], 42)
        self.assertEqual(data["daily"][0]["net"], 25)

    def test_old_connection_without_scope_is_told_to_reauthorize(self):
        self.conn.scopes = "openid youtube.readonly"
        self.conn.save()
        with self.assertRaises(InsightError):
            self.run_fetch()

    def test_unlinked_channel(self):
        self.channel.google_connection = None
        with self.assertRaises(InsightError):
            analytics.fetch(self.channel)

    def test_403_is_a_clear_message(self):
        with patch("googleaccounts.oauth.refresh_access_token", return_value="tok"), \
                patch("requests.get", return_value=R({}, 403)):
            with self.assertRaises(InsightError) as ctx:
                analytics.fetch(self.channel)
        self.assertNotIn("tok", str(ctx.exception))

    def test_view_saves_and_page_shows(self):
        with patch("googleaccounts.oauth.refresh_access_token", return_value="tok"), \
                patch("requests.get", side_effect=fake_get):
            r = self.client.post(reverse("channels:analytics_refresh", args=[self.channel.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ChannelAnalytics.objects.filter(channel=self.channel).exists())
        page = self.client.get(reverse("channels:detail", args=[self.channel.pk]))
        self.assertContains(page, "50.0 h")
        self.assertContains(page, "not real-time")

    def test_auditor_cannot_refresh(self):
        self.client.force_login(User.objects.create_user("aud", password="x", role=User.Role.AUDITOR))
        self.assertEqual(self.client.post(reverse("channels:analytics_refresh", args=[self.channel.pk])).status_code, 403)

    def test_unlinked_channel_page_explains(self):
        self.channel.google_connection = None
        self.channel.save()
        page = self.client.get(reverse("channels:detail", args=[self.channel.pk]))
        self.assertContains(page, "visible only to the channel owner")
