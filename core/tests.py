import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from customers.models import Customer
from orders.models import Order
from orders.monitoring import run_due_checks
from orders.youtube import FetchResult
from ytchannels.models import Channel

from .backup import BackupError, decrypt_bytes, encrypt_bytes, prune, sqlite_snapshot, verify_plain
from .models import SiteSettings

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()


@override_settings(BACKUP_ENCRYPTION_KEY=KEY)
class BackupTests(SimpleTestCase):
    def test_roundtrip_and_wrong_key(self):
        token = encrypt_bytes(b"hello")
        self.assertEqual(decrypt_bytes(token), b"hello")
        with override_settings(BACKUP_ENCRYPTION_KEY=OTHER_KEY):
            with self.assertRaises(BackupError):
                decrypt_bytes(token)

    def test_sqlite_snapshot_is_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.sqlite3"
            copy = Path(folder) / "copy.sqlite3"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE t (x INTEGER)")
            connection.execute("INSERT INTO t VALUES (1)")
            connection.commit()
            connection.close()
            sqlite_snapshot(source, copy)
            self.assertEqual(verify_plain(copy.read_bytes()), "sqlite")

    def test_prune_keeps_newest(self):
        with tempfile.TemporaryDirectory() as folder:
            for stamp in ("20260101_000000", "20260102_000000", "20260103_000000"):
                (Path(folder) / f"backup_{stamp}.enc").write_bytes(b"x")
            self.assertEqual(prune(folder, 2), 1)
            names = sorted(p.name for p in Path(folder).glob("*.enc"))
            self.assertEqual(names, ["backup_20260102_000000.enc", "backup_20260103_000000.enc"])


class SiteSettingsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("admin1", password="pass12345")
        self.manager = User.objects.create_user("mgr1", password="pass12345", role="manager")

    def form_data(self, **overrides):
        data = {
            "monitoring_interval_minutes": 37, "retry_base_minutes": 5, "max_failures": 5,
            "review_overdue_hours": 12, "drop_alert_percent": 4, "stalled_alert_hours": 36,
            "audit_retention_days": 365, "notification_retention_days": 90,
        }
        data.update(overrides)
        return data

    @override_settings(MONITORING_INTERVAL_MINUTES=45)
    def test_load_uses_django_settings_defaults(self):
        self.assertEqual(SiteSettings.load().monitoring_interval_minutes, 45)

    def test_superuser_can_save_and_it_is_audited(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("core:settings"), self.form_data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SiteSettings.load().monitoring_interval_minutes, 37)
        entry = AuditLog.objects.get(action="updated", entity_type="Settings")
        self.assertEqual(entry.changes["monitoring_interval_minutes"][1], "37")

    def test_anomaly_thresholds_are_saved_and_validated(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("core:settings"), self.form_data())
        loaded = SiteSettings.load()
        self.assertEqual((loaded.drop_alert_percent, loaded.stalled_alert_hours), (4, 36))
        bad = self.client.post(reverse("core:settings"), self.form_data(drop_alert_percent=0, stalled_alert_hours=2))
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(SiteSettings.load().drop_alert_percent, 4)

    def test_manager_cannot_open_settings(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("core:settings")).status_code, 403)

    def test_invalid_interval_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("core:settings"), self.form_data(monitoring_interval_minutes=0))
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(SiteSettings.load().monitoring_interval_minutes, 0)

    @override_settings(YOUTUBE_API_KEY="test-key")
    def test_monitoring_uses_site_settings(self):
        config = SiteSettings.load()
        config.monitoring_interval_minutes = 120
        config.save()
        operator = get_user_model().objects.create_user("op1", password="pass12345", role="operator")
        customer = Customer.objects.create(name="Ali")
        channel = Channel.objects.create(
            customer=customer, name="Ali TV", url="https://www.youtube.com/@alitv", youtube_channel_id="UC1"
        )
        order = Order.objects.create(
            customer=customer, channel=channel, requested_increase=100, baseline_count=500,
            status=Order.Status.MONITORING, assigned_to=operator,
        )
        found = FetchResult(560, "exact", "ok", "", "UC1")
        with patch("orders.monitoring.fetch_many", return_value={"UC1": found}):
            run_due_checks()
        order.refresh_from_db()
        self.assertGreater(order.next_check_at, timezone.now() + timedelta(minutes=90))