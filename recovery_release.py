"""Public recovery compatibility contract; never include runtime configuration."""
import os
import re

RELEASE_ID = "recovery-isolation-2026-10-17"
RECOVERY_PROTOCOL = 1
RECOVERY_CAPABILITIES = ("owner-approved-principal-yield-v2", "release-gated-approval-v1", "per-account-quote-errors-v1")
UPDATE_MESSAGE = (
    "Recovery update in progress. The website and server must finish updating before "
    "owner approval is available. Reload the app and try again. Archives and balances are unchanged."
)
# Render supplies this public source revision. Reject arbitrary environment text.
_revision = os.getenv("RENDER_GIT_COMMIT", "")
SOURCE_COMMIT = _revision if re.fullmatch(r"[0-9a-fA-F]{40}", _revision) else None


def release_metadata():
    return {
        "releaseId": RELEASE_ID,
        "sourceCommit": SOURCE_COMMIT,
        "recovery": {
            "protocol": RECOVERY_PROTOCOL,
            "capabilities": list(RECOVERY_CAPABILITIES),
            "ready": True,
        },
    }


def compatible_client(data):
    client = data.get("recoveryClient")
    return (
        isinstance(client, dict)
        and client.get("releaseId") == RELEASE_ID
        and type(client.get("protocol")) is int
        and client["protocol"] == RECOVERY_PROTOCOL
    )
