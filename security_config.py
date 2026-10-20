"""Shared HTTP security configuration for the Flask services."""

import os
from urllib.parse import urlsplit

from flask_cors import CORS


DEFAULT_CORS_ORIGIN = "https://wlodek2106-cyber.github.io"
ALLOWED_CORS_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
ALLOWED_CORS_HEADERS = ["Content-Type", "Authorization"]


def _configured_origins() -> list[str]:
    raw_origins = os.getenv("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ORIGIN)
    origins = [item.strip().rstrip("/") for item in raw_origins.split(",") if item.strip()]
    if not origins:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must contain at least one HTTPS origin")

    for origin in origins:
        parsed = urlsplit(origin)
        is_local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
        if (
            origin == "*"
            or parsed.scheme != "https" and not is_local_http
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise RuntimeError(f"Invalid CORS origin: {origin!r}")

    return origins


def configure_cors(app) -> None:
    """Allow browser API access only from explicitly configured origins."""
    CORS(
        app,
        resources={r"/*": {"origins": _configured_origins()}},
        allow_headers=ALLOWED_CORS_HEADERS,
        methods=ALLOWED_CORS_METHODS,
        supports_credentials=False,
    )
