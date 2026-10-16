"""LOCAL TEST HARNESS ONLY: synthetic Telegram and Phantom, no live RPC."""
import atexit
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import base58
from flask import Flask, Response, jsonify, request, send_from_directory
from nacl.public import Box, PrivateKey, PublicKey
from solders.hash import Hash
from solders.keypair import Keypair
from solders.transaction import Transaction

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_NOW = int(time.time()) * 1000
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import wallet_service as service
from test_recovery_mobile import signed_data, TOKEN

folder = tempfile.TemporaryDirectory(prefix="zerolife-synthetic-e2e-")
atexit.register(folder.cleanup)
os.environ["BOT_TOKEN"] = TOKEN
os.environ["PRIVATE_KEY"] = ""  # Never use an inherited real signing key.
treasury, phantom = Keypair(), Keypair()
service.SHARED_POOL_WALLET = str(treasury.pubkey())


class SyntheticRpc:
    def get_balance(self, _):
        return SimpleNamespace(value=1_000_000_000)
    def get_account_info(self, _):
        return SimpleNamespace(value=True)
    def get_latest_blockhash(self):
        return SimpleNamespace(value=SimpleNamespace(blockhash=Hash.default()))
    def send_raw_transaction(self, raw):
        return SimpleNamespace(value=Transaction.from_bytes(raw).signatures[0])


service._rpc_client = lambda: SyntheticRpc()
service._onchain_balances = lambda _: {"solBalance": 1, "zrlBalance": 2, "zrlConfigured": True}
app = Flask(__name__)
service.register_wallet_routes(app, folder.name, admin_telegram_id="100")
db_path = str(Path(folder.name, "wallet_ledger.sqlite3"))
with service._connect(db_path) as db:
    for user, available, old_zrl, old_sol in [("100", 10, 50, 0), ("200", 25, 100, 10_000_000)]:
        service._ensure_account(db, user)
        service._ledger_entry(db, user, "fixture", available * 1_000_000, "seed-" + user)
        db.execute("""UPDATE wallet_accounts SET legacy_pending_units=?,
            legacy_pending_sol_lamports=?,legacy_recorded=1,sol_lamports=500000,
            registration_claimed=1 WHERE telegram_id=?""", (old_zrl * 1_000_000, old_sol, user))

OLD_FARMS = [
    {"id": "fixture-old-zrl", "asset": "zrl", "amount": 1000, "months": 12, "claimedReward": .25,
     "startTime": FIXTURE_NOW-86400000, "endTime": FIXTURE_NOW+86400000, "dailyRate": .001},
    {"id": "fixture-old-sol", "asset": "sol", "amount": .001, "months": 3, "claimedReward": .02,
     "startTime": FIXTURE_NOW-86400000, "endTime": FIXTURE_NOW+86400000, "dailyRate": 100},
]
# Reproduce the actual defect: old recovery closed both positions but only
# paid SOL principal. This temporary database is NOT the production database.
from test_recovery_mobile import RecoveryTests
fixture = RecoveryTests()
fixture.path, fixture.farms = db_path, OLD_FARMS
fixture.seed_incomplete_old_settlement(FIXTURE_NOW)


