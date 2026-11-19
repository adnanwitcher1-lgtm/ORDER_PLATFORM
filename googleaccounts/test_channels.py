from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from customers.models import Customer
from ytchannels.models import Channel

from . import oauth, services
from .tests import GoogleBaseTest
from .models import GoogleAccountConnection

S = GoogleAccountConnection.Status
SETTINGS = dict(TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), GOOGLE_OAUTH_CLIENT_ID="id",
                GOOGLE_OAUTH_CLIENT_SECRET="secret", GOOGLE_OAUTH_REDIRECT_URI="http://x/cb/")
UC = "UC" + "b" * 22


def preview(cid=UC, title="Brand One"):
    return {"id": cid, "title": title, "handle": "@brandone", "url": "https://www.youtube.com/@brandone",
            "thumbnail": "", "subscribers": 5400, "subscribers_hidden": False, "accuracy": "approximate",
            "views": 90000, "video_count": 33}


@override_settings(**SETTINGS)
class AccountChannelsTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("mgr", password="x", role=User.Role.MANAGER)
        self.operator = User.objects.create_user("op", password="x", role=User.Role.OPERATOR)
        self.customer = Customer.objects.create(name="Saeed")
        self.conn = GoogleAccountConnection(label="Main", google_email="a@example.com", google_sub="sub-1")
        self.conn.set_refresh_token("refresh")
        self.conn.save()
        self.client.force_login(self.manager)
        self.detail = reverse("google:detail", args=[self.conn.pk])

    def listing(self, items):
        return patch.multiple("ytchannels.resolver", fetch_mine=lambda token: items), \
            patch("googleaccounts.oauth.refresh_access_token", return_value="access")

    def test_detail_lists_the_channels_with_numbers(self):
        a, b = self.listing([preview()])
        with a, b:
            page = self.client.get(self.detail)
        self.assertContains(page, "Channels of this account")
        self.assertContains(page, "Brand One")
        self.assertContains(page, "5,400")
        self.assertContains(page, "Import as Channel")

    def test_import_creates_the_channel_and_links_the_account(self):
        a, b = self.listing([preview()])
        with a, b:
            response = self.client.post(reverse("google:import_channel", args=[self.conn.pk]),
                                        {"channel_id": UC, "customer": self.customer.pk})
        self.assertEqual(response.status_code, 302)
        channel = Channel.objects.get(youtube_channel_id=UC)
        self.assertEqual((channel.customer, channel.name, channel.google_connection, channel.created_by),
                         (self.customer, "Brand One", self.conn, self.manager))
        a, b = self.listing([preview()])
        with a, b:
            page = self.client.get(self.detail)
        self.assertContains(page, "Added")
        self.assertNotContains(page, "Import as Channel")

    def test_only_channels_of_the_account_can_be_imported(self):
        a, b = self.listing([preview()])
        with a, b:
            self.client.post(reverse("google:import_channel", args=[self.conn.pk]),
                             {"channel_id": "UC" + "z" * 22, "customer": self.customer.pk})
        self.assertFalse(Channel.objects.exists())

    def test_import_needs_a_customer_and_blocks_duplicates(self):
        a, b = self.listing([preview()])
        with a, b:
            self.client.post(reverse("google:import_channel", args=[self.conn.pk]), {"channel_id": UC})
            self.assertFalse(Channel.objects.exists())
            Channel.objects.create(customer=self.customer, name="Old", url="https://www.youtube.com/@old",
                                   youtube_channel_id=UC)
            self.client.post(reverse("google:import_channel", args=[self.conn.pk]),
                             {"channel_id": UC, "customer": self.customer.pk})
        self.assertEqual(Channel.objects.count(), 1)

    def test_import_is_for_managers_only(self):
        self.client.force_login(self.operator)
        a, b = self.listing([preview()])
        with a, b:
            page = self.client.get(self.detail)
            self.assertContains(page, "Brand One")
            self.assertNotContains(page, "Import as Channel")
            response = self.client.post(reverse("google:import_channel", args=[self.conn.pk]),
                                        {"channel_id": UC, "customer": self.customer.pk})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Channel.objects.exists())

    def test_expired_authorization_shows_a_message_not_a_crash(self):
        with patch("googleaccounts.oauth.refresh_access_token", side_effect=oauth.NeedsReauth("expired")):
            page = self.client.get(self.detail)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Reauthorize this account")

    def test_account_that_needs_reauth_makes_no_api_call(self):
        self.conn.status = S.NEEDS_REAUTH
        self.conn.save()
        with patch("googleaccounts.oauth.refresh_access_token", side_effect=AssertionError("no call")):
            self.assertEqual(self.client.get(self.detail).status_code, 200)

    def test_fetch_mine_reads_the_account_channels(self):
        from ytchannels import resolver

        class R:
            status_code = 200
            def json(self):
                return {"items": [{"id": UC, "snippet": {"title": "T", "customUrl": "@t"},
                                   "statistics": {"subscriberCount": "10", "viewCount": "1", "videoCount": "1"}}]}
        with patch("requests.get", return_value=R()) as get:
            items = resolver.fetch_mine("tok")
        self.assertEqual(items[0]["title"], "T")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(get.call_args.kwargs["params"]["mine"], "true")


def mine(cid, title):
    return [{"id": cid, "title": title}]


class BrandChannelTests(GoogleBaseTest):
    """One Google login, several brand channels: each channel is its own connection."""

    CH_A, CH_B = "UC" + "a" * 22, "UC" + "b" * 22

    def test_two_channels_of_one_login_make_two_connections(self):
        a, _ = self.connect(channels=mine(self.CH_A, "Brand A"))
        b, action = self.connect(channels=mine(self.CH_B, "Brand B"), pending={"label": ""})
        self.assertEqual(action, "Connected")
        self.assertNotEqual(a.pk, b.pk)
        self.assertEqual(a.google_sub, b.google_sub)
        self.assertEqual(b.label, "Brand B")
        self.assertEqual(GoogleAccountConnection.objects.count(), 2)

    def test_same_channel_again_updates_not_duplicates(self):
        self.connect(channels=mine(self.CH_A, "Brand A"))
        _, action = self.connect(channels=mine(self.CH_A, "Brand A"))
        self.assertEqual(action, "Reconnected")
        self.assertEqual(GoogleAccountConnection.objects.count(), 1)

    def test_old_record_without_channel_is_adopted(self):
        old, _ = self.connect()      # made before channels were tracked
        self.assertEqual(old.youtube_channel_id, "")
        again, action = self.connect(channels=mine(self.CH_A, "Brand A"))
        self.assertEqual((again.pk, action), (old.pk, "Reconnected"))
        again.refresh_from_db()
        self.assertEqual(again.youtube_channel_id, self.CH_A)

    def test_lookup_failure_does_not_create_a_duplicate(self):
        self.connect(channels=mine(self.CH_A, "Brand A"))
        _, action = self.connect(channels=[])
        self.assertEqual(action, "Reconnected")
        self.assertEqual(GoogleAccountConnection.objects.count(), 1)

    def test_reauth_with_a_different_channel_is_rejected(self):
        a, _ = self.connect(channels=mine(self.CH_A, "Brand A"))
        with self.assertRaises(services.ServiceError):
            self.connect(channels=mine(self.CH_B, "Brand B"), pending={"reauth_pk": a.pk})
        _, action = self.connect(channels=mine(self.CH_A, "Brand A"), pending={"reauth_pk": a.pk})
        self.assertEqual(action, "Reauthorized")
