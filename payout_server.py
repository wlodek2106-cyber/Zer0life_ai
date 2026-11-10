"""Fail-closed compatibility entrypoint for the retired legacy API.

Use bot.py for the supported Telegram application and authenticated /wallet APIs.
Importing this module must not open/migrate databases, start farming workers,
create Solana clients, or send Telegram messages. Historical stores stay untouched.
"""

import os

from flask import Flask, jsonify

from security_config import configure_cors

app = Flask(__name__, static_folder=None)
configure_cors(app)

RETIREMENT_MESSAGE = (
    "This legacy API is retired. Open the current Zer0Life Run Telegram app. "
    "The supported server entrypoint is bot.py; legacy financial and chat operations are disabled."
)


@app.after_request
def retirement_headers(response):
    response.headers["X-Zer0Life-Legacy-API"] = "retired-no-data-access-1"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def retirement_health():
    # Infrastructure reachability is not a successful game operation.
    return jsonify(success=False, status="retired", error=RETIREMENT_MESSAGE)


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def retired_legacy_route(path):
    return jsonify(success=False, status="retired", error=RETIREMENT_MESSAGE), 410


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
