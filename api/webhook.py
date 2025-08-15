# api/webhook.py
import os, json, re, time, html, traceback, base64, hmac, hashlib, pathlib
from typing import Dict, Any, List, Tuple, Set
from flask import Flask, request, jsonify, redirect
import requests

app = Flask(__name__)
BUILD_TAG = "kuwait-igcse-portal-v3.3-sec-per-subject-prefs"

# =========================
# Config / Env
# =========================
TELEGRAM_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
BOT_USERNAME   = (os.getenv("BOT_USERNAME") or "").strip()  # e.g. kuwait_igcse_portal_bot
BOT_API        = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL", "https://kuwait-igcse-portal-nu.vercel.app") or "").rstrip("/")
PORTAL_WA_NUMBER = re.sub(r"\D+", "", os.getenv("PORTAL_WA_NUMBER", "+96597273411")) or "96597273411"

# Google Sheets webhook (+ SECRET) for analytics
GS_WEBHOOK = (os.getenv("GS_WEBHOOK") or "").strip()
GS_SECRET  = (os.getenv("GS_SECRET")  or "").strip()

# Optional: verify Telegram header (set it when you setWebhook)
TELEGRAM_WEBHOOK_SECRET = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()

# Deep-link tokens & WA redirect HMAC
DEEPLINK_SIGNING_SECRET = (os.getenv("DEEPLINK_SIGNING_SECRET") or "").encode()
WA_SIGNING_SECRET       = (os.getenv("WA_SIGNING_SECRET") or "").encode()

# Admin log chat
ADMIN_LOG_CHAT_ID = int(os.getenv("ADMIN_LOG_CHAT_ID", "0") or 0)

# Allow/Block lists (optional)
ALLOWED_CHAT_TYPES = set((os.getenv("ALLOWED_CHAT_TYPES","private,group,supergroup").split(",")))
ALLOWED_GROUP_IDS  = set(int(x) for x in (os.getenv("ALLOWED_GROUP_IDS","").split(",")) if x)
BLOCKED_USER_IDS   = set(int(x) for x in (os.getenv("BLOCKED_USER_IDS","").split(",")) if x)

# Debug raw updates to GS?
LOG_RAW_UPDATES = os.getenv("LOG_RAW_UPDATES","0") == "1"

# =========================
# Utils
# =========================
def tg(method: str, payload: Dict[str, Any]):
    if not BOT_API:
        print(f"[TG] skip {method} (no token)")
        return None
    try:
        r = requests.post(f"{BOT_API}/{method}", json=payload, timeout=20)
        try:
            j = r.json()
        except Exception:
            j = {}
        if r.status_code != 200 or (isinstance(j, dict) and not j.get("ok", True)):
            print(f"[TG ERR] {method} {r.status_code} -> {r.text[:500]}")
        else:
            print(f"[TG OK] {method}")
        return r
    except Exception as e:
        print("[TG EXC]", method, repr(e))
        return None

def push_event(event_type: str, payload: Dict[str, Any]):
    if not GS_WEBHOOK or not GS_SECRET:
        return
    rec = {"ts": int(time.time()), "event": event_type, **payload, "_secret": GS_SECRET}
    try:
        requests.post(GS_WEBHOOK, data=json.dumps(rec), timeout=4)
    except Exception as e:
        print("[ANALYTICS] push_event failed:", repr(e))

def admin_log(text: str):
    if ADMIN_LOG_CHAT_ID:
        tg("sendMessage", {"chat_id": ADMIN_LOG_CHAT_ID, "text": text[:3900]})

def _now_ts() -> int:
    return int(time.time())

def _client_ip() -> str:
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.remote_addr or ""

def _client_ua() -> str:
    return request.headers.get("user-agent","")

def verify_tg_header() -> bool:
    if not TELEGRAM_WEBHOOK_SECRET:
        return True
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token","")
    return hmac.compare_digest(got, TELEGRAM_WEBHOOK_SECRET)

def chat_allowed(msg) -> bool:
    ctype = msg["chat"]["type"]
    if ctype not in ALLOWED_CHAT_TYPES:
        return False
    if ctype in ("group","supergroup") and ALLOWED_GROUP_IDS:
        return msg["chat"]["id"] in ALLOWED_GROUP_IDS
    return True

def h(x: str) -> str:
    return html.escape(x or "")

# =========================
# Deep links (signed)
# =========================
def _b64u(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).decode().rstrip("=")

def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode())

def make_deeplink(campaign_id: str, extra: dict=None) -> str:
    payload = {"cid": campaign_id, "ts": _now_ts()}
    if extra:
        payload.update(extra)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    sig = hmac.new(DEEPLINK_SIGNING_SECRET, raw, hashlib.sha256).digest()
    token = _b64u(raw) + "." + _b64u(sig)
    return f"https://t.me/{BOT_USERNAME}?start={token}"

