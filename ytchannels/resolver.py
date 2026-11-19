"""Turn a YouTube link, @handle or channel ID into the channel's details (public data, API key)."""
import re
from urllib.parse import parse_qs, urlparse

import requests

from .insights import BASE_URL, InsightError, _get, _int

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
ID_RE = re.compile(r"^UC[\w-]{20,30}$")
NOT_UNDERSTOOD = (
    "This is not a YouTube channel or video link that can be read. Use a link such as "
    "youtube.com/@name, youtube.com/channel/UC... or a video link."
)


def parse_reference(text):
    """Returns (kind, value) with kind in id, handle, user, video; or None."""
    text = (text or "").strip()
    if not text:
        return None
    if ID_RE.match(text):
        return "id", text
    if text.startswith("@") and "/" not in text:
        return "handle", text
    parsed = urlparse(text if "://" in text else "https://" + text)
    host = parsed.netloc.lower()
    if host not in YOUTUBE_HOSTS:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if host == "youtu.be":
        return ("video", parts[0]) if parts else None
    if not parts:
        return None
    first = parts[0]
    if first.startswith("@"):
        return "handle", first
    if first == "channel" and len(parts) > 1 and ID_RE.match(parts[1]):
        return "id", parts[1]
    if first == "user" and len(parts) > 1:
        return "user", parts[1]
    if first in ("shorts", "live", "embed") and len(parts) > 1:
        return "video", parts[1]
    if first == "watch":
        vid = parse_qs(parsed.query).get("v", [""])[0]
        return ("video", vid) if vid else None
    return None


def preview_from_item(item):
    sn, st = item.get("snippet", {}), item.get("statistics", {})
    hidden = bool(st.get("hiddenSubscriberCount"))
    subs = None if hidden else _int(st.get("subscriberCount"))
    handle = sn.get("customUrl", "")
    if handle and not handle.startswith("@"):
        handle = ""     # old-style custom URLs are not handles
    cid = item.get("id", "")
    return {
        "id": cid,
        "title": sn.get("title", ""),
        "handle": handle,
        "url": f"https://www.youtube.com/{handle}" if handle else f"https://www.youtube.com/channel/{cid}",
        "thumbnail": sn.get("thumbnails", {}).get("default", {}).get("url", ""),
        "subscribers": subs,
        "subscribers_hidden": hidden,
        "accuracy": "" if subs is None else ("exact" if subs < 1000 else "approximate"),
        "views": _int(st.get("viewCount")),
        "video_count": _int(st.get("videoCount")),
    }


def describe(text):
    """Channel details for a link, @handle or channel ID. Raises InsightError."""
    ref = parse_reference(text)
    if ref is None:
        raise InsightError(NOT_UNDERSTOOD)
    kind, value = ref
    if kind == "video":
        data = _get("videos", {"part": "snippet", "id": value})
        items = data.get("items") or []
        if not items:
            raise InsightError("Video not found on YouTube.")
        kind, value = "id", items[0].get("snippet", {}).get("channelId", "")
        if not value:
            raise InsightError("The channel of this video could not be found.")
    params = {"id": value} if kind == "id" else {"forHandle": value} if kind == "handle" else {"forUsername": value}
    data = _get("channels", {"part": "snippet,statistics", **params})
    items = data.get("items") or []
    if not items:
        raise InsightError("Channel not found on YouTube.")
    return preview_from_item(items[0])


def fetch_mine(access_token):
    """The channels of the signed-in Google account (read-only OAuth token)."""
    try:
        response = requests.get(
            f"{BASE_URL}/channels",
            params={"part": "snippet,statistics", "mine": "true", "maxResults": 50},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
    except requests.RequestException:
        raise InsightError("Network error: could not reach YouTube.")
    if response.status_code in (401, 403):
        raise InsightError(
            f"YouTube did not allow reading the channels (HTTP {response.status_code}). "
            "Reauthorize the Google account."
        )
    if response.status_code != 200:
        raise InsightError(f"YouTube API error (HTTP {response.status_code}).")
    return [preview_from_item(i) for i in response.json().get("items") or []]
