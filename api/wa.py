# api/wa.py
import os, json, re, base64, time
from urllib.parse import quote
from flask import Flask, request, redirect, jsonify
import requests

app = Flask(__name__)

# ====== ENV ======
PORTAL_WA_NUMBER = re.sub(r"\D+", "", os.getenv("PORTAL_WA_NUMBER", "+96597273411")) or "96597273411"
GS_WEBHOOK = (os.getenv("GS_WEBHOOK") or "").strip()
GS_SECRET  = (os.getenv("GS_SECRET") or "").strip()

def push_event(event_type: str, payload: dict):
    if not GS_WEBHOOK or not GS_SECRET:
        return
    rec = {"ts": int(time.time()), "event": event_type, **payload, "_secret": GS_SECRET}
    try:
        requests.post(GS_WEBHOOK, data=json.dumps(rec), timeout=4)
    except Exception:
        pass

def _client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    if xff and "," in xff:
        return xff.split(",")[0].strip()
    return xff or (request.remote_addr or "")

@app.get("/api/wa")
def wa_redirect():
    t = request.args.get("t", "")
    if not t:
        return jsonify({"ok": False, "error": "missing token"}), 400
    pad = "=" * (-len(t) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode((t + pad).encode()))
    except Exception:
        return jsonify({"ok": False, "error": "bad token"}), 400

    # log click
    push_event("whatsapp_click", {
        "user_id": data.get("user_id"),
        "username": data.get("username") or "",
        "teacher_id": data.get("teacher_id"),
        "ip": _client_ip(),
    })

    wa = re.sub(r"\D+", "", (data.get("wa") or "")) or PORTAL_WA_NUMBER
    text = data.get("text") or ""
    return redirect(f"https://wa.me/{wa}?text={quote(text)}", code=302)

# Fallback for other HTTP verbs
@app.route("/api/wa", methods=["POST", "PUT", "DELETE", "PATCH"])
def wa_methods():
    return jsonify({"ok": True})