def verify_deeplink_token(token: str) -> dict|None:
    try:
        raw_b64, sig_b64 = token.split(".", 1)
        raw = _b64u_dec(raw_b64)
        sig = _b64u_dec(sig_b64)
        good = hmac.compare_digest(sig, hmac.new(DEEPLINK_SIGNING_SECRET, raw, hashlib.sha256).digest())
        if not good:
            return None
        return json.loads(raw.decode())
    except Exception:
        return None

# =========================
# Signed WA redirect
# =========================
def sign_wa_token(obj: dict) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",",":")).encode()
    sig = hmac.new(WA_SIGNING_SECRET, raw, hashlib.sha256).digest()
    return _b64u(raw) + "." + _b64u(sig)

def unsign_wa_token(tok: str) -> dict|None:
    try:
        raw_b64, sig_b64 = tok.split(".", 1)
        raw = _b64u_dec(raw_b64)
        sig = _b64u_dec(sig_b64)
        good = hmac.compare_digest(sig, hmac.new(WA_SIGNING_SECRET, raw, hashlib.sha256).digest())
        if not good: return None
        return json.loads(raw.decode())
    except Exception:
        return None

def build_wa_redirect_link(user_id, username, teacher_id, wa_number, prefill_text):
    payload = {
        "user_id": user_id,
        "username": username or "",
        "teacher_id": teacher_id,
        "wa": re.sub(r"\D+", "", wa_number or "") or PORTAL_WA_NUMBER,
        "text": prefill_text,
        "ts": _now_ts()
    }
    token = sign_wa_token(payload)
    return f"{PUBLIC_BASE_URL}/api/wa?t={token}"

# =========================
# Data loading + integrity
# =========================
DATA_PATH = os.path.join(os.path.dirname(__file__), "teachers.json")
try:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TEACHERS = json.load(f)
    try:
        TEACHERS_HASH = hashlib.sha256(pathlib.Path(DATA_PATH).read_bytes()).hexdigest()
        print("teachers.json sha256:", TEACHERS_HASH)
        push_event("boot_hash", {"teachers_sha256": TEACHERS_HASH})
    except Exception:
        TEACHERS_HASH = ""
    print(f"Loaded {len(TEACHERS)} teachers from {DATA_PATH}.")
except Exception as e:
    print(f"ERROR loading teachers.json: {e}")
    TEACHERS = []
    TEACHERS_HASH = ""

# =========================
# Subjects dictionaries
# =========================
VALID_SUBJECTS = {
    # Split Core vs AS/A Level for similar subjects
    "math (core)": [
        "math (core)", "mathematics (core)", "maths (core)",
        "additional math (core)", "further math (core)", "igcse mathematics (core)"
    ],
    "math (as/a level)": [
        "math (as/a level)", "mathematics (as/a level)", "maths (as/a level)",
        "additional math (as/a level)", "further math (as/a level)", "igcse mathematics (as/a level)"
    ],
    "physics (core)": ["physics (core)", "phys (core)"],
    "physics (as/a level)": ["physics (as/a level)", "phys (as/a level)"],
    "chemistry (core)": ["chemistry (core)", "chem (core)"],
    "chemistry (as/a level)": ["chemistry (as/a level)", "chem (as/a level)"],
    "biology (core)": ["biology (core)", "bio (core)"],
    "biology (as/a level)": ["biology (as/a level)", "bio (as/a level)"],

    # English
    "english language": ["english", "english language", "esl", "first language english", "second language english", "english sl"],
    "english literature": ["english literature", "literature", "english fl"],

    # Business / Economics
    "business (core)": ["business (core)", "business studies (core)"],
    "business (as/a level)": ["business (as/a level)", "business studies (as/a level)"],
    "economics (core)": ["economics (core)", "econ (core)"],
    "economics (as/a level)": ["economics (as/a level)", "econ (as/a level)"],
    "psychology (as/a level)": ["psychology (as/a level)", "psy (as/a level)"],

    # Others
    "accounting": ["accounting", "accounts"],
    "geography": ["geography", "geo"],
    "history": ["history"],
    "arabic": ["arabic", "arabic first language", "arabic foreign language"],
    "french": ["french"],
    "german": ["german"],
    "spanish": ["spanish"],
    "sociology": ["sociology"],
    "humanities & social sciences": ["humanities", "social sciences"],
    "combined science": ["combined science", "double award science", "coordinated science"],
    "environmental management": ["environmental management", "em"],
    "physical education": ["pe", "physical education"],
    "travel & tourism": ["travel & tourism", "travel", "tourism"],
    "computer science": ["computer science", "cs"],
    "ict": ["ict", "information and communication technology"],
}

