"""Automatic encrypted off-disk backups. Disabled until explicitly configured."""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from urllib.parse import urlparse
import uuid

from backup_archive import (
    HISTORICAL_JSON, REQUIRED_TABLES, copy_historical, decrypt_file,
    digest, encrypt_file, private_write, snapshot_sqlite, validate_sqlite,
)


LOG = logging.getLogger(__name__)
SET_NAME = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{32}$")
ALLOWED_OBJECT = re.compile(
    r"^(ledger\.sqlite3|manifest\.json|history-\d{3}\.json)\.enc$"
)
_start_lock = threading.Lock()
_thread = None


def enabled():
    value = os.getenv("BACKUP_ENABLED", "false").lower()
    if value not in ("true", "false"):
        raise ValueError("BACKUP_ENABLED must be true or false")
    return value == "true"


def private_root():
    # Fixed outside application roots AND outside the persistent Render disk.
    # Never accept a caller-selected path which could point into a public tree.
    root = Path("/tmp") / f"zerolife-private-backups-{os.getuid()}"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise PermissionError("Backup staging directory is not private")
    return root


@contextmanager
def private_workdir():
    with tempfile.TemporaryDirectory(prefix="run-", dir=private_root()) as directory:
        os.chmod(directory, 0o700)
        yield Path(directory)


def _number(env, default, minimum, maximum):
    value = int(os.getenv(env, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError("Backup numeric setting outside safe range")
    return value


class Settings:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.interval = _number("BACKUP_INTERVAL_SECONDS", 3600, 60, 86400)
        self.timeout = _number("BACKUP_SNAPSHOT_TIMEOUT_SECONDS", 120, 1, 600)
        self.keep = _number("BACKUP_KEEP_COUNT", 48, 2, 10000)
        self.days = _number("BACKUP_KEEP_DAYS", 30, 1, 3650)
        self.bucket = os.environ["BACKUP_S3_BUCKET"]
        self.prefix = os.getenv("BACKUP_S3_PREFIX", "zerolife-ledger").strip("/")
        if (not self.bucket or not re.fullmatch(r"[A-Za-z0-9_-]+", self.prefix)):
            raise ValueError("A dedicated bucket and simple backup prefix are required")
        self.endpoint = os.getenv("BACKUP_S3_ENDPOINT_URL") or None
        if self.endpoint:
            endpoint = urlparse(self.endpoint)
            if (endpoint.scheme != "https" or not endpoint.hostname
                    or endpoint.username or endpoint.password
                    or endpoint.query or endpoint.fragment):
                raise ValueError("S3 endpoint must use HTTPS without credentials")
        self.key = base64.b64decode(os.environ["BACKUP_ENCRYPTION_KEY"], validate=True)
        if len(self.key) != 32:
            raise ValueError("Backup encryption requires a dedicated 32-byte key")
        # Only explicitly configured historical files; never crawl the disk.
        extra = json.loads(os.getenv("BACKUP_HISTORICAL_JSON_PATHS", "[]"))
        if not isinstance(extra, list) or len(extra) > 100:
            raise ValueError("Historical JSON paths must be a short array")
        self.history = [(self.data_dir / name, False) for name in HISTORICAL_JSON]
        for item in extra:
            if not isinstance(item, str) or not Path(item).is_absolute():
                raise ValueError("Additional historical files need absolute paths")
            path = Path(item)
            if path.suffix.lower() != ".json":
                raise ValueError("Only JSON historical files may be configured")
            self.history.append((path, True))
        self.history = list(dict.fromkeys(self.history))


class S3Store:
    def __init__(self, settings, client=None):
        self.settings = settings
        if client is None:
            # Lazy import: a disabled backup must not break bot startup.
            import boto3
            from botocore.config import Config
            client = boto3.client(
                "s3", endpoint_url=settings.endpoint,
                config=Config(connect_timeout=10, read_timeout=60,
                              retries={"max_attempts": 3, "mode": "standard"}),
            )
        self.client = client

    def upload_verified(self, key, path, work):
        # No ACL headers: use a dedicated private bucket with public access
        # blocked. Ciphertext remains protected even if a bucket is misconfigured.
        self.client.upload_file(str(path), self.settings.bucket, key,
                                ExtraArgs={"ContentType": "application/octet-stream"})
        downloaded = work / ("readback-" + uuid.uuid4().hex)
        try:
            self.client.download_file(self.settings.bucket, key, str(downloaded))
            os.chmod(downloaded, 0o600)
            if digest(downloaded) != digest(path):
                raise ValueError("Remote backup verification failed")
        finally:
            downloaded.unlink(missing_ok=True)

    def objects(self):
        paginator = self.client.get_paginator("list_objects_v2")
        result = {}
        for page in paginator.paginate(Bucket=self.settings.bucket,
                                       Prefix=self.settings.prefix + "/"):
            for obj in page.get("Contents", []):
                suffix = obj["Key"][len(self.settings.prefix) + 1:]
                parts = suffix.split("/")
                if len(parts) == 2 and SET_NAME.fullmatch(parts[0]) and ALLOWED_OBJECT.fullmatch(parts[1]):
                    result.setdefault(parts[0], []).append(obj["Key"])
        return result

    def rotate(self, current):
        groups = self.objects()  # Paginate; never assume <= 1000 objects.
        completed = sorted(
            (name for name, keys in groups.items()
             if f"{self.settings.prefix}/{name}/manifest.json.enc" in keys),
            reverse=True,
        )
        if current not in completed:
            raise ValueError("Verified manifest is not visible; refusing retention")
        # Keep at least the newest N completed sets, AND all sets younger than
        # D days. Failed attempts never count toward the minimum.
        cutoff = time.time() - self.settings.days * 86400
        protected = set(completed[:self.settings.keep]) | {current}
        for name, keys in groups.items():
            stamp = datetime.strptime(name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
            if name in protected or stamp >= cutoff:
                continue
            # Remove the completion marker first. A crash during retention
            # must not leave a partially deleted set marked as complete.
            for key in sorted(keys, key=lambda value: not value.endswith("/manifest.json.enc")):
                self.client.delete_object(Bucket=self.settings.bucket, Key=key)


def run_backup(settings, store):
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex
    with private_workdir() as work:
        database = work / "ledger.sqlite3"
        snapshot_sqlite(settings.data_dir / "wallet_ledger.sqlite3",
                        database, settings.timeout)
        files = [("ledger.sqlite3", database, None)]
        missing = []
        for index, (path, required) in enumerate(settings.history):
            if not path.exists() and not path.is_symlink():
                if required:
                    raise FileNotFoundError("Configured historical JSON is missing")
                missing.append(path.name)
                continue
            local = work / f"history-{index:03d}.json"
            copy_historical(path, local)
            files.append((local.name, local, str(path)))
        manifest = {
            "format": 1, "run_id": run_id, "started_at": started.isoformat(),
            "sqlite_tables": sorted(REQUIRED_TABLES), "files": [],
            "absent_optional_history": missing,
            "consistency": "SQLite online snapshot; JSON files are independent captures",
        }
        for name, path, source in files:
            encrypted = work / (name + ".enc")
            encrypt_file(path, encrypted, settings.key)
            # Authenticate and inspect the encrypted archive before uploading.
            checked = work / ("checked-" + name)
            decrypt_file(encrypted, checked, settings.key)
            if digest(path) != digest(checked):
                raise ValueError("Archive roundtrip verification failed")
            if name == "ledger.sqlite3":
                validate_sqlite(checked)
            checked.unlink()
            key = f"{settings.prefix}/{run_id}/{name}.enc"
            store.upload_verified(key, encrypted, work)
            manifest["files"].append({
                "object": key, "sha256": digest(path),
                "encrypted_sha256": digest(encrypted), "bytes": path.stat().st_size,
                "historical_source": source,
            })
        # Commit marker uploaded LAST, only after every object passed readback.
        # The manifest is also encrypted: no historical paths/metadata are public.
        manifest_path = work / "manifest.json"
        private_write(manifest_path, json.dumps(manifest, sort_keys=True).encode())
        encrypted = work / "manifest.json.enc"
        encrypt_file(manifest_path, encrypted, settings.key)
        store.upload_verified(f"{settings.prefix}/{run_id}/manifest.json.enc", encrypted, work)
        store.rotate(run_id)
    return run_id


def _write_status(status, last_success=None):
    root = private_root()
    temporary = root / ("status-" + uuid.uuid4().hex)
    private_write(temporary, json.dumps({
        "status": status, "last_success": last_success,
        "updated_at": time.time(),
    }).encode())
    os.replace(temporary, root / "status.json")


def health():
    """Only generic health; never disclose balances, filenames or settings."""
    try:
        if not enabled():
            return {"status": "disabled"}, 503
        status = json.loads((private_root() / "status.json").read_text())
        interval = _number("BACKUP_INTERVAL_SECONDS", 3600, 60, 86400)
        if status["status"] != "ok":
            return {"status": "error"}, 503
        if time.time() - status["last_success"] > interval * 2 + 600:
            return {"status": "stale"}, 503
        return {"status": "ok"}, 200
    except Exception:
        return {"status": "pending"}, 503


def _worker(data_dir, notify, stop):
    last_success = None
    last_alert = 0
    while not stop.is_set():
        try:
            # Held for the worker lifetime: gunicorn processes/reloads must not
            # run competing uploads/retention. Standby workers can take over.
            root = private_root()
            with open(root / "worker.lock", "a", opener=lambda p, flags: os.open(
                p, flags | os.O_NOFOLLOW, 0o600
            )) as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    stop.wait(30)
                    continue
                # Clean crash leftovers ONLY after acquiring the worker lock.
                import shutil
                for leftover in root.glob("run-*"):
                    if leftover.is_dir() and not leftover.is_symlink():
                        shutil.rmtree(leftover)
                _write_status("pending")
                while not stop.is_set():
                    interval = 300
                    try:
                        settings = Settings(data_dir)
                        interval = settings.interval
                        if notify is None:
                            raise ValueError("An admin error notification channel is required")
                        run_backup(settings, S3Store(settings))
                        last_success = time.time()
                        _write_status("ok", last_success)
                        LOG.info("Off-disk backup completed and verified")
                    except Exception as exc:
                        _write_status("error", last_success)
                        # Never log exception text/tracebacks: SDK errors may
                        # contain credentials, URLs or private file paths.
                        LOG.error("Off-disk backup failed (%s)", type(exc).__name__)
                        if time.time() - last_alert >= 3600:
                            if notify:
                                try:
                                    notify("Zer0Life Run: резервная копия вне диска не подтверждена. "
                                           "Проверьте /health/backups и настройки BACKUP_ в Render. "
                                           "Не выполняйте восстановление диска.")
                                except Exception as alert_error:
                                    LOG.error("Backup admin alert failed (%s)", type(alert_error).__name__)
                            last_alert = time.time()
                    stop.wait(interval)
        except Exception as exc:
            LOG.error("Backup worker unavailable (%s)", type(exc).__name__)
            stop.wait(60)


def start_backups(data_dir, notify=None):
    """Call once at bot startup; first backup is immediate, not after an hour."""
    global _thread
    try:
        if not enabled():
            LOG.warning("Off-disk backups disabled; no automatic off-disk protection")
            return None
    except ValueError:
        LOG.error("Invalid BACKUP_ENABLED; off-disk protection unavailable")
        return None
    with _start_lock:
        if _thread and _thread.is_alive():
            return _thread
        stop = threading.Event()
        _thread = threading.Thread(target=_worker, args=(data_dir, notify, stop),
                                   name="private-ledger-backups", daemon=True)
        _thread.start()
        return _thread
