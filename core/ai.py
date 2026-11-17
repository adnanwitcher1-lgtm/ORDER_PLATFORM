"""Optional AI helper (Anthropic Claude or Google Gemini).

Everything in the platform also works without a key (rule based). When a key is
set, the AI only receives small summary facts (counts, statuses, order numbers),
never tokens, notes, passwords or Google account data.
"""
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

API_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
API_VERSION = "2023-06-01"
TIMEOUT_SECONDS = 20


class AIError(Exception):
    """Error that is safe to show to the user."""


def provider():
    """"anthropic", "gemini" or None. AI_PROVIDER can force one; blank means: whichever key is set."""
    chosen = (getattr(settings, "AI_PROVIDER", "") or "").strip().lower()
    has_claude = bool(getattr(settings, "ANTHROPIC_API_KEY", ""))
    has_gemini = bool(getattr(settings, "GEMINI_API_KEY", ""))
    if chosen == "gemini":
        return "gemini" if has_gemini else None
    if chosen == "anthropic":
        return "anthropic" if has_claude else None
    if has_claude:
        return "anthropic"
    return "gemini" if has_gemini else None


def model_name():
    if provider() == "gemini":
        return getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    return settings.AI_MODEL


def is_configured():
    return provider() is not None


def within_limit(user):
    """Per-user hourly budget so a stuck button cannot run up costs."""
    limit = getattr(settings, "AI_HOURLY_LIMIT", 30)
    key = f"ai-calls:{user.pk}:{timezone.now():%Y%m%d%H}"
    count = cache.get(key, 0)
    if count >= limit:
        return False
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, 3600)
    return True


def ask(system, prompt, max_tokens=300):
    """Return the model's text answer or raise AIError."""
    which = provider()
    if which is None:
        raise AIError("AI is not configured.")
    if which == "gemini":
        return _ask_gemini(system, prompt, max_tokens)
    return _ask_claude(system, prompt, max_tokens)


def _post(url, headers, body):
    import requests

    try:
        response = requests.post(url, headers=headers, json=body, timeout=TIMEOUT_SECONDS)
    except requests.RequestException:
        raise AIError("Could not reach the AI service.")
    if response.status_code == 429:
        raise AIError("The AI service limit was reached (429). Try again later.")
    if response.status_code != 200:
        # The body can echo request details, so only the status code is reported.
        raise AIError(f"The AI service returned an error ({response.status_code}).")
    try:
        return response.json()
    except ValueError:
        raise AIError("The AI service returned an unreadable answer.")


def _finish(text):
    text = (text or "").strip()
    if not text:
        raise AIError("The AI service returned an empty answer.")
    return text


def _ask_claude(system, prompt, max_tokens):
    data = _post(
        API_URL,
        {
            "x-api-key": settings.ANTHROPIC_API_KEY,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
        {
            "model": settings.AI_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    try:
        parts = data.get("content", [])
        return _finish("".join(p.get("text", "") for p in parts if p.get("type") == "text"))
    except AttributeError:
        raise AIError("The AI service returned an unreadable answer.")


def _ask_gemini(system, prompt, max_tokens):
    model = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
    config = {"maxOutputTokens": max_tokens}
    if "flash" in model:
        # Flash "thinking" would use up the small answer budget, so it is switched off.
        config["thinkingConfig"] = {"thinkingBudget": 0}
    data = _post(
        GEMINI_URL.format(model=model),
        {"x-goog-api-key": settings.GEMINI_API_KEY, "content-type": "application/json"},
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": config,
        },
    )
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return _finish("".join(p.get("text", "") for p in parts))
    except (KeyError, IndexError, TypeError, AttributeError):
        raise AIError("The AI service returned an empty or blocked answer.")
