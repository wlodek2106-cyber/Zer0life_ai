"""Synthetic-only checks; all temporary DBs live outside the served workspace."""

import base64
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import backup_archive as archive
import wallet_backup as backup


class FakeS3:
    def __init__(self):
        self.data = {}
        self.deleted = []
        self.corrupt = False

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.data[key] = Path(path).read_bytes()

    def download_file(self, bucket, key, path):
        raw = self.data[key]
        Path(path).write_bytes(raw + (b"corrupt" if self.corrupt else b""))

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket, Prefix):
        # Force multiple pages, including empty pages.
        yield {}
        for key in sorted(self.data):
            if key.startswith(Prefix):
                yield {"Contents": [{"Key": key}]}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        del self.data[Key]


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zrl-backup-test-", dir="/tmp")
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir(mode=0o700)
        self.db_path = self.data / "wallet_ledger.sqlite3"
        self.db = sqlite3.connect(self.db_path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA wal_autocheckpoint=0")
        for table in archive.REQUIRED_TABLES:
            self.db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY, value TEXT)")
        self.db.execute("INSERT INTO wallet_accounts VALUES(1, 'synthetic-account')")
        self.db.execute("INSERT INTO wallet_farms VALUES(1, 'synthetic-farm')")
        self.db.commit()
        self.env = patch.dict(os.environ, {
            "BACKUP_ENABLED": "true", "BACKUP_S3_BUCKET": "test-private",
            "BACKUP_S3_PREFIX": "test-ledger",
            "BACKUP_ENCRYPTION_KEY": base64.b64encode(b"x" * 32).decode(),
            "BACKUP_INTERVAL_SECONDS": "60", "BACKUP_KEEP_COUNT": "2",
            "BACKUP_KEEP_DAYS": "1", "BACKUP_HISTORICAL_JSON_PATHS": "[]",
            "BACKUP_S3_ENDPOINT_URL": "https://s3.example.invalid",
        })
        self.env.start()
        self.settings = backup.Settings(self.data)
        self.s3 = FakeS3()
        self.store = backup.S3Store(self.settings, self.s3)

    def tearDown(self):
        self.db.close()
        self.env.stop()
        self.temp.cleanup()

    def decrypt(self, raw, name):
        encrypted = self.root / (name + ".enc")
        target = self.root / name
        encrypted.write_bytes(raw)
        archive.decrypt_file(encrypted, target, self.settings.key)
        return target

    def test_complete_backup_roundtrip_wal_and_json_without_source_changes(self):
        self.assertTrue(Path(str(self.db_path) + "-wal").exists())
        (self.data / "users.json").write_text('["synthetic-user"]')
        (self.data / "leaderboard_stats.json").write_text('{"synthetic": {"distance": 9}}')
        before = self.db.execute("SELECT * FROM wallet_accounts").fetchall()
        self.db.execute("INSERT INTO wallet_accounts VALUES(2, 'uncommitted')")
        run_id = backup.run_backup(self.settings, self.store)
        prefix = f"test-ledger/{run_id}/"
        self.assertEqual(list(self.s3.data)[-1], prefix + "manifest.json.enc")
        manifest = json.loads(self.decrypt(self.s3.data[prefix + "manifest.json.enc"], "manifest.json").read_text())
        self.assertEqual(len(manifest["files"]), 3)
        ledger = self.decrypt(self.s3.data[prefix + "ledger.sqlite3.enc"], "roundtrip.sqlite3")
        archive.validate_sqlite(ledger)
        with sqlite3.connect(ledger) as db:
            self.assertEqual(db.execute("SELECT * FROM wallet_accounts").fetchall(), before)
            self.assertEqual(db.execute("SELECT value FROM wallet_farms").fetchone()[0], "synthetic-farm")
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT * FROM wallet_accounts").fetchall(), before)
        for entry in manifest["files"]:
            self.assertEqual(entry["encrypted_sha256"], archive.digest(
                self._bytes_file(self.s3.data[entry["object"]], "hash-check")
            ))
        self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)
        for ciphertext in self.s3.data.values():
            self.assertNotIn(b"synthetic-account", ciphertext)
            self.assertNotIn(b"synthetic-user", ciphertext)
        self.assertFalse(list(backup.private_root().glob("run-*")))

    def _bytes_file(self, data, name):
        target = self.root / name
        target.write_bytes(data)
        return target

    def test_missing_source_never_creates_a_database(self):
        absent = self.data / "absent.sqlite3"
        with self.assertRaises(FileNotFoundError):
            archive.snapshot_sqlite(absent, self.root / "snapshot")
        self.assertFalse(absent.exists())

    def test_actual_wallet_schema_includes_all_required_accounting_tables(self):
        from flask import Flask
        from wallet_service import register_wallet_routes

        data_dir = self.root / "actual-schema"
        app = Flask("backup-schema-test")
        register_wallet_routes(app, str(data_dir), admin_telegram_id="synthetic-admin")
        settings = backup.Settings(data_dir)
        run_id = backup.run_backup(settings, self.store)
        ledger = self.decrypt(
            self.s3.data[f"test-ledger/{run_id}/ledger.sqlite3.enc"], "actual.sqlite3"
        )
        archive.validate_sqlite(ledger)

    def test_missing_tables_and_corruption_block_uploads(self):
        self.db.execute("DROP TABLE wallet_farms")
        self.db.commit()
        with self.assertRaises(ValueError):
            backup.run_backup(self.settings, self.store)
        self.assertEqual(self.s3.data, {})
        bad = self.root / "corrupt.sqlite3"
        bad.write_bytes(b"not sqlite")
        with self.assertRaises(sqlite3.DatabaseError):
            archive.validate_sqlite(bad)

    def test_foreign_key_check_is_enforced(self):
        self.db.executescript("""
            CREATE TABLE parent(id INTEGER PRIMARY KEY);
            CREATE TABLE child(parent_id INTEGER REFERENCES parent(id));
            INSERT INTO child VALUES(999);
        """)
        with self.assertRaises(ValueError):
            archive.snapshot_sqlite(self.db_path, self.root / "fk.sqlite3")

    def test_invalid_or_missing_configured_history_blocks_completion(self):
        (self.data / "users.json").write_text("{broken")
        with self.assertRaises(ValueError):
            backup.run_backup(self.settings, self.store)
        self.assertEqual(self.s3.data, {})
        (self.data / "users.json").unlink()
        self.settings.history.append((self.data / "missing.json", True))
        with self.assertRaises(FileNotFoundError):
            backup.run_backup(self.settings, self.store)
        self.assertEqual(self.s3.data, {})

    def test_additional_history_is_separate_and_missing_optional_is_recorded(self):
        extra = self.root / "old-farms.json"
        extra.write_text('[{"id": "synthetic-farm"}]')
        with patch.dict(os.environ, {"BACKUP_HISTORICAL_JSON_PATHS": json.dumps([str(extra)])}):
            settings = backup.Settings(self.data)
        run_id = backup.run_backup(settings, self.store)
        manifest = json.loads(self.decrypt(
            self.s3.data[f"test-ledger/{run_id}/manifest.json.enc"], "history-manifest.json"
        ).read_text())
        self.assertEqual(set(manifest["absent_optional_history"]), set(archive.HISTORICAL_JSON))
        entry = manifest["files"][1]
        self.assertEqual(entry["historical_source"], str(extra))
        restored = self.decrypt(self.s3.data[entry["object"]], "inspected-farms.json")
        self.assertEqual(restored.read_bytes(), extra.read_bytes())

    def test_remote_corruption_no_commit_and_no_retention(self):
        self.s3.corrupt = True
        with self.assertRaises(ValueError):
            backup.run_backup(self.settings, self.store)
        self.assertFalse(any(key.endswith("manifest.json.enc") for key in self.s3.data))
        self.assertEqual(self.s3.deleted, [])
        self.assertFalse(list(backup.private_root().glob("run-*")))

    def test_upload_failure_preserves_existing_sets(self):
        self.add_set(8, "1")
        original = dict(self.s3.data)
        with patch.object(self.s3, "upload_file", side_effect=OSError("secret-test")):
            with self.assertRaises(OSError):
                backup.run_backup(self.settings, self.store)
        self.assertEqual(self.s3.data, original)
        self.assertEqual(self.s3.deleted, [])

    def test_authenticated_stream_rejects_mutation_truncation_and_wrong_key(self):
        plain = self.root / "plain"
        # Larger than a frame, plus empty input covered below.
        plain.write_bytes(b"synthetic" * (archive.CHUNK // 4))
        encrypted = self.root / "encrypted"
        archive.encrypt_file(plain, encrypted, self.settings.key)
        raw = encrypted.read_bytes()
        for index, damaged in enumerate((raw[:-1], raw[:-20], raw + b"x",
                                         raw[:100] + bytes([raw[100] ^ 1]) + raw[101:])):
            encrypted.write_bytes(damaged)
            target = self.root / f"rejected-{index}"
            with self.assertRaises(Exception):
                archive.decrypt_file(encrypted, target, self.settings.key)
            self.assertFalse(target.exists())
        encrypted.write_bytes(raw)
        with self.assertRaises(Exception):
            archive.decrypt_file(encrypted, self.root / "wrong-key", b"y" * 32)
        plain.write_bytes(b"")
        encrypted.unlink()
        archive.encrypt_file(plain, encrypted, self.settings.key)
        archive.decrypt_file(encrypted, self.root / "empty", self.settings.key)
        self.assertEqual((self.root / "empty").read_bytes(), b"")

    def add_set(self, days_old, suffix, complete=True):
        stamp = (datetime.now(timezone.utc) - timedelta(days=days_old)).strftime("%Y%m%dT%H%M%SZ")
        name = stamp + "-" + suffix * 32
        for filename in ("ledger.sqlite3.enc", "history-000.json.enc"):
            self.s3.data[f"test-ledger/{name}/{filename}"] = b"fake"
        if complete:
            self.s3.data[f"test-ledger/{name}/manifest.json.enc"] = b"fake"
        return name

    def test_rotation_minimum_age_partial_cleanup_and_prefix_isolation(self):
        oldest = self.add_set(8, "1")
        protected = self.add_set(7, "2")
        current = self.add_set(0, "3")
        partial = self.add_set(9, "4", complete=False)
        self.s3.data["another-prefix/private-object"] = b"untouched"
        self.s3.data[f"test-ledger/{oldest}/unknown-object"] = b"untouched"
        self.store.rotate(current)
        self.assertTrue(any(f"/{oldest}/" in key for key in self.s3.deleted))
        self.assertTrue(any(f"/{partial}/" in key for key in self.s3.deleted))
        self.assertFalse(any(f"/{protected}/" in key for key in self.s3.deleted))
        self.assertIn("another-prefix/private-object", self.s3.data)
        self.assertIn(f"test-ledger/{oldest}/unknown-object", self.s3.data)
        removed_oldest = [key for key in self.s3.deleted if f"/{oldest}/" in key]
        self.assertEqual(removed_oldest[0], f"test-ledger/{oldest}/manifest.json.enc")

    def test_rotation_requires_visible_new_complete_set(self):
        self.add_set(9, "a")
        with self.assertRaises(ValueError):
            self.store.rotate("not-visible")
        self.assertEqual(self.s3.deleted, [])

    def test_configuration_rejects_unsafe_values(self):
        for key, value in (
            ("BACKUP_S3_ENDPOINT_URL", "http://example.invalid"),
            ("BACKUP_S3_ENDPOINT_URL", "https://user:pass@example.invalid"),
            ("BACKUP_ENCRYPTION_KEY", "not-base64"),
            ("BACKUP_KEEP_COUNT", "1"),
            ("BACKUP_INTERVAL_SECONDS", "0"),
            ("BACKUP_S3_PREFIX", "../another"),
            ("BACKUP_HISTORICAL_JSON_PATHS", '["relative.json"]'),
        ):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                with self.assertRaises((ValueError, TypeError)):
                    backup.Settings(self.data)

    def test_no_overwrite_and_no_symlink_history(self):
        existing = self.root / "already-exists"
        existing.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            archive.snapshot_sqlite(self.db_path, existing)
        self.assertEqual(existing.read_bytes(), b"preserve")
        (self.data / "users.json").symlink_to(existing)
        with self.assertRaises(ValueError):
            backup.run_backup(self.settings, self.store)

    def test_health_never_discloses_private_data(self):
        fake_root = self.root / "health"
        fake_root.mkdir()
        with patch.object(backup, "private_root", return_value=fake_root):
            self.assertEqual(backup.health(), ({"status": "pending"}, 503))
            backup._write_status("ok", backup.time.time())
            self.assertEqual(backup.health(), ({"status": "ok"}, 200))
            backup._write_status("ok", backup.time.time() - 900)
            self.assertEqual(backup.health(), ({"status": "stale"}, 503))
            backup._write_status("error", backup.time.time())
            self.assertEqual(backup.health(), ({"status": "error"}, 503))
            with patch.dict(os.environ, {"BACKUP_ENABLED": "false"}):
                self.assertEqual(backup.health(), ({"status": "disabled"}, 503))

    def test_worker_alerts_and_redacts_failure(self):
        stop = threading.Event()
        alerts = []
        def failure(*args):
            stop.set()
            raise RuntimeError("sensitive-secret-do-not-log")
        with patch.object(backup, "S3Store", return_value=self.store), \
                patch.object(backup, "run_backup", side_effect=failure), \
                patch.object(backup, "_write_status") as status, \
                self.assertLogs(backup.LOG, logging.ERROR) as logs:
            backup._worker(self.data, alerts.append, stop)
        self.assertEqual(len(alerts), 1)
        self.assertNotIn("sensitive-secret", "\n".join(logs.output) + alerts[0])
        status.assert_called_with("error", None)

    def test_worker_lock_prevents_second_process_work(self):
        stop = threading.Event()
        class OneWait:
            def is_set(self):
                return stop.is_set()
            def wait(self, seconds):
                stop.set()
        with open(backup.private_root() / "worker.lock", "a") as lock:
            backup.fcntl.flock(lock, backup.fcntl.LOCK_EX)
            with patch.object(backup, "run_backup") as run:
                backup._worker(self.data, None, OneWait())
                run.assert_not_called()

    def test_disabled_start_does_not_create_a_worker(self):
        with patch.dict(os.environ, {"BACKUP_ENABLED": "false"}), \
                patch.object(backup.threading, "Thread") as thread:
            self.assertIsNone(backup.start_backups(self.data))
            thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