SUBJECT_GROUPS: Dict[str, List[Tuple[str, str]]] = {
    "Core subjects": [
        ("MTH_CORE", "Math (Core)"),
        ("PHY_CORE", "Physics (Core)"),
        ("CHE_CORE", "Chemistry (Core)"),
        ("BIO_CORE", "Biology (Core)"),
        ("ENL", "English SL"),
        ("ENLIT", "English FL"),
        ("BUS_CORE", "Business (Core)"),
        ("ECO_CORE", "Economics (Core)"),
        ("ACC", "Accounting"),
        ("SOC", "Sociology"),
    ],
    "Languages": [
        ("FR", "French"),
        ("DE", "German"),
        ("AR", "Arabic"),
    ],
    "Creative & Technical": [
        ("ICT", "ICT"),
        ("CS",  "Computer Science"),
    ],
    "Other options": [
        ("EM", "Environmental Management"),
        ("PE", "Physical Education"),
        ("COMSCI", "Combined Science"),
        ("TT", "Travel & Tourism"),
    ],
    "Cambridge & Edexcel AS & A Level Subjects": [
        ("MTH_ALEVEL", "Math (AS/A Level)"),
        ("PHY_ALEVEL", "Physics (AS/A Level)"),
        ("CHE_ALEVEL", "Chemistry (AS/A Level)"),
        ("BIO_ALEVEL", "Biology (AS/A Level)"),
        ("BUS_ALEVEL", "Business (AS/A Level)"),
        ("ECO_ALEVEL", "Economics (AS/A Level)"),
        ("PSY_ALEVEL", "Psychology (AS/A Level)"),
        ("SOC", "Sociology"),
        ("ENLIT", "English Literature"),
    ],
}

CODE_TO_SUBJECT = {
    "MTH_CORE": "Math (Core)",
    "MTH_ALEVEL": "Math (AS/A Level)",
    "PHY_CORE": "Physics (Core)",
    "PHY_ALEVEL": "Physics (AS/A Level)",
    "CHE_CORE": "Chemistry (Core)",
    "CHE_ALEVEL": "Chemistry (AS/A Level)",
    "BIO_CORE": "Biology (Core)",
    "BIO_ALEVEL": "Biology (AS/A Level)",
    "ENL": "English SL",
    "ENLIT": "English FL",
    "BUS_CORE": "Business (Core)",
    "BUS_ALEVEL": "Business (AS/A Level)",
    "ECO_CORE": "Economics (Core)",
    "ECO_ALEVEL": "Economics (AS/A Level)",
    "PSY_ALEVEL": "Psychology (AS/A Level)",
    "ACC": "Accounting",
    "SOC": "Sociology",
    "FR": "French",
    "DE": "German",
    "AR": "Arabic",
    "ICT": "ICT",
    "CS": "Computer Science",
    "COMSCI": "Combined Science",
    "EM": "Environmental Management",
    "PE": "Physical Education",
    "TT": "Travel & Tourism",
}

BOARD_CODES = {"C": "Cambridge", "E": "Edexcel", "O": "OxfordAQA"}

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _nice_subject_name(key: str) -> str:
    nice = {
        "ict": "ICT",
        "cs": "Computer Science",
        "pe": "Physical Education",
        "english language": "English Language",
        "english literature": "English Literature",
    }
    return nice.get(key, key.title())

def canonical_subject(label: str) -> str | None:
    t = _norm(label)
    if not t:
        return None
    t_clean = re.sub(r"[^a-z0-9\s&()/-]+", " ", t)
    t_clean = re.sub(r"\s+", " ", t_clean).strip()
    for canonical, aliases in VALID_SUBJECTS.items():
        pool = [canonical] + aliases
        pool_norm = [_norm(x) for x in pool]
        if any(t_clean == p for p in pool_norm):
            return _nice_subject_name(canonical.lower())
        for alias in pool_norm:
            if re.search(rf"\b{re.escape(alias)}\b", t_clean):
                return _nice_subject_name(canonical.lower())
    print("[WARN] canonical_subject: no match for", label)
    return None

def teacher_has_subject(teacher_subjects: List[str], wanted_label: str) -> bool:
    wanted = canonical_subject(wanted_label)
    if not wanted:
        return False
    for s in teacher_subjects or []:
        c = canonical_subject(s)
        if c == wanted:
            return True
    return False

def canonical_board(label: str) -> str:
    t = _norm(label)
    if t in ("o", "oxford", "oxfordaqa", "oxford aqa"):
        return "oxfordaqa"
    if t in ("c", "cambridge"):
        return "cambridge"
    if t in ("e", "edexcel", "pearson edexcel", "pearson"):
        return "edexcel"
    return t or ""

# Precompute canonical fields
for t in TEACHERS:
    subj = t.get("subjects", []) or []
    t["_subjects_canon"] = set()
    for s in subj:
        c = canonical_subject(s)
        if c:
            t["_subjects_canon"].add(c)
    t["_boards_canon"] = [canonical_board(b) for b in (t.get("boards") or [])]

