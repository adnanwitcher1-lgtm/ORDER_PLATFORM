import os
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.backup import (
    BackupError, decrypt_bytes, encrypt_bytes, prune, sqlite_snapshot, verify_plain,
)


class Command(BaseCommand):
    help = "Creates an encrypted database backup, or verifies/decrypts an existing backup."

    def add_arguments(self, parser):
        parser.add_argument("--verify", metavar="FILE", help="Decrypt and verify a backup (restore test).")
        parser.add_argument("--decrypt", metavar="FILE", help="Decrypt the backup and write it to --out.")
        parser.add_argument("--out", metavar="PATH", help="Output file to use with --decrypt.")

    def handle(self, *args, **options):
        try:
            if options["verify"]:
                kind = verify_plain(decrypt_bytes(Path(options["verify"]).read_bytes()))
                self.stdout.write(self.style.SUCCESS(f"Backup is valid ({kind})."))
            elif options["decrypt"]:
                if not options["out"]:
                    raise CommandError("--out is required with --decrypt.")
                Path(options["out"]).write_bytes(decrypt_bytes(Path(options["decrypt"]).read_bytes()))
                self.stdout.write(self.style.SUCCESS(f"Decrypted and written to {options['out']}."))
            else:
                self.create()
        except BackupError as exc:
            raise CommandError(str(exc))

    def create(self):
        db = settings.DATABASES["default"]
        engine = db["ENGINE"]

        if engine.endswith("sqlite3"):
            fd, temp_path = tempfile.mkstemp(suffix=".sqlite3")
            os.close(fd)
            try:
                sqlite_snapshot(db["NAME"], temp_path)
                plain = Path(temp_path).read_bytes()
            finally:
                os.unlink(temp_path)
        elif "postgresql" in engine:
            env = {**os.environ, "PGPASSWORD": str(db.get("PASSWORD", ""))}
            command = ["pg_dump", "-Fc", "-h", db.get("HOST") or "127.0.0.1",
                       "-p", str(db.get("PORT") or "5432"), "-U", db["USER"], "-d", db["NAME"]]
            try:
                plain = subprocess.run(command, env=env, capture_output=True, check=True).stdout
            except FileNotFoundError:
                raise BackupError("pg_dump is not installed or not in PATH.")
            except subprocess.CalledProcessError as exc:
                raise BackupError(f"pg_dump failed: {exc.stderr.decode(errors='replace')[:300]}")
        else:
            raise BackupError(f"Backup for this database ({engine}) is not supported yet.")

        encrypted = encrypt_bytes(plain)
        folder = Path(settings.BACKUP_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"backup_{timezone.localtime():%Y%m%d_%H%M%S}.enc"
        target.write_bytes(encrypted)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass

        removed = prune(folder, settings.BACKUP_KEEP)
        self.stdout.write(self.style.SUCCESS(
            f"Backup created: {target} ({len(encrypted) // 1024} KB). Old backups removed: {removed}."
        ))