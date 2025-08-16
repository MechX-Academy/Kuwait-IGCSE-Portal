# api/webhook.py
import os, json, re, time, html, traceback, base64, hmac, hashlib
from typing import Dict, Any, List, Tuple, Set
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
BUILD_TAG = "kuwait-igcse-portal-v3.1-final"

# ------------ Telegram basics ------------
TELEGRAM_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
BOT_USERNAME   = (os.getenv("BOT_USERNAME") or "").lstrip("@").strip()
BOT_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else None

TELEGRAM_WEBHOOK_SECRET = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
ADMIN_LOG_CHAT_ID = (os.getenv("ADMIN_LOG_CHAT_ID") or "").strip()
LOG_RAW_UPDATES = (os.getenv("LOG_RAW_UPDATES") or "0").strip().lower() in ("1", "true", "yes")

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

def tg_edit_or_send(chat_id: int, message_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode, "reply_markup": reply_markup}
    r = tg("editMessageText", payload)
    ok = False
    try:
        j = r.json() if r is not None else {}
        ok = (r is not None) and (r.status_code == 200) and (isinstance(j, dict) and j.get("ok", True))
    except Exception:
        ok = False
    if not ok:
        tg("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "reply_markup": reply_markup})

def admin_log(text: str):
    if not (ADMIN_LOG_CHAT_ID and BOT_API):
        return
    tg("sendMessage", {"chat_id": ADMIN_LOG_CHAT_ID, "text": text[:4000]})

# ------------ Config ------------
PORTAL_WA_NUMBER = re.sub(r"\D+", "", os.getenv("PORTAL_WA_NUMBER", "+96597273411")) or "96597273411"
PUBLIC_BASE_URL  = (os.getenv("PUBLIC_BASE_URL", "https://kuwait-igcse-portal.vercel.app") or "").rstrip("/")

# Optional WA redirect HMAC
WA_SIGNING_SECRET = (os.getenv("WA_SIGNING_SECRET") or "").strip() or None

# ------------ Google Sheets Analytics (Apps Script Web App) ------------
GS_WEBHOOK = os.getenv("GS_WEBHOOK", "").strip()  # مثال: https://script.google.com/macros/s/AKfycb.../exec
GS_SECRET  = os.getenv("GS_SECRET", "").strip()

def push_event(event_type: str, payload: dict):
    if not (GS_WEBHOOK and GS_SECRET):
        return
    rec = {
        "ts": int(time.time()),
        "event": event_type,
        **payload,
        "_secret": GS_SECRET
    }
    try:
        requests.post(GS_WEBHOOK, json=rec, timeout=4)
    except Exception as e:
        print("[ANALYTICS] push_event failed:", repr(e))

# ------------ Load data ------------
DATA_PATH = os.path.join(os.path.dirname(__file__), "teachers.json")
try:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        TEACHERS = json.load(f)
    print(f"Loaded {len(TEACHERS)} teachers from {DATA_PATH}.")
except Exception as e:
    print(f"ERROR loading teachers.json: {e}")
    TEACHERS = []

# ------------ Subjects (Extended + AS/A) ------------
VALID_SUBJECTS = {
    "math (extended)": ["math (extended)", "mathematics (extended)", "maths (extended)", "igcse mathematics (extended)"],
    "math (as/a level)": ["math (as/a level)", "mathematics (as/a level)", "maths (as/a level)", "additional math (as/a level)", "further math (as/a level)"],
    "physics (extended)": ["physics (extended)", "phys (extended)"],
    "physics (as/a level)": ["physics (as/a level)", "phys (as/a level)"],
    "chemistry (extended)": ["chemistry (extended)", "chem (extended)"],
    "chemistry (as/a level)": ["chemistry (as/a level)", "chem (as/a level)"],
    "biology (extended)": ["biology (extended)", "bio (extended)"],
    "biology (as/a level)": ["biology (as/a level)", "bio (as/a level)"],
    "english language": ["english", "english language", "esl", "first language english", "second language english", "english sl"],
    "english literature": ["english literature", "literature", "english fl"],
    "business (extended)": ["business (extended)", "business studies (extended)"],
    "business (as/a level)": ["business (as/a level)", "business studies (as/a level)"],
    "economics (extended)": ["economics (extended)", "econ (extended)"],
    "economics (as/a level)": ["economics (as/a level)", "econ (as/a level)"],
    "psychology (as/a level)": ["psychology (as/a level)", "psy (as/a level)"],
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

SUBJECT_GROUPS = {
    "Core (IGCSE) subjects": [
        ("MTH_EXT", "Math (Extended)"),
        ("PHY_EXT", "Physics (Extended)"),
        ("CHE_EXT", "Chemistry (Extended)"),
        ("BIO_EXT", "Biology (Extended)"),
        ("ENL", "English SL"),
        ("ENLIT", "English FL"),
        ("BUS_EXT", "Business (Extended)"),
        ("ECO_EXT", "Economics (Extended)"),
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
    "MTH_EXT": "Math (Extended)",
    "MTH_ALEVEL": "Math (AS/A Level)",
    "PHY_EXT": "Physics (Extended)",
    "PHY_ALEVEL": "Physics (AS/A Level)",
    "CHE_EXT": "Chemistry (Extended)",
    "CHE_ALEVEL": "Chemistry (AS/A Level)",
    "BIO_EXT": "Biology (Extended)",
    "BIO_ALEVEL": "Biology (AS/A Level)",
    "ENL": "English SL",
    "ENLIT": "English FL",
    "BUS_EXT": "Business (Extended)",
    "BUS_ALEVEL": "Business (AS/A Level)",
    "ECO_EXT": "Economics (Extended)",
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

# ------------ Helpers ------------
def h(x: str) -> str:
    return html.escape(x or "")

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _nice_subject_name(key: str) -> str:
    nice = {
        "ict": "ICT",
        "cs": "Computer Science",
        "pe": "Physical Education",
        "english language": "English Language",
        "english literature": "English Literature",
        "math (extended)": "Math (Extended)",
        "math (as/a level)": "Math (AS/A Level)",
        "physics (extended)": "Physics (Extended)",
        "physics (as/a level)": "Physics (AS/A Level)",
        "chemistry (extended)": "Chemistry (Extended)",
        "chemistry (as/a level)": "Chemistry (AS/A Level)",
        "biology (extended)": "Biology (Extended)",
        "biology (as/a level)": "Biology (AS/A Level)",
        "business (extended)": "Business (Extended)",
        "business (as/a level)": "Business (AS/A Level)",
        "economics (extended)": "Economics (Extended)",
        "economics (as/a level)": "Economics (AS/A Level)",
        "psychology (as/a level)": "Psychology (AS/A Level)",
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
        s_label = CODE_TO_SUBJECT.get(s, s)  # allow codes (e.g. MTH_EXT)
        c = canonical_subject(s_label)
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

# Precompute canonical fields on teachers
for t in TEACHERS:
    raw_subj = t.get("subjects", []) or []
    # display labels (convert codes if any)
    t["_subjects_display"] = [CODE_TO_SUBJECT.get(x, x) for x in raw_subj]
    # matching set
    t["_subjects_canon"] = set()
    for s in t["_subjects_display"]:
        c = canonical_subject(s)
        if c:
            t["_subjects_canon"].add(c)
    t["_boards_canon"] = [canonical_board(b) for b in (t.get("boards") or [])]

def match_teachers(subject=None, grade=None, board=None, limit=4):
    board_can = canonical_board(board) if board else ""
    results = []
    debug_why = []
    for t in TEACHERS:
        why = {"teacher": t.get("name"), "ok": True, "reasons": []}
        if subject and not teacher_has_subject(t.get("subjects", []), subject):
            why["ok"] = False; why["reasons"].append(f"subject_mismatch: wanted={subject}, teacher_subjects={t.get('subjects')}")
        if grade is not None:
            grades = t.get("grades") or []
            if grade not in grades:
                why["ok"] = False; why["reasons"].append(f"grade_mismatch: wanted={grade}, teacher_grades={grades}")
        if board_can:
            if board_can not in (t.get("_boards_canon") or []):
                why["ok"] = False; why["reasons"].append(f"board_mismatch: wanted_can={board_can}, teacher_can={t.get('_boards_canon')}")
        if why["ok"]:
            results.append(t)
        else:
            debug_why.append(why)
    if not results:
        print("[DEBUG NO MATCH]", json.dumps(debug_why[:5], ensure_ascii=False))
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

# ------------ WA redirect (tracking via /api/wa) ------------
def build_wa_redirect_link(user_id, username, teacher_id, wa_number, prefill_text):
    payload = {
        "user_id": user_id,
        "username": username or "",
        "teacher_id": teacher_id,
        "wa": re.sub(r"\D+", "", wa_number or "") or PORTAL_WA_NUMBER,
        "text": prefill_text
    }
    t = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode().rstrip("=")
    base = PUBLIC_BASE_URL
    if WA_SIGNING_SECRET:
        sig = hmac.new(WA_SIGNING_SECRET.encode("utf-8"), t.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{base}/api/wa?t={t}&sig={sig}"
    return f"{base}/api/wa?t={t}"

# ------------ Keyboards & rendering ------------
def format_teacher_caption_html(t: Dict[str,Any], student_full_name: str, board: str, grade: int, subjects: List[str]) -> str:
    quals = ", ".join(t.get("qualifications", []) or [])
    boards = ", ".join(t.get("boards", []) or [])
    grades = ""
    if t.get("grades"):
        gmin, gmax = min(t["grades"]), max(t["grades"])
        grades = f"Grades {gmin}-{gmax}"
    lines = [
        f"<b>{h(t.get('name','Tutor'))}</b> — {h(', '.join(t.get('_subjects_display') or t.get('subjects', [])))}",
        "  " + " | ".join([x for x in ([h(grades)] if grades else []) + ([f"Boards {h(boards)}"] if boards else []) if x]),
    ]
    if t.get("bio"):      lines.append("  " + h(t["bio"]))
    if quals:             lines.append("  " + f"Qualifications: {h(quals)}")
    return "\n".join(lines)

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

# ------------ Sessions & idempotency ------------
SESSIONS: Dict[int, Dict[str, Any]] = {}
RECENT_DONE: Dict[int, List[Tuple[str, float]]] = {}

def session(chat_id: int) -> Dict[str, Any]:
    if chat_id not in SESSIONS:
        SESSIONS[chat_id] = {
            "stage": "idle",
            "name": "",
            "selections": [],
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

# ------------ Security check for Telegram webhook ------------
def _check_telegram_secret():
    if not TELEGRAM_WEBHOOK_SECRET:
        return True
    sec = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    ok = hmac.compare_digest(sec, TELEGRAM_WEBHOOK_SECRET)
    if not ok:
        print("[WEBHOOK] bad secret header")
    return ok

# ------------ Routes ------------
@app.get("/api/webhook")
def ping():
    return jsonify(ok=True, build=BUILD_TAG, teachers=len(TEACHERS), bot=bool(BOT_API))

def _handle_webhook():
    if not _check_telegram_secret():
        return jsonify(ok=False, error="forbidden"), 403
    try:
        update = request.get_json(force=True, silent=True) or {}
        if LOG_RAW_UPDATES:
            admin_log(f"RAW UPDATE: {json.dumps(update)[:3500]}")
        else:
            print("[UPDATE]", json.dumps(update)[:1200])

        # ---------- Callback queries ----------
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            msg_id = cq["message"]["message_id"]
            data = cq.get("data", "")
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
                    tg_edit_or_send(chat_id, msg_id,
                        summary_text(b, g, sel),
                        reply_markup=kb_with_restart(kb_subjects(b, g, sel))
                    )
                    return jsonify({"ok": True})

                tg_edit_or_send(chat_id, msg_id,
                    "🔢 <b>Step 2/3 – Grade</b>\nSelect your current grade:",
                    reply_markup=kb_with_restart(kb_grade(b))
                )
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
                tg_edit_or_send(chat_id, msg_id,
                    summary_text(b, g, sel),
                    reply_markup=kb_with_restart(kb_subjects(b, g, sel))
                )
                return jsonify({"ok": True})

            # Toggle subject
            if data.startswith("T|"):
                _, code, b, g, enc = data.split("|", 4)
                g = int(g)
                sel = decode_sel(enc)
                if code == "__RESET__":
                    sel = set()
                else:
                    if code in sel: sel.remove(code)
                    else: sel.add(code)
                tg_edit_or_send(chat_id, msg_id,
                    summary_text(b, g, sel),
                    reply_markup=kb_with_restart(kb_subjects(b, g, sel))
                )
                return jsonify({"ok": True})

            # Done selecting subjects
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
                    "prefs": {}
                }
                s.setdefault("selections", []).append(selection)

                push_event("selection", {
                    "user_id": user_id, "username": username,
                    "board": BOARD_CODES.get(b,b), "grade": g,
                    "subjects": selection["subjects"]
                })

                s["pref_flow"] = {
                    "sel_idx": len(s["selections"]) - 1,
                    "i": 0,
                    "subjects": selection["subjects"],
                    "current_mode": None
                }
                cur_subj = s["pref_flow"]["subjects"][0]
                s["stage"] = "ask_mode_per_subject"
                tg_edit_or_send(chat_id, msg_id,
                    f"🎯 Lesson type for <b>{h(cur_subj)}</b>?",
                    reply_markup=kb_mode()
                )
                return jsonify({"ok": True})

            # MODE per subject
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
                tg_edit_or_send(chat_id, msg_id,
                    f"🗓️ Lessons/week for <b>{h(cur_subj)}</b>?",
                    reply_markup=kb_lpw()
                )
                return jsonify({"ok": True})

            # LPW per subject
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

                # next or finish
                pf["i"] += 1
                if pf["i"] < len(pf["subjects"]):
                    next_subj = pf["subjects"][pf["i"]]
                    pf["current_mode"] = None
                    s["stage"] = "ask_mode_per_subject"
                    tg_edit_or_send(chat_id, msg_id,
                        f"🎯 Lesson type for <b>{h(next_subj)}</b>?",
                        reply_markup=kb_mode()
                    )
                    return jsonify({"ok": True})
                else:
                    s["pref_flow"] = None
                    s["stage"] = "flow"
                    tg_edit_or_send(chat_id, msg_id,
                        "Preferences saved ✅\nYou can add more selections or show tutors.",
                        reply_markup=kb_with_restart({
                            "inline_keyboard": [
                                [{"text": "➕ Add more", "callback_data": "ADD_MORE"}],
                                [{"text": "🚀 Show tutors", "callback_data": "SHOW_ALL"}]
                            ]
                        })
                    )
                    return jsonify({"ok": True})

            # Add more
            if data == "ADD_MORE":
                tg_edit_or_send(chat_id, msg_id,
                    "🧭 <b>Step 1/3 – Board</b>\nChoose the board for the new selection:",
                    reply_markup=kb_with_restart(kb_board())
                )
                return jsonify({"ok": True})

            # Show all tutors
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

            # Send WhatsApp
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
                tg("sendMessage", {
                    "chat_id": chat_id,
                    "text": f'<a href="{wa_link}">📩 Open WhatsApp</a>',
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": kb_with_restart({"inline_keyboard": []})
                })
                return jsonify({"ok": True})

            return jsonify({"ok": True})

        # ---------- Normal messages ----------
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return jsonify({"ok": True})

        chat_id = msg["chat"]["id"]
        text = (msg.get("text") or "").strip()
        s = session(chat_id)

        u = msg.get("from", {}) or {}
        user_id = u.get("id"); username = u.get("username") or ""

        if text.lower() in ("/start", "start"):
            SESSIONS[chat_id] = {"stage": "ask_name", "name": "", "selections": []}
            tg("sendMessage", {
                "chat_id": chat_id,
                "text": "👋 Welcome to Kuwait IGCSE Portal!\nPlease type your full name (student):",
                "reply_markup": kb_with_restart({"inline_keyboard": []})
            })
            push_event("session_start", {"user_id": user_id, "username": username})
            return jsonify({"ok": True})

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

        tg("sendMessage", {
            "chat_id": chat_id,
            "text": "Please use the options below to continue 👇",
            "reply_markup": kb_with_restart(kb_board())
        })
        return jsonify({"ok": True})

    except Exception as e:
        print("[ERR]", repr(e))
        print(traceback.format_exc())
        return jsonify({"ok": True}), 200

# Vercel entrypoint
@app.post("/api/webhook")
def webhook_api():
    return _handle_webhook()

# Catch-all for POST (keeps Telegram happy if path changes)
@app.route("/", defaults={"subpath": ""}, methods=["POST"])
@app.route("/<path:subpath>", methods=["POST"])
def webhook_catchall(subpath=None):
    return _handle_webhook()
