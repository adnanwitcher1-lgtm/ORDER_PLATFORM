from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from customers.models import Customer

from . import resolver
from .forms import ChannelForm
from .insights import InsightError
from .models import Channel

UC = "UC" + "a" * 22


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


def channel_item(cid=UC, title="Cook Show", handle="@cookshow", subs="12300"):
    return {"id": cid,
            "snippet": {"title": title, "customUrl": handle, "thumbnails": {"default": {"url": "https://i/t.jpg"}}},
            "statistics": {"subscriberCount": subs, "viewCount": "5000", "videoCount": "40"}}


class ParseTests(SimpleTestCase):
    def test_references(self):
        cases = {
            "https://www.youtube.com/@cookshow": ("handle", "@cookshow"),
            "youtube.com/@cookshow/videos": ("handle", "@cookshow"),
            "@cookshow": ("handle", "@cookshow"),
            f"https://www.youtube.com/channel/{UC}": ("id", UC),
            UC: ("id", UC),
            "https://www.youtube.com/user/oldname": ("user", "oldname"),
            "https://www.youtube.com/watch?v=abc123": ("video", "abc123"),
            "https://youtu.be/abc123?t=5": ("video", "abc123"),
            "https://www.youtube.com/shorts/xyz": ("video", "xyz"),
        }
        for text, expected in cases.items():
            self.assertEqual(resolver.parse_reference(text), expected, text)

    def test_not_understood(self):
        for text in ("", "https://example.com/@x", "https://www.youtube.com/", "hello world", "https://www.youtube.com/c/oldcustom"):
            self.assertIsNone(resolver.parse_reference(text), text)


@override_settings(YOUTUBE_API_KEY="yt-secret")
class DescribeTests(SimpleTestCase):
    def test_handle_uses_forhandle_and_reads_subscribers(self):
        with patch("requests.get", return_value=FakeResponse({"items": [channel_item()]})) as get:
            info = resolver.describe("https://www.youtube.com/@cookshow")
        self.assertEqual(get.call_args.kwargs["params"]["forHandle"], "@cookshow")
        self.assertEqual(info["id"], UC)
        self.assertEqual(info["title"], "Cook Show")
        self.assertEqual(info["subscribers"], 12300)
        self.assertEqual(info["accuracy"], "approximate")
        self.assertEqual(info["url"], "https://www.youtube.com/@cookshow")

    def test_small_channel_is_exact(self):
        with patch("requests.get", return_value=FakeResponse({"items": [channel_item(subs="250")]})):
            self.assertEqual(resolver.describe(UC)["accuracy"], "exact")

    def test_video_link_finds_the_channel(self):
        def route(url, params=None, timeout=None, headers=None):
            if url.endswith("/videos"):
                return FakeResponse({"items": [{"snippet": {"channelId": UC}}]})
            return FakeResponse({"items": [channel_item()]})
        with patch("requests.get", side_effect=route):
            self.assertEqual(resolver.describe("https://youtu.be/abc123")["id"], UC)

    def test_errors(self):
        with self.assertRaises(InsightError):
            resolver.describe("https://example.com/x")
        with patch("requests.get", return_value=FakeResponse({"items": []})), self.assertRaises(InsightError):
            resolver.describe("@nobody")

    def test_hidden_subscribers_and_old_custom_url(self):
        item = channel_item(handle="CookShowOld")
        item["statistics"] = {"hiddenSubscriberCount": True, "viewCount": "1", "videoCount": "1"}
        with patch("requests.get", return_value=FakeResponse({"items": [item]})):
            info = resolver.describe(UC)
        self.assertTrue(info["subscribers_hidden"])
        self.assertIsNone(info["subscribers"])
        self.assertEqual(info["url"], f"https://www.youtube.com/channel/{UC}")


