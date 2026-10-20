import os
import unittest
from unittest.mock import patch

from flask import Flask, jsonify

from security_config import configure_cors


class SecurityConfigTests(unittest.TestCase):
    def make_client(self):
        app = Flask(__name__)
        configure_cors(app)

        @app.route("/api/health", methods=["GET", "POST", "OPTIONS"])
        def health():
            return jsonify({"ok": True})

        return app.test_client()

    def test_allows_the_configured_web_app_origin(self):
        client = self.make_client()

        response = client.get(
            "/api/health",
            headers={"Origin": "https://wlodek2106-cyber.github.io"},
        )

        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "https://wlodek2106-cyber.github.io",
        )

    def test_rejects_unconfigured_origins(self):
        client = self.make_client()

        response = client.get(
            "/api/health",
            headers={"Origin": "https://untrusted.example"},
        )

        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_preflight_is_limited_to_configured_origin_and_methods(self):
        client = self.make_client()

        response = client.options(
            "/api/health",
            headers={
                "Origin": "https://wlodek2106-cyber.github.io",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type",
            },
        )

        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "https://wlodek2106-cyber.github.io",
        )
        self.assertIn("POST", response.headers.get("Access-Control-Allow-Methods", ""))
        self.assertNotEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

    def test_rejects_wildcard_configuration(self):
        with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": "*"}):
            with self.assertRaisesRegex(RuntimeError, "Invalid CORS origin"):
                self.make_client()


if __name__ == "__main__":
    unittest.main()
