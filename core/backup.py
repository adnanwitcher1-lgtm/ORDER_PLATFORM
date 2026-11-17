import os
import sqlite3
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

SQLITE_HEADER = b"SQLite format 3"
POSTGRES_HEADER = b"PGDMP"


class BackupError(Exception):
    """Error that is safe to show to the user."""


def _fernet():
    key = getattr(settings, "BACKUP_ENCRYPTION_KEY", "")
    if not key:
        raise BackupError("BACKUP_ENCRYPTION_KEY is not set (.env).")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise BackupError("BACKUP_ENCRYPTION_KEY has an invalid format.")


def encrypt_bytes(data):
    return _fernet().encrypt(data)


def decrypt_bytes(data):
    try:
        return _fernet().decrypt(data)
    except InvalidToken:
        raise BackupError("The backup could not be decrypted (wrong key or damaged file).")


def sqlite_snapshot(source_path, target_path):
    """A consistent copy of SQLite (even while the server is running)."""
    source = sqlite3.connect(str(source_path))
    target = sqlite3.connect(str(target_path))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def verify_plain(data):
    """Check the backup after decryption. Returns 'sqlite' or 'postgresql'."""
    if data.startswith(SQLITE_HEADER):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        try:
            Path(path).write_bytes(data)
            connection = sqlite3.connect(path)
            try:
                result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                connection.close()
        finally:
            os.unlink(path)
        if result != "ok":
            raise BackupError(f"SQLite integrity check fail: {result}")
        return "sqlite"
    if data.startswith(POSTGRES_HEADER):
        return "postgresql"
    raise BackupError("The backup format was not recognized.")


def prune(directory, keep, pattern="backup_*.enc"):
    """Keep the newest `keep` backups and delete the rest. The name contains a timestamp, so sorting works."""
    files = sorted(Path(directory).glob(pattern), reverse=True)
    stale = files[keep:]
    for path in stale:
        path.unlink()
    return len(stale)