def match_teachers(subject=None, grade=None, board=None, limit=4):
    board_can = canonical_board(board) if board else ""
    results = []
    debug_why = []
    for t in TEACHERS:
        ok = True
        why = []

        if subject and not teacher_has_subject(t.get("subjects", []), subject):
            ok = False; why.append(f"subject_mismatch: wanted={subject}, ts={t.get('subjects')}")
        if grade is not None:
            grades = t.get("grades") or []
            if grade not in grades:
                ok = False; why.append(f"grade_mismatch: wanted={grade}, tg={grades}")
        if board_can:
            if board_can not in (t.get("_boards_canon") or []):
                ok = False; why.append(f"board_mismatch: wanted={board_can}, tb={(t.get('_boards_canon'))}")

        if ok:
            results.append(t)
        else:
            debug_why.append({"teacher": t.get("name"), "reasons": why})

    if not results:
        print("[DEBUG NO MATCH]", json.dumps(debug_why[:6], ensure_ascii=False))
        admin_log("⚠️ No match:\n" + json.dumps(debug_why[:6], ensure_ascii=False))

    results.sort(key=lambda tt: tt.get("name", "").lower())
    return results[:limit]

def collect_best_matches(subjects: List[str], grade: int, board: str, k: int = 4) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for s in subjects:
        for t in match_teachers(s, grade, board, limit=3):
            tid = t.get("id") or t["name"]
            if tid in seen:
                continue
            seen.add(tid)
            out.append(t)
            if len(out) >= k:
                return out
    return out

# =========================
# Keyboards
# =========================
def kb_with_restart(markup: Dict[str, Any] | None) -> Dict[str, Any]:
    if not markup:
        markup = {"inline_keyboard": []}
    rows = markup.get("inline_keyboard", [])
    rows.append([{"text": "⟲ Restart", "callback_data": "FORCE_RESTART"}])
    return {"inline_keyboard": rows}

def encode_sel(sel: Set[str]) -> str:
    return ".".join(sorted(sel)) if sel else ""

def decode_sel(s: str) -> Set[str]:
    return set([x for x in s.split(".") if x])

def kb_board():
    return {"inline_keyboard": [[
        {"text": "Cambridge", "callback_data": "B|C"},
        {"text": "Edexcel",   "callback_data": "B|E"},
        {"text": "OxfordAQA", "callback_data": "B|O"},
    ]]}

def kb_grade(board_code: str):
    rows, row = [], []
    for g in range(7, 13):
        row.append({"text": f"{g}", "callback_data": f"G|{g}|{board_code}"})
        if len(row) == 4:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([{"text": "⬅️ Back", "callback_data": "B|"+board_code}])
    return {"inline_keyboard": rows}

def kb_subjects(board_code: str, grade: int, sel: Set[str]):
    rows = []
    def tick(code): return "✅" if code in sel else "☐"
    for group, items in SUBJECT_GROUPS.items():
        rows.append([{"text": f"— {group} —", "callback_data": "noop"}])
        for i in range(0, len(items), 2):
            row = []
            for code, label in items[i:i+2]:
                row.append({
                    "text": f"{tick(code)} {label}",
                    "callback_data": f"T|{code}|{board_code}|{grade}|{encode_sel(sel)}"
                })
            rows.append(row)
    rows.append([
        {"text": "Done ✅", "callback_data": f"D|{board_code}|{grade}|{encode_sel(sel)}"},
        {"text": "Reset ↩️", "callback_data": f"T|__RESET__|{board_code}|{grade}|{encode_sel(sel)}"},
    ])
    rows.append([{"text": "⬅️ Back", "callback_data": f"G|{grade}|{board_code}"}])
    return {"inline_keyboard": rows}

def summary_text(board_code: str, grade: int, sel: Set[str]) -> str:
    board = BOARD_CODES.get(board_code, board_code)
    chosen = ", ".join(h(CODE_TO_SUBJECT[c]) for c in sorted(sel)) if sel else "—"
    return (f"<b>Step 3/3 – Subjects</b>\n"
            f"Board: <b>{h(board)}</b>   |   Grade: <b>{grade}</b>\n"
            f"Pick one or more subjects, then press <b>Done</b>.\n"
            f"Selected: {chosen}")

def kb_mode():
    return {"inline_keyboard": [[
        {"text": "👤 One-to-One", "callback_data": "MODE|1:1"},
        {"text": "👥 Group",      "callback_data": "MODE|group"},
    ],[
        {"text": "⟲ Restart", "callback_data": "FORCE_RESTART"}
    ]]}

def kb_lpw():
    return {
        "inline_keyboard": [[
            {"text": "1/week", "callback_data": "LPW|1"},
            {"text": "2/week", "callback_data": "LPW|2"},
        ],[
            {"text": "⟲ Restart", "callback_data": "FORCE_RESTART"}
        ]]
    }

