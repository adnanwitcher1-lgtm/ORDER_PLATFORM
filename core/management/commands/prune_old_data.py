from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_audit
from core.models import SiteSettings
from notifications.models import Notification


class Command(BaseCommand):
    help = "Deletes old audit logs and read notifications according to the retention settings."

    def handle(self, *args, **options):
        cfg = SiteSettings.load()
        now = timezone.now()

        audit_deleted = AuditLog.objects.filter(
            created_at__lt=now - timedelta(days=cfg.audit_retention_days)
        ).delete()[0]
        notifications_deleted = Notification.objects.filter(
            read_at__isnull=False,
            created_at__lt=now - timedelta(days=cfg.notification_retention_days),
        ).delete()[0]

        log_audit(None, "Retention cleanup", entity_type="System", entity_label="prune_old_data",
                  changes={"audit_logs_deleted": audit_deleted,
                           "notifications_deleted": notifications_deleted})
        self.stdout.write(f"Audit logs removed: {audit_deleted}, notifications removed: {notifications_deleted}")