@override_settings(YOUTUBE_API_KEY="yt-secret", AUTO_LOOKUP=True)
class FormAutofillTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Saeed")

    def form(self, **data):
        base = {"customer": self.customer.pk, "url": "https://www.youtube.com/@cookshow",
                "name": "", "youtube_channel_id": "", "authorization_notes": ""}
        return ChannelForm({**base, **data})

    def test_name_and_id_are_filled_from_youtube(self):
        with patch("requests.get", return_value=FakeResponse({"items": [channel_item()]})):
            form = self.form()
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["name"], "Cook Show")
        self.assertEqual(form.cleaned_data["youtube_channel_id"], UC)

    def test_typed_values_are_kept(self):
        with patch("requests.get", return_value=FakeResponse({"items": [channel_item()]})):
            form = self.form(name="My name")
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["name"], "My name")

    def test_video_link_is_replaced_by_the_channel_link(self):
        def route(url, params=None, timeout=None, headers=None):
            if url.endswith("/videos"):
                return FakeResponse({"items": [{"snippet": {"channelId": UC}}]})
            return FakeResponse({"items": [channel_item()]})
        with patch("requests.get", side_effect=route):
            form = self.form(url="https://www.youtube.com/watch?v=abc123")
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["url"], "https://www.youtube.com/@cookshow")

    def test_same_channel_cannot_be_added_twice(self):
        Channel.objects.create(customer=self.customer, name="Existing", url="https://www.youtube.com/@x",
                               youtube_channel_id=UC)
        with patch("requests.get", return_value=FakeResponse({"items": [channel_item()]})):
            form = self.form()
            self.assertFalse(form.is_valid())
        self.assertIn("youtube_channel_id", form.errors)

    def test_youtube_down_means_manual_entry_not_a_crash(self):
        with patch("requests.get", return_value=FakeResponse({}, 500)):
            form = self.form()
            self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        with patch("requests.get", return_value=FakeResponse({}, 500)):
            self.assertTrue(self.form(name="Typed").is_valid())


@override_settings(YOUTUBE_API_KEY="yt-secret")
class EndpointTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("mgr", password="x", role=User.Role.MANAGER)
        self.auditor = User.objects.create_user("aud", password="x", role=User.Role.AUDITOR)
        self.customer = Customer.objects.create(name="Saeed")
        self.client.force_login(self.manager)

    def test_lookup_ok_and_existing(self):
        with patch("requests.get", return_value=FakeResponse({"items": [channel_item()]})):
            data = self.client.get(reverse("channels:lookup"), {"q": "@cookshow"}).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["channel"]["title"], "Cook Show")
        self.assertEqual(data["existing"], "")
        Channel.objects.create(customer=self.customer, name="Already", url="https://www.youtube.com/@a",
                               youtube_channel_id=UC)
        with patch("requests.get", return_value=FakeResponse({"items": [channel_item()]})):
            data = self.client.get(reverse("channels:lookup"), {"q": "@cookshow"}).json()
        self.assertEqual(data["existing"], "Already")

    def test_lookup_error_is_json_not_a_crash(self):
        data = self.client.get(reverse("channels:lookup"), {"q": "nonsense"}).json()
        self.assertFalse(data["ok"])
        self.assertNotIn("yt-secret", data["error"])

    def test_lookup_needs_the_right_role(self):
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.get(reverse("channels:lookup"), {"q": "@x"}).status_code, 403)

    def test_add_page_has_the_lookup_form(self):
        page = self.client.get(reverse("channels:create"))
        self.assertContains(page, "lookupBtn")
        self.assertContains(page, "channel_form.js")

    def test_current_count_for_the_order_baseline(self):
        channel = Channel.objects.create(customer=self.customer, name="C", url="https://www.youtube.com/@c",
                                         youtube_channel_id=UC)
        with patch("requests.get", return_value=FakeResponse({"items": [channel_item(subs="20400")]})):
            data = self.client.get(reverse("channels:current_count", args=[channel.pk])).json()
        self.assertEqual((data["ok"], data["count"], data["accuracy"]), (True, 20400, "approximate"))
        with patch("requests.get", return_value=FakeResponse({}, 500)):
            data = self.client.get(reverse("channels:current_count", args=[channel.pk])).json()
        self.assertFalse(data["ok"])
        self.assertIsNone(data["count"])

    def test_order_form_page_wires_the_baseline_helper(self):
        page = self.client.get(reverse("orders:create"))
        self.assertContains(page, "data-count-url")
        self.assertContains(page, "baselineHint")