def kb_select_teachers(matches: List[Dict[str, Any]], selected_ids: Set[str]):
    rows = []
    def tick(tid): return "✅" if tid in selected_ids else "☐"
    for t in matches:
        rows.append([{
            "text": f"{tick(t['id'])} {t['name']}",
            "callback_data": f"SEL_TEACHER|{t['id']}"
        }])
    if not rows:
        rows.append([{"text": "No matching results", "callback_data": "noop"}])
    rows.append([{"text": "📩 Send WhatsApp Link", "callback_data": "SEND_WA"}])
    rows.append([{"text": "➕ Add more subjects", "callback_data": "ADD_MORE"}])
    return {"inline_keyboard": rows}

# =========================
# Session / Idempotency
# =========================
SESSIONS: Dict[int, Dict[str, Any]] = {}
RECENT_DONE: Dict[int, List[Tuple[str, float]]] = {}

def session(chat_id: int) -> Dict[str, Any]:
    if chat_id not in SESSIONS:
        SESSIONS[chat_id] = {
            "stage": "idle",
            "name": "",
            "selections": [],  # [{board_code, grade, subjects:[...], prefs:{ subj: {mode, lpw} }}]
        }
    return SESSIONS[chat_id]

def already_done(chat_id: int, signature: str, ttl: int = 300) -> bool:
    now = time.time()
    lst = RECENT_DONE.get(chat_id, [])
    lst = [(k, t) for (k, t) in lst if now - t < ttl]
    RECENT_DONE[chat_id] = lst
    for k, _ in lst:
        if k == signature:
            return True
    lst.append((signature, now))
    RECENT_DONE[chat_id] = lst
    return False

# =========================
# Routes
# =========================
@app.get("/api/webhook")
def ping():
    return jsonify(ok=True, build=BUILD_TAG, teachers=len(TEACHERS), bot=bool(BOT_API), hash=TEACHERS_HASH != "")

@app.get("/api/wa")
def wa_redirect():
    t = request.args.get("t", "")
    data = unsign_wa_token(t)
    if not data:
        return "Bad token", 400

    ip = _client_ip()
    ua = _client_ua()
    push_event("whatsapp_click", {
        "user_id": data.get("user_id"),
        "username": data.get("username") or "",
        "teacher_id": data.get("teacher_id"),
        "ip": ip, "ua": ua
    })

    wa = re.sub(r"\D+", "", data.get("wa", "") or "") or PORTAL_WA_NUMBER
    text = data.get("text", "") or ""
    return redirect(f"https://wa.me/{wa}?text={requests.utils.requote_uri(text)}", code=302)