@app.get("/")
def home():
    user = "100" if request.args.get("user") == "100" else "200"
    old_farms = [] if user == "100" else OLD_FARMS
    stub = """
    <script>
    window.Telegram={WebApp:{
      initData:INIT_DATA,initDataUnsafe:{user:{id:USER_ID,username:'Fixture'+USER_ID}},
      ready(){},expand(){},isVersionAtLeast(){return true;},
      HapticFeedback:{impactOccurred(){},notificationOccurred(){},selectionChanged(){}},
      showAlert(message){document.getElementById('fixture-alert').textContent=message;},
      showConfirm(message,callback){callback(window.confirm(message));},
      openLink(url){document.getElementById('fixture-phantom').dataset.url=url;document.getElementById('fixture-phantom').hidden=false;}
    }};
    </script>
    """
    # Match the real profile namespace exactly, without modifying application code.
    source = ROOT.joinpath("dashboard.html").read_text()
    import re
    match = re.search(r"const STORAGE_PREFIX\s*=\s*([^;]+);", source)
    if match:
        # Expose the actual declaration below and seed after its evaluation,
        # before preservation runs.
        seed = "if(!localStorage.getItem(STORAGE_PREFIX+'fixture_seeded')){localStorage.setItem(STORAGE_PREFIX+'legacy_pending_farms',JSON.stringify(" + json.dumps(old_farms) + "));localStorage.setItem(STORAGE_PREFIX+'fixture_seeded','1');}"
        registered = "localStorage.setItem(STORAGE_PREFIX+'username','Fixture" + user + "');"
        source = source.replace(match.group(0), match.group(0) + registered + seed, 1)
    stub = stub.replace("INIT_DATA", json.dumps(signed_data(user))).replace("USER_ID", user).replace("FARMS", json.dumps(old_farms))
    source = source.replace('<script src="https://telegram.org/js/telegram-web-app.js"></script>', stub)
    source = source.replace("https://zer0life-ai-iz5n.onrender.com", "")
    controls = """<aside style="position:fixed;top:0;left:0;right:0;z-index:999999;background:#21321b;color:white;padding:5px;font-size:10px">
      SYNTHETIC TEST ONLY · <span id="fixture-alert"></span>
      <button id="fixture-phantom" hidden onclick="fetch('/__test__/phantom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:this.dataset.url})}).then(r=>r.json()).then(r=>{document.getElementById('fixture-alert').textContent=r.success?'Synthetic Phantom approved':r.error;this.hidden=true;window.phantomMobile.resume();})">Approve synthetic Phantom</button>
    </aside>"""
    return Response(source.replace("</body>", controls + "</body>"), mimetype="text/html")


@app.post("/__test__/phantom")
def approve_fixture_phantom():
    try:
        options = {k: v[0] for k, v in parse_qs(urlsplit(request.json["url"]).query).items()}
        peer = PrivateKey.generate()
        box = Box(peer, PublicKey(base58.b58decode(options["dapp_encryption_public_key"])))
        encrypted = box.encrypt(json.dumps({"public_key": str(phantom.pubkey()), "session": "synthetic-only"}).encode())
        target = urlsplit(options["redirect_link"])
        with app.test_client() as client:
            connected = client.get(target.path + "?" + target.query + "&" + urlencode({
                "phantom_encryption_public_key": base58.b58encode(bytes(peer.public_key)).decode(),
                "nonce": base58.b58encode(encrypted.nonce).decode(),
                "data": base58.b58encode(encrypted.ciphertext).decode(),
            }))
            sign = {k: v[0] for k, v in parse_qs(urlsplit(connected.location).query).items()}
            payload = json.loads(box.decrypt(base58.b58decode(sign["payload"]), base58.b58decode(sign["nonce"])))
            signature = phantom.sign_message(base58.b58decode(payload["message"]))
            encrypted = box.encrypt(json.dumps({"signature": base58.b58encode(bytes(signature)).decode()}).encode())
            target = urlsplit(sign["redirect_link"])
            client.get(target.path + "?" + target.query + "&" + urlencode({
                "nonce": base58.b58encode(encrypted.nonce).decode(), "data": base58.b58encode(encrypted.ciphertext).decode(),
            }))
        return jsonify(success=True)
    except Exception:
        return jsonify(success=False, error="Synthetic Phantom approval failed"), 400


@app.get("/<path:name>")
def static_file(name):
    if name == "wallet.js":
        return Response(ROOT.joinpath(name).read_text().replace(
            "https://zer0life-ai-iz5n.onrender.com", ""), mimetype="text/javascript")
    if name in {"legacy-farms.js", "legacy-recovery.js", "recovery-actions.js", "phantom-mobile.js"} or name.startswith("assets/"):
        return send_from_directory(ROOT, name)
    return jsonify(success=True, leaderboard=[])


@app.post("/<path:name>")
def unused_fixture_api(name):
    return jsonify(success=True)


if __name__ == "__main__":
    # Realistic transaction construction, synthetic treasury and RPC only.
    os.environ["PRIVATE_KEY"] = base58.b58encode(bytes(treasury)).decode()
    os.environ["ZRL_MINT"] = str(Keypair().pubkey())
    app.run(host="127.0.0.1", port=4084, threaded=True, debug=False)
