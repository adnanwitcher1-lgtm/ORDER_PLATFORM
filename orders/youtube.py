from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import requests
from django.conf import settings

from .models import MetricObservation

A = MetricObservation.Accuracy
API_URL = "https://www.googleapis.com/youtube/v3/channels"
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}
BATCH_SIZE = 50
NOT_CONFIGURED_MSG = "YouTube API key is not set (YOUTUBE_API_KEY in .env)."


@dataclass(frozen=True)
class FetchResult:
    count: Optional[int]
    accuracy: str
    api_status: str   # ok, hidden, not_found, quota, error, not_configured, unsupported_url
    message: str
    channel_id: str = ""


def _failure(status, message):
    return FetchResult(None, A.UNAVAILABLE, status, message)


def resolve_lookup(channel):
    """Convert the channel to an API lookup parameter. None if not possible."""
    if channel.youtube_channel_id:
        return {"id": channel.youtube_channel_id}
    parts = urlparse(channel.url).path.strip("/").split("/")
    if parts and parts[0].startswith("@"):
        return {"forHandle": parts[0]}
    if len(parts) > 1 and parts[0] == "channel":
        return {"id": parts[1]}
    if len(parts) > 1 and parts[0] == "user":
        return {"forUsername": parts[1]}
    return None


def _error_reason(response):
    try:
        return response.json()["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def _call_api(lookup):
    """Returns (items, error). If there is an error, items is None."""
    key = getattr(settings, "YOUTUBE_API_KEY", "")
    if not key:
        return None, _failure("not_configured", NOT_CONFIGURED_MSG)
    try:
        response = requests.get(
            API_URL, params={"part": "statistics", "key": key, **lookup}, timeout=10
        )
    except requests.RequestException:
        # The exception text contains the URL (and key), so it is not included in the message.
        return None, _failure("error", "Network error: could not reach YouTube.")

    if response.status_code != 200:
        if _error_reason(response) in QUOTA_REASONS:
            return None, _failure("quota", "The YouTube API daily quota is exhausted.")
        return None, _failure("error", f"YouTube API error (HTTP {response.status_code}).")
    return response.json().get("items") or [], None


def _result_from_item(item):
    stats = item.get("statistics", {})
    channel_id = item.get("id", "")

    if stats.get("hiddenSubscriberCount"):
        return FetchResult(None, A.UNAVAILABLE, "hidden",
                           "The channel has hidden its subscriber count.", channel_id)
    raw = stats.get("subscriberCount")
    if raw is None:
        return FetchResult(None, A.UNAVAILABLE, "error",
                           "The API did not return a subscriber count.", channel_id)

    count = int(raw)
    if count < 1000:
        return FetchResult(count, A.EXACT, "ok", "", channel_id)
    return FetchResult(count, A.APPROXIMATE, "ok", "The YouTube public count is rounded.", channel_id)


def fetch_subscriber_count(channel):
    if not getattr(settings, "YOUTUBE_API_KEY", ""):
        return _failure("not_configured", NOT_CONFIGURED_MSG)

    lookup = resolve_lookup(channel)
    if lookup is None:
        return _failure(
            "unsupported_url",
            "The channel cannot be identified from this URL. Edit the channel and enter the YouTube channel ID (UC...).",
        )

    items, error = _call_api(lookup)
    if error:
        return error
    if not items:
        return _failure("not_found", "Channel not found on YouTube.")
    return _result_from_item(items[0])


def fetch_many(channel_ids):
    """Up to 50 channel IDs per request. Returns {channel_id: FetchResult}."""
    ids = list(dict.fromkeys(cid for cid in channel_ids if cid))
    results = {}
    for start in range(0, len(ids), BATCH_SIZE):
        chunk = ids[start:start + BATCH_SIZE]
        items, error = _call_api({"id": ",".join(chunk), "maxResults": BATCH_SIZE})
        if error:
            results.update({cid: error for cid in chunk})
            continue
        found = {item.get("id"): _result_from_item(item) for item in items}
        for cid in chunk:
            results[cid] = found.get(cid) or _failure("not_found", "Channel not found on YouTube.")
    return results