def _handle_webhook():
    try:
        if request.method == "POST" and not verify_tg_header():
            push_event("bad_header", {"ip": _client_ip()})
            return jsonify({"ok": True}), 403

        update = request.get_json(force=True, silent=True) or {}
        if LOG_RAW_UPDATES:
            push_event("trace_update", {"ip": _client_ip(), "ua": _client_ua(), "raw": update})
        print("[UPDATE]", json.dumps(update)[:2000])

        # --------- Callback queries ---------
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            msg_id  = cq["message"]["message_id"]
            data    = cq.get("data", "")
            tg("answerCallbackQuery", {"callback_query_id": cq["id"]})

            user_obj = cq.get("from", {}) or {}
            user_id = user_obj.get("id")
            username = user_obj.get("username") or ""

            if data == "noop":
                return jsonify({"ok": True})

            # Restart
            if data == "FORCE_RESTART":
                SESSIONS[chat_id] = {"stage": "ask_name", "name": "", "selections": []}
                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": "👋 Welcome to Kuwait IGCSE Portal!\nPlease type your full name (student):",
                    "reply_markup": kb_with_restart({"inline_keyboard": []})
                })
                push_event("restart", {"user_id": user_id, "username": username})
                admin_log(f"⟲ Restart by @{username or user_id}")
                return jsonify({"ok": True})

            # Board
            if data.startswith("B|"):
                b = data.split("|", 1)[1]
                s = session(chat_id)
                s["board_code"] = b
                push_event("board", {"user_id": user_id, "username": username, "board": BOARD_CODES.get(b,b)})

                if isinstance(s.get("grade"), int):
                    g = s["grade"]
                    sel = set()
                    tg("editMessageText", {
                        "chat_id": chat_id, "message_id": msg_id,
                        "text": summary_text(b, g, sel),
                        "parse_mode": "HTML",
                        "reply_markup": kb_with_restart(kb_subjects(b, g, sel))
                    })
                    return jsonify({"ok": True})

                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": "🔢 <b>Step 2/3 – Grade</b>\nSelect your current grade:",
                    "parse_mode": "HTML",
                    "reply_markup": kb_with_restart(kb_grade(b))
                })
                return jsonify({"ok": True})

            # Grade
            if data.startswith("G|"):
                _, g, b = data.split("|", 2)
                g = int(g)
                s = session(chat_id)
                s["board_code"] = b
                s["grade"] = g
                sel = set()
                push_event("grade", {"user_id": user_id, "username": username, "board": BOARD_CODES.get(b,b), "grade": g})

                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": summary_text(b, g, sel),
                    "parse_mode": "HTML",
                    "reply_markup": kb_with_restart(kb_subjects(b, g, sel))
                })
                return jsonify({"ok": True})

            # Toggle Subject
            if data.startswith("T|"):
                _, code, b, g, enc = data.split("|", 4)
                g = int(g)
                sel = decode_sel(enc)
                if code == "__RESET__":
                    sel = set()
                else:
                    if code in sel: sel.remove(code)
                    else: sel.add(code)
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": summary_text(b, g, sel),
                    "parse_mode": "HTML",
                    "reply_markup": kb_with_restart(kb_subjects(b, g, sel))
                })
                return jsonify({"ok": True})

            # Done selecting subjects  ----------------
            if data.startswith("D|"):
                _, b, g, enc = data.split("|", 3)
                g = int(g)
                sel_codes = [x for x in enc.split(".") if x]
                if not sel_codes:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Please select at least one subject."})
                    return jsonify({"ok": True})

                s = session(chat_id)
                selection = {
                    "board_code": b,
                    "grade": g,
                    "subjects": [CODE_TO_SUBJECT[c] for c in sel_codes],
                    "prefs": {}  # per-subject prefs
                }
                s.setdefault("selections", []).append(selection)

                admin_log(f"✅ Selection: @{username or user_id} | {BOARD_CODES.get(b,b)} G{g} | {', '.join(selection['subjects'])}")

                push_event("selection", {
                    "user_id": user_id, "username": username,
                    "board": BOARD_CODES.get(b,b), "grade": g,
                    "subjects": selection["subjects"]
                })

                # Start per-subject Q&A flow
                s["pref_flow"] = {
                    "sel_idx": len(s["selections"]) - 1,
                    "i": 0,
                    "subjects": selection["subjects"],
                    "current_mode": None
                }
                cur_subj = s["pref_flow"]["subjects"][0]
                s["stage"] = "ask_mode_per_subject"
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": f"🎯 Lesson type for <b>{h(cur_subj)}</b>?",
                    "parse_mode": "HTML",
                    "reply_markup": kb_mode()
                })
                return jsonify({"ok": True})

            # MODE per subject ------------------------
            if data.startswith("MODE|"):
                _, mode = data.split("|", 1)
                s = session(chat_id)
                pf = s.get("pref_flow")
                if not pf:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "No subject is pending."})
                    return jsonify({"ok": True})

                pf["current_mode"] = mode
                cur_subj = pf["subjects"][pf["i"]]
                s["stage"] = "ask_lpw_per_subject"
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": f"🗓️ Lessons/week for <b>{h(cur_subj)}</b>?",
                    "parse_mode": "HTML",
                    "reply_markup": kb_lpw()
                })
                return jsonify({"ok": True})

            # LPW per subject -------------------------
            if data.startswith("LPW|"):
                _, n = data.split("|", 1)
                s = session(chat_id)
                pf = s.get("pref_flow")
                if not pf:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "No subject is pending."})
                    return jsonify({"ok": True})

                try:
                    n_int = int(n)
                    if n_int not in (1, 2):
                        n_int = 1
                except:
                    n_int = 1

                sel = s["selections"][pf["sel_idx"]]
                cur_subj = pf["subjects"][pf["i"]]
                sel.setdefault("prefs", {})[cur_subj] = {"mode": pf["current_mode"], "lpw": n_int}
                push_event("subject_pref", {
                    "user_id": user_id, "username": username,
                    "board": BOARD_CODES.get(sel["board_code"], sel["board_code"]),
                    "grade": sel["grade"],
                    "subject": cur_subj,
                    "mode": pf["current_mode"],
                    "lessons_per_week": n_int
                })

                pf["i"] += 1
                if pf["i"] < len(pf["subjects"]):
                    next_subj = pf["subjects"][pf["i"]]
                    pf["current_mode"] = None
                    s["stage"] = "ask_mode_per_subject"
                    tg("editMessageText", {
                        "chat_id": chat_id, "message_id": msg_id,
                        "text": f"🎯 Lesson type for <b>{h(next_subj)}</b>?",
                        "parse_mode": "HTML",
                        "reply_markup": kb_mode()
                    })
                    return jsonify({"ok": True})
                else:
                    s["pref_flow"] = None
                    s["stage"] = "flow"
                    tg("editMessageText", {
                        "chat_id": chat_id, "message_id": msg_id,
                        "text": ("Preferences saved ✅\n"
                                 "You can add more selections or show tutors."),
                        "reply_markup": kb_with_restart({
                            "inline_keyboard": [
                                [{"text": "➕ Add more", "callback_data": "ADD_MORE"}],
                                [{"text": "🚀 Show tutors", "callback_data": "SHOW_ALL"}]
                            ]
                        })
                    })
                    return jsonify({"ok": True})

            # Add more
            if data == "ADD_MORE":
                tg("editMessageText", {
                    "chat_id": chat_id, "message_id": msg_id,
                    "text": "🧭 <b>Step 1/3 – Board</b>\nChoose the board for the new selection:",
                    "parse_mode": "HTML",
                    "reply_markup": kb_with_restart(kb_board())
                })
                return jsonify({"ok": True})

            # Show Tutors
            if data == "SHOW_ALL":
                s = session(chat_id)
                selections = s.get("selections", [])
                if not selections:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "No selections yet."})
                    return jsonify({"ok": True})

                per_teacher_map: Dict[str, Dict[str, Any]] = {}
                ordered_cards: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

                for sel in selections:
                    board_name_display = BOARD_CODES.get(sel["board_code"], sel["board_code"])
                    matches = collect_best_matches(sel["subjects"], sel["grade"], board_name_display, k=4)
                    for t in matches:
                        tid = t.get("id") or t["name"]
                        if not any((t2.get("id") or t2["name"]) == tid for (t2, _) in ordered_cards):
                            ordered_cards.append((t, sel))
                        entry = per_teacher_map.setdefault(tid, {"id": tid, "name": t["name"], "parts": []})
                        entry["parts"].append({
                            "subjects": sel["subjects"],
                            "board": board_name_display,
                            "grade": sel["grade"],
                            "prefs": sel.get("prefs", {})
                        })

                student_name = s.get("name") or "Student"
                for t, sel in ordered_cards:
                    caption = format_teacher_caption_html(
                        t, student_name,
                        BOARD_CODES.get(sel["board_code"], sel["board_code"]),
                        sel["grade"],
                        sel["subjects"]
                    )
                    photo = t.get("photo_url")
                    if photo:
                        tg("sendPhoto", {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML"})
                    else:
                        tg("sendMessage", {"chat_id": chat_id, "text": caption, "parse_mode": "HTML"})

                s["last_matches"] = [{"id": v["id"], "name": v["name"]} for v in per_teacher_map.values()]
                s["per_teacher_map"] = per_teacher_map
                s["selected_teachers"] = set()

                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": "Select the tutors you're interested in, then press <b>📩 Send WhatsApp Link</b>.",
                    "parse_mode": "HTML",
                    "reply_markup": kb_with_restart(kb_select_teachers(s["last_matches"], s["selected_teachers"]))
                })
                push_event("show_tutors", {"user_id": user_id, "username": username})
                admin_log(f"📋 Show tutors for @{username or user_id}")
                return jsonify({"ok": True})

            # Toggle teacher selection
            if data.startswith("SEL_TEACHER|"):
                _, tid = data.split("|", 1)
                s = session(chat_id)
                sel_ids: Set[str] = s.setdefault("selected_teachers", set())
                if tid in sel_ids: sel_ids.remove(tid)
                else: sel_ids.add(tid)
                rows = kb_select_teachers(s.get("last_matches", []), sel_ids)
                tg("editMessageReplyMarkup", {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "reply_markup": kb_with_restart(rows)
                })
                return jsonify({"ok": True})

            # Send WA link
            if data == "SEND_WA":
                s = session(chat_id)
                sel_ids: Set[str] = s.get("selected_teachers", set())
                if not sel_ids:
                    tg("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Pick at least one tutor."})
                    return jsonify({"ok": True})

                per_teacher_map = s.get("per_teacher_map", {})
                chosen = [per_teacher_map[tid] for tid in sel_ids if tid in per_teacher_map]

                def fmt_pref(p):
                    if not p: return ""
                    m = "1:1" if p.get("mode") == "1:1" else ("Group" if p.get("mode") == "group" else None)
                    w = p.get("lpw")
                    parts = []
                    if m: parts.append(m)
                    if w: parts.append(f"{w}/wk")
                    return f" [{', '.join(parts)}]" if parts else ""

                msg_lines = [f"Hello, this is {s.get('name','Student')}.\nI'm interested in the following:"]
                for item in chosen:
                    name = item["name"]
                    sub_lines = []
                    for part in item["parts"]:
                        board = part["board"]
                        grade = part["grade"]
                        prefs = part.get("prefs", {})
                        subj_bits = []
                        for subj in part.get("subjects", []):
                            subj_bits.append(f"{subj}{fmt_pref(prefs.get(subj))}")
                        if subj_bits:
                            sub_lines.append(f"{', '.join(subj_bits)} - {board} Grade {grade}")
                    if sub_lines:
                        msg_lines.append(f"- {name} ({' | '.join(sub_lines)})")

                msg_lines.append("Could you please share availability and fees?")
                final_msg = "\n".join(msg_lines)

                wa_link = build_wa_redirect_link(
                    user_id=user_id,
                    username=username,
                    teacher_id=None,
                    wa_number=PORTAL_WA_NUMBER,
                    prefill_text=final_msg
                )

                push_event("send_wa", {"user_id": user_id, "username": username, "selections": s.get("selections", [])})
                admin_log(f"📨 SEND_WA by @{username or user_id}\n{final_msg[:800]}")

                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": f'<a href="{wa_link}">📩 Open WhatsApp</a>',
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": kb_with_restart({"inline_keyboard": []})
                })
                return jsonify({"ok": True})

            return jsonify({"ok": True})

        # --------- Normal messages (/start, name, fallback) ---------
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})

        # basic meta
        if not chat_allowed(msg):
            push_event("blocked_chat", {"chat_id": msg["chat"]["id"], "type": msg["chat"]["type"]})
            return jsonify({"ok": True})

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()

        u = msg.get("from", {}) or {}
        user_id = u.get("id")
        username = u.get("username") or ""

        # meta trace
        forward = {
            "fwd_user_id": (msg.get("forward_from") or {}).get("id"),
            "fwd_chat_id": (msg.get("forward_from_chat") or {}).get("id"),
            "fwd_chat_type": (msg.get("forward_from_chat") or {}).get("type"),
        }
        via_bot = (msg.get("via_bot") or {}).get("id")
        push_event("msg_meta", {
            "user_id": user_id, "username": username,
            "chat_type": msg["chat"]["type"], "chat_title": msg["chat"].get("title",""),
            "via_bot": via_bot, **forward
        })

        # /start (with optional signed payload)
        if text.lower().startswith("/start"):
            parts = text.split(maxsplit=1)
            payload_token = parts[1] if len(parts) > 1 else ""
            start_meta = verify_deeplink_token(payload_token) if payload_token else None

            push_event("start", {
                "user_id": user_id,
                "username": username,
                "chat_type": msg["chat"]["type"],
                "chat_title": msg["chat"].get("title",""),
                "start_payload_ok": bool(start_meta),
                "campaign_id": (start_meta or {}).get("cid"),
                "extra": (start_meta or {})
            })
            admin_log(f"🚀 /start by @{username or user_id} | cid={ (start_meta or {}).get('cid') }")

            SESSIONS[chat_id] = {"stage": "ask_name", "name": "", "selections": []}
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "👋 Welcome to Kuwait IGCSE Portal!\nPlease type your full name (student):",
                "reply_markup": kb_with_restart({"inline_keyboard": []})
            })
            return jsonify({"ok": True})

        s = session(chat_id)

        if s.get("stage") == "ask_name" and text:
            s["name"] = text
            s["stage"] = "flow"
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "🧭 <b>Step 1/3 – Board</b>\nChoose the board:",
                "parse_mode": "HTML",
                "reply_markup": kb_with_restart(kb_board())
            })
            return jsonify({"ok": True})

        # Fallback
        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "Please use the options below to continue 👇",
            "reply_markup": kb_with_restart(kb_board())
        })
        return jsonify({"ok": True})

    except Exception as e:
        print("[ERR]", repr(e))
        print(traceback.format_exc())
        push_event("exception", {"err": repr(e)})
        admin_log(f"🔥 Exception:\n{repr(e)}")
        return jsonify({"ok": True}), 200

# -------- Telegram webhook endpoints --------
@app.post("/api/webhook")
def webhook_api():
    return _handle_webhook()

@app.route("/", defaults={"subpath": ""}, methods=["POST"])
@app.route("/<path:subpath>", methods=["POST"])
def webhook_catchall(subpath=None):
    return _handle_webhook()

# ---------- Helper: caption ----------
def format_teacher_caption_html(t: Dict[str,Any], student_full_name: str, board: str, grade: int, subjects: List[str]) -> str:
    quals = ", ".join(t.get("qualifications", []))
    boards = ", ".join(t.get("boards", []))
    grades = ""
    if t.get("grades"):
        gmin, gmax = min(t["grades"]), max(t["grades"])
        grades = f"Grades {gmin}-{gmax}"
    lines = [
        f"<b>{h(t['name'])}</b> — {h(', '.join(t.get('subjects', [])))}",
        "  " + " | ".join([x for x in [h(grades), f"Boards {h(boards)}" if boards else ""] if x]),
    ]
    if t.get("bio"):      lines.append("  " + h(t["bio"]))
    if quals:             lines.append("  " + f"Qualifications: {h(quals)}")
    return "\n".join(lines)
