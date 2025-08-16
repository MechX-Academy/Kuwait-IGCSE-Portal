# api/wa.py
import os, json, base64, hmac, hashlib, html, re
from urllib.parse import quote
from flask import Flask, request, jsonify, make_response

app = Flask(__name__)

WA_SIGNING_SECRET = (os.getenv("WA_SIGNING_SECRET") or "").strip()
PORTAL_WA_NUMBER  = re.sub(r"\D+", "", os.getenv("PORTAL_WA_NUMBER", "+96597273411")) or "96597273411"

# (اختياري) Google Sheets analytics
GS_WEBHOOK = os.getenv("GS_WEBHOOK", "").strip()
GS_SECRET  = os.getenv("GS_SECRET", "").strip()

def push_event(event_type: str, payload: dict):
    if not (GS_WEBHOOK and GS_SECRET):
        return
    import requests, time
    rec = {"ts": int(time.time()), "event": event_type, **payload, "_secret": GS_SECRET}
    try:
        requests.post(GS_WEBHOOK, json=rec, timeout=4)
    except Exception as e:
        print("[ANALYTICS] push_event failed:", repr(e))

def _client_ip():
    return (request.headers.get("x-forwarded-for") or request.remote_addr or "").split(",")[0].strip()

# ✅ اسمع على كل المسارات الشائعة: "/" و"/api/wa" و أي مسار يتبعت
@app.route("/", defaults={"_path": ""}, methods=["GET"])
@app.route("/<path:_path>", methods=["GET"])
def wa_redirect(_path=""):
    """
    /api/wa?t=<base64>&sig=<hmac>
    التوكن t فيه: {"user_id","username","teacher_id","wa","text"}
    """
    t   = request.args.get("t", "")
    sig = request.args.get("sig", "")

    # Verify HMAC signature لو متضبوطة
    if WA_SIGNING_SECRET:
        good = hmac.new(WA_SIGNING_SECRET.encode("utf-8"), t.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, sig or ""):
            return jsonify(ok=False, error="bad signature"), 403

    # فكّ التوكن
    try:
        pad = "=" * (-len(t) % 4)
        raw = base64.urlsafe_b64decode((t + pad).encode("utf-8"))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return jsonify(ok=False, error="bad token"), 400

    # لوج اختياري
    try:
        push_event("whatsapp_click", {
            "user_id":   data.get("user_id"),
            "username":  data.get("username") or "",
            "teacher_id":data.get("teacher_id"),
            "ip":        _client_ip()
        })
    except Exception:
        pass

    wa  = re.sub(r"\D+", "", (data.get("wa") or "")) or PORTAL_WA_NUMBER
    txt = data.get("text") or ""
    target = f"https://wa.me/{wa}?text={quote(txt)}"

    # صفحة بانر + تحويل سريع
    html_page = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0.6;url={html.escape(target)}">
<title>Redirecting…</title>
<style>
  body {{ font-family: system-ui,-apple-system,Segoe UI,Roboto,Ubuntu; background:#0b0f13; color:#e5e7eb; display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
  .box {{ text-align:center; max-width:640px; padding:28px 32px; background:#111827; border:1px solid #1f2937; border-radius:14px; box-shadow:0 10px 40px rgba(0,0,0,.35); }}
  h1 {{ margin:0 0 10px; font-weight:700; font-size:22px; letter-spacing:.2px; }}
  p  {{ margin:8px 0 0; line-height:1.5; color:#9ca3af; }}
  a  {{ color:#93c5fd; text-decoration:none; }}
</style>
<script>
  setTimeout(function(){{ window.location.replace("{html.escape(target)}"); }}, 600);
</script>
</head><body>
  <div class="box">
    <h1>This App done by Dr.Eng. Ahmed Fathy</h1>
    <p>We're opening WhatsApp for you… If it doesn't open, <a href="{html.escape(target)}">tap here</a>.</p>
  </div>
</body></html>"""
    resp = make_response(html_page, 200)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp
