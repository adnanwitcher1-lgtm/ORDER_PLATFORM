"""Owner-only numbers from the YouTube Analytics API (needs the account's yt-analytics.readonly permission).

The data is NOT real-time: YouTube finishes a day 1-3 days later, so the report ends 3 days ago.
"""
from datetime import timedelta

import requests
from django.utils import timezone

from googleaccounts import oauth

from .insights import InsightError

REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
METRICS = "views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost"
DELAY_DAYS = 3


def _report(token, channel_id, start, end, dimensions=""):
    params = {"ids": f"channel=={channel_id}", "startDate": start, "endDate": end, "metrics": METRICS}
    if dimensions:
        params.update(dimensions=dimensions, sort=dimensions)
    try:
        response = requests.get(REPORTS_URL, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    except requests.RequestException:
        raise InsightError("Could not reach YouTube Analytics. Please try again.")
    if response.status_code in (401, 403):
        raise InsightError(
            "This Google account cannot read Analytics for this channel. Reauthorize the account (allow the "
            "Analytics permission) and make sure you choose this channel on Google's screen.", 403)
    if response.status_code != 200:
        raise InsightError(f"YouTube Analytics returned an error (HTTP {response.status_code}).", response.status_code)
    return response.json()


def _totals(payload):
    headers = [h["name"] for h in payload.get("columnHeaders", [])]
    rows = payload.get("rows") or [[0] * len(headers)]
    return dict(zip(headers, rows[0]))


def summarize(totals):
    minutes = int(totals.get("estimatedMinutesWatched") or 0)
    gained = int(totals.get("subscribersGained") or 0)
    lost = int(totals.get("subscribersLost") or 0)
    return {
        "views": int(totals.get("views") or 0),
        "watch_minutes": minutes,
        "watch_hours": round(minutes / 60, 1),
        "avg_view_seconds": int(totals.get("averageViewDuration") or 0),
        "subs_gained": gained,
        "subs_lost": lost,
        "subs_net": gained - lost,
    }


def fetch(channel, days=28, since=None, today=None):
    """Totals for the last `days` days (ending DELAY_DAYS ago), a daily series, and optionally
    the exact net subscriber change since a date (for an order)."""
    connection = channel.google_connection
    if connection is None or not connection.has_token:
        raise InsightError("This channel is not linked to a connected Google account.")
    if not connection.has_analytics:
        raise InsightError("This Google account was connected before the Analytics permission existed. Reauthorize it first.", 403)
    try:
        token = oauth.refresh_access_token(connection.get_refresh_token())
    except oauth.NeedsReauth:
        raise InsightError("Google authorization expired. Reauthorize the account.", 403)
    except oauth.OAuthError as exc:
        raise InsightError(str(exc))
    cid = channel.youtube_channel_id
    if not cid:
        raise InsightError("This channel has no Channel ID.")

    today = today or timezone.localdate()
    end = today - timedelta(days=DELAY_DAYS)
    start = end - timedelta(days=days - 1)
    total = _report(token, cid, start.isoformat(), end.isoformat())
    daily = _report(token, cid, start.isoformat(), end.isoformat(), "day")
    headers = [h["name"] for h in daily.get("columnHeaders", [])]
    series = [dict(zip(headers, row)) for row in daily.get("rows", [])]
    data = {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "totals": summarize(_totals(total)),
        "daily": [{"day": r.get("day"), "views": r.get("views", 0),
                   "minutes": r.get("estimatedMinutesWatched", 0),
                   "net": (r.get("subscribersGained", 0) - r.get("subscribersLost", 0))} for r in series],
        "note": "Analytics data is 1-3 days late, so the last days are not included.",
    }
    if since and since <= end:
        data["since"] = {"from": since.isoformat(), "to": end.isoformat(),
                         **summarize(_totals(_report(token, cid, since.isoformat(), end.isoformat())))}
    return data
