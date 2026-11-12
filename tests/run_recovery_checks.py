#!/usr/bin/env python3
"""Run only offline accounting recovery checks. No DB/URL/credential arguments."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if len(sys.argv) != 1:
        print("This runner accepts no arguments; all fixtures are generated in temporary directories.", file=sys.stderr)
        return 2
    if not shutil.which("node"):
        print("Node.js 22+ is required for the synthetic legacy storage checks.", file=sys.stderr)
        return 2
    # Keep only interpreter/runtime discovery settings. Never inherit bot, wallet,
    # recovery-transfer, database or deployment credentials into the test process.
    env = {key: value for key, value in os.environ.items()
           if key in ("PATH", "PYTHONPATH", "LD_LIBRARY_PATH", "PYTHONHOME", "SYSTEMROOT")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    bootstrap = """
import socket, sys
import unittest
def forbidden(*args, **kwargs):
    raise AssertionError('Network forbidden in offline recovery checks')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
sys.path.insert(0, 'tests')
suite = unittest.defaultTestLoader.loadTestsFromNames([
    'test_recovery_accounting', 'test_recovery_history'])
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
"""
    commands = [
        [sys.executable, "-c", bootstrap],
        ["node", "--test", "tests/legacy-recovery.test.cjs", "tests/legacy-farms.test.cjs",
         "tests/recovery-actions.test.cjs", "tests/recovery-storage.test.cjs"],
    ]
    failed = False
    for command in commands:
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, timeout=120, check=False)
            failed |= result.returncode != 0
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"Recovery check could not complete: {type(error).__name__}", file=sys.stderr)
            failed = True
    print("Offline recovery checks: " + ("FAILED" if failed else "PASSED"), flush=True)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
