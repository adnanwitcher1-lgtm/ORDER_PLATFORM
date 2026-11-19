from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from customers.models import Customer

from . import insights
from .models import Channel, ChannelInsight


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


CHANNEL = {"items": [{
    "id": "UC123",
    "snippet": {"title": "Cook Show", "customUrl": "@cookshow", "country": "PK",
                "publishedAt": "2020-05-01T10:00:00Z", "description": "Recipes every week",
                "thumbnails": {"default": {"url": "https://img.example/t.jpg"}}},
    "statistics": {"subscriberCount": "12300", "viewCount": "4500000", "videoCount": "210"},
    "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
    "brandingSettings": {"channel": {"keywords": "cooking food"}},
}]}
PLAYLIST = {"items": [{"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}},
                      {"contentDetails": {"videoId": "v3"}}]}
VIDEOS = {"items": [
    {"id": "v1", "snippet": {"title": "Biryani", "publishedAt": "2026-10-01T00:00:00Z", "tags": ["Biryani", "rice"]},
     "statistics": {"viewCount": "1000", "likeCount": "100", "commentCount": "10"},
     "contentDetails": {"duration": "PT10M30S"}},
    {"id": "v2", "snippet": {"title": "Quick snack", "publishedAt": "2026-10-08T00:00:00Z", "tags": ["rice"]},
     "statistics": {"viewCount": "3000", "likeCount": "200", "commentCount": "20"},
     "contentDetails": {"duration": "PT45S"}},
    {"id": "v3", "snippet": {"title": "Karahi", "publishedAt": "2026-10-15T00:00:00Z"},
     "statistics": {"viewCount": "2000"},
     "contentDetails": {"duration": "PT1H2M3S"}},
]}


def fake_get(url, params=None, timeout=None):
    if url.endswith("/channels"):
        return FakeResponse(CHANNEL)
    if url.endswith("/playlistItems"):
        return FakeResponse(PLAYLIST)
    if url.endswith("/videos"):
        return FakeResponse(VIDEOS)
    return FakeResponse({}, 404)


@override_settings(YOUTUBE_API_KEY="yt-secret")
class FetchTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Saeed")
        self.channel = Channel.objects.create(customer=self.customer, name="Cook", url="https://www.youtube.com/@cookshow")

    def test_parse_duration(self):
        self.assertEqual(insights.parse_duration("PT10M30S"), 630)
        self.assertEqual(insights.parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(insights.parse_duration("P1DT1H"), 90000)
        self.assertEqual(insights.parse_duration("bad"), 0)

    def test_fetch_collects_channel_and_video_numbers(self):
        with patch("requests.get", side_effect=fake_get):
            data = insights.fetch(self.channel)
        info, st = data["channel"], data["stats"]
        self.assertEqual(info["subscribers"], 12300)
        self.assertEqual(info["views"], 4500000)
        self.assertEqual(info["video_count"], 210)
        self.assertEqual(st["count"], 3)
        self.assertEqual(st["avg_views"], 2000)
        self.assertEqual(st["median_views"], 2000)
        self.assertEqual(st["short_count"], 1)
        self.assertEqual(st["long_count"], 2)
        self.assertEqual(st["top"][0]["title"], "Quick snack")
        self.assertEqual(st["uploads_per_week"], 1.0)
        self.assertIn("rice", st["top_tags"])
        self.assertEqual([v["title"] for v in data["videos"]], ["Biryani", "Quick snack", "Karahi"])

    def test_hidden_subscribers(self):
        hidden = {"items": [{**CHANNEL["items"][0],
                             "statistics": {"hiddenSubscriberCount": True, "viewCount": "5", "videoCount": "0"},
                             "contentDetails": {}}]}
        with patch("requests.get", return_value=FakeResponse(hidden)):
            data = insights.fetch(self.channel)
        self.assertTrue(data["channel"]["subscribers_hidden"])
        self.assertIsNone(data["channel"]["subscribers"])
        self.assertEqual(data["stats"]["count"], 0)

    def test_errors_never_contain_the_key(self):
        quota = FakeResponse({"error": {"errors": [{"reason": "quotaExceeded"}]}}, 403)
        with patch("requests.get", return_value=quota), self.assertRaises(insights.InsightError) as ctx:
            insights.fetch(self.channel)
        self.assertIn("quota", str(ctx.exception))
        with patch("requests.get", side_effect=insights.requests.ConnectionError("https://x?key=yt-secret")):
            with self.assertRaises(insights.InsightError) as ctx:
                insights.fetch(self.channel)
        self.assertNotIn("yt-secret", str(ctx.exception))

    def test_no_key_and_unknown_channel(self):
        with override_settings(YOUTUBE_API_KEY=""), self.assertRaises(insights.InsightError):
            insights.fetch(self.channel)
        with patch("requests.get", return_value=FakeResponse({"items": []})), self.assertRaises(insights.InsightError):
            insights.fetch(self.channel)

    def test_channel_without_uploads_is_ok(self):
        def route(url, params=None, timeout=None):
            return FakeResponse(CHANNEL) if url.endswith("/channels") else FakeResponse({}, 404)
        with patch("requests.get", side_effect=route):
            data = insights.fetch(self.channel)
        self.assertEqual(data["videos"], [])

    def test_rule_summary_languages(self):
        with patch("requests.get", side_effect=fake_get):
            data = insights.fetch(self.channel)
        en = insights.rule_summary(data, "en")
        ur = insights.rule_summary(data, "ur")
        self.assertIn("12,300 subscribers", en)
        self.assertIn("subscribers", ur)
        self.assertNotEqual(en, ur)


@override_settings(YOUTUBE_API_KEY="yt-secret", ANTHROPIC_API_KEY="", GEMINI_API_KEY="")
class ViewTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("mgr", password="x", role=User.Role.MANAGER)
        self.auditor = User.objects.create_user("aud", password="x", role=User.Role.AUDITOR)
        self.customer = Customer.objects.create(name="Saeed")
        self.channel = Channel.objects.create(customer=self.customer, name="Cook", url="https://www.youtube.com/@cookshow")
        self.client.force_login(self.manager)

    def refresh(self):
        with patch("requests.get", side_effect=fake_get):
            return self.client.post(reverse("channels:insights_refresh", args=[self.channel.pk]))

    def test_detail_page_without_and_with_data(self):
        url = reverse("channels:detail", args=[self.channel.pk])
        self.assertContains(self.client.get(url), "No details yet")
        self.refresh()
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Cook Show")
        self.assertContains(page, "12.3K")
        self.assertContains(page, "Watch time")
        self.assertContains(page, "Not available")
        self.assertContains(page, "chartVideos")

    def test_refresh_saves_and_is_throttled(self):
        self.assertEqual(self.refresh().status_code, 302)
        insight = ChannelInsight.objects.get(channel=self.channel)
        first = insight.fetched_at
        self.assertEqual(insight.data["channel"]["subscribers"], 12300)
        with patch("requests.get", side_effect=AssertionError("must not call YouTube")):
            self.client.post(reverse("channels:insights_refresh", args=[self.channel.pk]))
        insight.refresh_from_db()
        self.assertEqual(insight.fetched_at, first)

    def test_refresh_error_is_reported(self):
        with patch("requests.get", return_value=FakeResponse({}, 500)):
            response = self.client.post(reverse("channels:insights_refresh", args=[self.channel.pk]), follow=True)
        self.assertContains(response, "YouTube API error")
        self.assertFalse(ChannelInsight.objects.exists())

    def test_auditor_can_view_but_not_refresh(self):
        self.refresh()
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.get(reverse("channels:detail", args=[self.channel.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("channels:insights_refresh", args=[self.channel.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("channels:insights_ai", args=[self.channel.pk])).status_code, 403)

    def test_login_and_post_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("channels:detail", args=[self.channel.pk])).status_code, 302)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("channels:insights_refresh", args=[self.channel.pk])).status_code, 405)

    def test_summary_needs_data_first(self):
        response = self.client.post(reverse("channels:insights_ai", args=[self.channel.pk]), follow=True)
        self.assertContains(response, "Fetch the channel details first")

    def test_rule_summary_without_ai_key(self):
        self.refresh()
        self.client.post(reverse("channels:insights_ai", args=[self.channel.pk]), {"lang": "ur"})
        insight = ChannelInsight.objects.get(channel=self.channel)
        self.assertEqual(insight.ai_source, "rules")
        self.assertEqual(insight.ai_lang, "ur")
        self.assertIn("subscribers", insight.ai_summary)

    def test_ai_summary_uses_ai_and_sends_no_secrets(self):
        self.refresh()
        with override_settings(GEMINI_API_KEY="g-key"), \
                patch("core.ai.ask", return_value="A cooking channel.") as ask:
            self.client.post(reverse("channels:insights_ai", args=[self.channel.pk]), {"lang": "en"})
        insight = ChannelInsight.objects.get(channel=self.channel)
        self.assertEqual((insight.ai_source, insight.ai_summary), ("ai", "A cooking channel."))
        prompt = ask.call_args.args[1]
        self.assertIn("Biryani", prompt)
        self.assertNotIn("yt-secret", prompt)
        self.assertNotIn("g-key", prompt)

    def test_channel_list_links_to_detail(self):
        page = self.client.get(reverse("channels:list"))
        self.assertContains(page, reverse("channels:detail", args=[self.channel.pk]))
