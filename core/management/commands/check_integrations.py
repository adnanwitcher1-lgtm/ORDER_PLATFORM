from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from core import ai
from reports import whatsapp


def mask(value):
    return "set (hidden)" if value else "NOT set"


class Command(BaseCommand):
    help = (
        "Shows which integrations are configured and can run live tests with YOUR keys: "
        "--email ADDRESS, --ai, --whatsapp NUMBER, --youtube."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Send a test email to this address.")
        parser.add_argument("--ai", action="store_true", help="Make one small live call to the AI service.")
        parser.add_argument("--whatsapp", help="Send the template message to this number (e.g. 923001234567).")
        parser.add_argument("--youtube", action="store_true", help="Make one live YouTube API call.")

    def line(self, ok, text):
        self.stdout.write(("[ OK ] " if ok else "[ -- ] ") + text)

    def handle(self, *args, **o):
        self.stdout.write("Configuration")
        self.line(bool(settings.YOUTUBE_API_KEY), f"YouTube API key: {mask(settings.YOUTUBE_API_KEY)}")
        self.line(bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET),
                  "Google OAuth client id/secret: " + mask(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET))
        self.line(bool(settings.TOKEN_ENCRYPTION_KEY), f"TOKEN_ENCRYPTION_KEY: {mask(settings.TOKEN_ENCRYPTION_KEY)}")
        self.line(bool(settings.BACKUP_ENCRYPTION_KEY), f"BACKUP_ENCRYPTION_KEY: {mask(settings.BACKUP_ENCRYPTION_KEY)}")
        console = settings.EMAIL_BACKEND.endswith("console.EmailBackend")
        self.line(not console, f"Email backend: {settings.EMAIL_BACKEND.rsplit('.', 1)[-1]}"
                  + (" (emails only print in this window)" if console else f" -> {settings.EMAIL_HOST}:{settings.EMAIL_PORT}"))
        key = settings.GEMINI_API_KEY if ai.provider() == "gemini" else settings.ANTHROPIC_API_KEY
        self.line(ai.is_configured(), f"AI ({ai.provider() or 'none'}) key: {mask(key)} (model {ai.model_name()})")
        self.line(whatsapp.is_configured(), "WhatsApp API: " + mask(whatsapp.is_configured())
                  + f" (template {settings.WHATSAPP_TEMPLATE_NAME}, {settings.WHATSAPP_TEMPLATE_LANG})")
        self.line(True, f"SITE_URL: {settings.SITE_URL}   REQUIRE_2FA: {settings.REQUIRE_2FA}")

        if o["email"]:
            self.stdout.write("\nEmail test")
            try:
                send_mail(f"{settings.BRAND_NAME} test email", "If you can read this, email sending works.",
                          None, [o["email"]], fail_silently=False)
                self.line(True, f"Sent to {o['email']}. Check the inbox (and spam).")
            except Exception as exc:  # noqa: BLE001
                self.line(False, f"Failed: {exc.__class__.__name__}: {exc}")

        if o["ai"]:
            self.stdout.write("\nAI test")
            try:
                reply = ai.ask("Reply with one short sentence.", "Say hello to the Order Platform team.", max_tokens=40)
                self.line(True, f"Answer: {reply[:120]}")
            except ai.AIError as exc:
                self.line(False, str(exc))

        if o["whatsapp"]:
            self.stdout.write("\nWhatsApp test")
            try:
                message_id = whatsapp.send_template(o["whatsapp"], "Test", f"{settings.SITE_URL}/")
                self.line(True, f"Accepted by WhatsApp (message id {message_id}).")
            except whatsapp.WhatsAppError as exc:
                self.line(False, str(exc))

        if o["youtube"]:
            self.stdout.write("\nYouTube test")
            import requests
            try:
                r = requests.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "statistics", "forHandle": "@YouTube", "key": settings.YOUTUBE_API_KEY},
                    timeout=15,
                )
                self.line(r.status_code == 200, f"HTTP {r.status_code}"
                          + ("" if r.status_code == 200 else " - check the key and that YouTube Data API v3 is enabled"))
            except requests.RequestException as exc:
                self.line(False, f"Could not reach YouTube ({exc.__class__.__name__}).")
