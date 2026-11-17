"""TOTP (authenticator app) helpers: setup, verification, recovery codes."""
import hashlib
import io
import secrets

import pyotp
import qrcode
import qrcode.image.svg
from django.conf import settings
from django.utils import timezone

from . import crypto
from .models import RecoveryCode, TwoFactorDevice

CODE_COUNT = 8


def has_two_factor(user):
    return TwoFactorDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def new_secret():
    return pyotp.random_base32()


def provisioning_uri(user, secret):
    issuer = getattr(settings, "BRAND_NAME", "Order Platform")
    return pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=issuer)


def qr_svg(uri):
    """Inline SVG of the QR code (generated here, safe to mark as HTML)."""
    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode()


def _match(secret, code, last_counter, now=None):
    """Return the matching time step (> last_counter) or None. Allows 1 step of clock drift."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    now = now or timezone.now()
    current = totp.timecode(now)
    for step in (current - 1, current, current + 1):
        if step > last_counter and secrets.compare_digest(totp.at(step * totp.interval), code):
            return step
    return None


def confirm_setup(user, secret, code):
    """Check the first code. On success the device is saved and 8 recovery codes are returned."""
    step = _match(secret, code, 0)
    if step is None:
        return None
    TwoFactorDevice.objects.update_or_create(
        user=user,
        defaults={"secret_encrypted": crypto.encrypt(secret), "confirmed_at": timezone.now(), "last_counter": step},
    )
    return regenerate_recovery_codes(user)


def verify_code(user, code):
    device = TwoFactorDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
    if device is None:
        return False
    secret = crypto.decrypt(device.secret_encrypted)
    if not secret:
        return False
    step = _match(secret, code, device.last_counter)
    if step is None:
        return False
    # compare-and-set so the same code cannot be used twice, even in parallel
    updated = TwoFactorDevice.objects.filter(pk=device.pk, last_counter__lt=step).update(last_counter=step)
    return bool(updated)


def _hash(code):
    return hashlib.sha256(code.replace("-", "").lower().encode()).hexdigest()


def regenerate_recovery_codes(user):
    RecoveryCode.objects.filter(user=user).delete()
    codes = []
    for _ in range(CODE_COUNT):
        raw = secrets.token_hex(4)                 # 8 hex characters
        codes.append(f"{raw[:4]}-{raw[4:]}")
    RecoveryCode.objects.bulk_create([RecoveryCode(user=user, code_hash=_hash(c)) for c in codes])
    return codes


def use_recovery_code(user, code):
    cleaned = (code or "").strip()
    if len(cleaned.replace("-", "")) != 8:
        return False
    updated = RecoveryCode.objects.filter(
        user=user, code_hash=_hash(cleaned), used_at__isnull=True
    ).first()
    if updated is None:
        return False
    return bool(RecoveryCode.objects.filter(pk=updated.pk, used_at__isnull=True).update(used_at=timezone.now()))


def unused_recovery_count(user):
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def disable(user):
    TwoFactorDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()
