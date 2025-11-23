# app/utils/db.py
from __future__ import annotations
import sqlite3
import json
import time
import secrets
import hashlib
import os
from pathlib import Path
from typing import Optional, Any, List, Dict

# ============================================================
# PASSWORD HASHING (bcrypt preferred)
# ============================================================
try:
    import bcrypt
    HAS_BCRYPT = True
except Exception:
    bcrypt = None
    HAS_BCRYPT = False


# ============================================================
# DATABASE PATHS (ONE DB ONLY — app/instance/app.db)
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent  # app/
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = INSTANCE_DIR / "app.db"   # <-- MAIN AND ONLY DB


# ============================================================
# CONNECTION HELPERS
# ============================================================
def get_conn() -> sqlite3.Connection:
    """Primary connection used everywhere."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_connection() -> sqlite3.Connection:
    """Compatibility alias for older modules."""
    return get_conn()


# ============================================================
# PASSWORD HELPERS
# ============================================================
def hash_password(plain: str) -> str:
    if not plain:
        return ""
    if HAS_BCRYPT:
        hashed = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt())
        return hashed.decode("utf-8")
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    if HAS_BCRYPT:
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            return False
    return hashlib.sha256(plain.encode("utf-8")).hexdigest() == hashed


# ============================================================
# INIT DB
# ============================================================
def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT,
        password_hash TEXT,
        reset_token TEXT,
        reset_expiry INTEGER
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS family (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        member_name TEXT,
        relation TEXT,
        monthly_income REAL DEFAULT 0,
        age INTEGER DEFAULT 0,
        notes TEXT,
        is_head INTEGER DEFAULT 0,
        family_name TEXT DEFAULT ''
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        date TEXT,
        amount REAL,
        category TEXT,
        assigned_member TEXT,
        split_json TEXT,
        note TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        main_budget REAL,
        category_limits_json TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        goal_name TEXT,
        target_amount REAL,
        months_to_complete INTEGER,
        created_on TEXT
    )
    """)

    conn.commit()
    conn.close()


# Run at import
try:
    init_db()
except Exception:
    pass


# ============================================================
# USER MANAGEMENT
# ============================================================
def create_user(username: str, email: str, password: str) -> bool:
    if not username or not password:
        return False
    try:
        pw_hash = hash_password(password)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email or "", pw_hash)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        try: conn.close()
        except: pass
        return False
    except Exception:
        try: conn.close()
        except: pass
        return False


register_user = create_user


def login_user(username: str, password: str) -> bool:
    if not username or not password:
        return False
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return False
    return verify_password(password, row["password_hash"])


def get_user_email(username: str) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT email FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    email = row["email"]
    return email if email else None


# ============================================================
# RESET TOKEN
# ============================================================
def create_reset_token(email_or_username: str, ttl_seconds: int = 3600) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT username FROM users WHERE username = ? OR email = ?",
        (email_or_username, email_or_username)
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None

    username = row["username"]
    token = secrets.token_urlsafe(24)
    expiry = int(time.time()) + ttl_seconds

    cur.execute(
        "UPDATE users SET reset_token=?, reset_expiry=? WHERE username=?",
        (token, expiry, username)
    )
    conn.commit()
    conn.close()
    return token


def verify_reset_token(token: str) -> Optional[str]:
    if not token:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT username, reset_expiry FROM users WHERE reset_token=?", (token,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None

    expiry = int(row["reset_expiry"] or 0)
    if expiry and time.time() > expiry:
        return None

    return row["username"]


validate_reset_token = verify_reset_token


def reset_password(username: str, new_password: str) -> bool:
    try:
        new_hash = hash_password(new_password)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET password_hash=?, reset_token=NULL, reset_expiry=NULL WHERE username=?",
            (new_hash, username)
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        try: conn.close()
        except: pass
        return False
# ------------------------------------------------------------
# CLEAR RESET TOKEN (needed by app.py)
# ------------------------------------------------------------
def clear_reset_token(username: str) -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET reset_token = NULL, reset_expiry = NULL WHERE username = ?",
            (username,)
        )
        conn.commit()
        conn.close()
    except Exception:
        try:
            conn.close()
        except:
            pass

# ============================================================
# FAMILY
# ============================================================
def add_family_member(username: str,
                      member_name: str,
                      relation: str,
                      monthly_income: float = 0.0,
                      age: int = 0,
                      notes: str = "",
                      is_head: bool = False,
                      family_name: str = "",
                      **kwargs) -> bool:

    try: monthly_income = float(monthly_income or 0.0)
    except: monthly_income = 0.0

    try: age = int(age or 0)
    except: age = 0

    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO family (
                username, member_name, relation, monthly_income,
                age, notes, is_head, family_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            username,
            member_name or "",
            relation or "",
            monthly_income,
            age,
            notes or "",
            1 if is_head else 0,
            family_name or ""
        ))
        conn.commit()
        conn.close()

        # auto-sync budget
        sync_budget_from_family(username)

        return True

    except Exception:
        try: conn.close()
        except: pass
        return False


def save_family(family_name: str, username: str, rows: List[Dict]) -> bool:
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("DELETE FROM family WHERE username=?", (username,))

        for r in rows:
            inc = float(r.get("monthly_income") or 0.0)
            age = int(r.get("age") or 0)

            cur.execute("""
                INSERT INTO family (
                    username, member_name, relation, monthly_income,
                    age, notes, is_head, family_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                username,
                r.get("member_name",""),
                r.get("relation",""),
                inc,
                age,
                r.get("notes",""),
                1 if str(r.get("is_head","")).lower() in ("1","on","true","yes") else 0,
                family_name or ""
            ))

        conn.commit()
        conn.close()

        # auto-sync
        sync_budget_from_family(username)

        return True

    except Exception:
        try: conn.close()
        except: pass
        return False


def load_family(username: str) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, member_name, relation, monthly_income, age, notes, is_head, family_name
        FROM family
        WHERE username=?
        ORDER BY id ASC
    """, (username,))
    rows = cur.fetchall()
    conn.close()

    return [{k: r[k] for k in r.keys()} for r in rows]


# ============================================================
# EXPENSES
# ============================================================
def add_expense(username: str,
                amount: Any,
                category: str,
                assigned_member: str = "",
                split: Any = None,
                note: str = "",
                date: Optional[str] = None) -> bool:

    try:
        if date is None:
            date = time.strftime("%Y-%m-%d")

        amount_val = float(amount or 0)

        split_json = json.dumps(split) if split else ""

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO expenses (username, date, amount, category, assigned_member, split_json, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (username, date, amount_val, category or "", assigned_member or "", split_json, note or ""))
        conn.commit()
        conn.close()
        return True

    except Exception:
        try: conn.close()
        except: pass
        return False


def load_expenses(username: str) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, date, amount, category, assigned_member, split_json, note
        FROM expenses
        WHERE username=?
        ORDER BY date DESC, id DESC
    """, (username,))
    rows = cur.fetchall()
    conn.close()

    result = []
    for r in rows:
        row = {k: r[k] for k in r.keys()}
        try:
            row["split"] = json.loads(r["split_json"]) if r["split_json"] else None
        except:
            row["split"] = None
        result.append(row)
    return result


# ============================================================
# BUDGETS
# ============================================================
def set_budget(username: str, main_budget: Any, category_limits: Any = None) -> bool:
    try:
        mb_val = float(main_budget) if main_budget not in (None, "") else None
    except:
        mb_val = None

    cat_json = "{}"
    if category_limits is not None:
        try:
            cat_json = json.dumps(category_limits)
        except:
            cat_json = "{}"

    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM budgets WHERE username=?", (username,))
        cur.execute("""
            INSERT INTO budgets (username, main_budget, category_limits_json)
            VALUES (?, ?, ?)
        """, (username, mb_val, cat_json))
        conn.commit()
        conn.close()
        return True

    except Exception:
        try: conn.close()
        except: pass
        return False


def load_budget(username: str) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT main_budget, category_limits_json
        FROM budgets
        WHERE username=?
        ORDER BY id DESC LIMIT 1
    """, (username,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return {"main_budget": None, "category_limits_json": "{}"}

    try:
        main_val = float(row["main_budget"]) if row["main_budget"] not in (None,"") else None
    except:
        main_val = None

    return {"main_budget": main_val, "category_limits_json": row["category_limits_json"] or "{}"}


# ============================================================
# GOALS
# ============================================================
def add_goal(username: str, goal_name: str, target_amount: float, months: int,
             created_on: Optional[str] = None) -> bool:

    try:
        if created_on is None:
            created_on = time.strftime("%Y-%m-%d")

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO goals (username, goal_name, target_amount, months_to_complete, created_on)
            VALUES (?, ?, ?, ?, ?)
        """, (username, goal_name, float(target_amount or 0), int(months or 1), created_on))
        conn.commit()
        conn.close()
        return True

    except Exception:
        try: conn.close()
        except: pass
        return False


def load_goals(username: str) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, goal_name, target_amount, months_to_complete, created_on
        FROM goals
        WHERE username=?
        ORDER BY id DESC
    """, (username,))
    rows = cur.fetchall()
    conn.close()

    return [{k: r[k] for k in r.keys()} for r in rows]


# ============================================================
# CATEGORY BREAKDOWN
# ============================================================
def category_breakdown(username: str, year: int, month: int) -> Dict[str, float]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT category, SUM(amount) as total
        FROM expenses
        WHERE username=?
          AND substr(date,1,4)=?
          AND substr(date,6,2)=?
        GROUP BY category
    """, (username, str(year), f"{month:02d}"))
    rows = cur.fetchall()
    conn.close()

    return {r["category"] or "Other": float(r["total"] or 0.0) for r in rows}


# ============================================================
# TELEGRAM / EMAIL ALERT HELPERS (unchanged)
# ============================================================
import requests
from datetime import datetime


def load_telegram_config():
    cfg_path = os.path.join(BASE_DIR, "instance", "telegram_config.json")
    if not os.path.exists(cfg_path):
        return {"bot_token": "", "chat_id": ""}
    with open(cfg_path, "r") as f:
        return json.load(f)


def send_telegram_alert(message: str) -> bool:
    try:
        cfg = load_telegram_config()
        token = cfg.get("bot_token","").strip()
        chat_id = cfg.get("chat_id","").strip()

        if not token or not chat_id:
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)
        return r.status_code == 200
    except:
        return False


import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = os.environ.get("SMTP_PORT")
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")


def send_email_alert(to_addr: str, subject: str, body: str) -> bool:
    try:
        if not SMTP_HOST or not SMTP_PORT or not SMTP_USER or not SMTP_PASS:
            return False

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_addr

        with smtplib.SMTP_SSL(SMTP_HOST, int(SMTP_PORT)) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_addr, msg.as_string())
        return True
    except:
        return False


def send_budget_alert(username: str, category: str, spent: float, limit: float):
    body = (
        f"⚠️ Budget Alert!\n"
        f"User: {username}\n"
        f"Category: {category}\n"
        f"Spent: ₹{spent}\n"
        f"Limit: ₹{limit}\n"
        f"Time: {datetime.now()}"
    )

    send_telegram_alert(body)

    email = get_user_email(username)
    if email:
        send_email_alert(email, f"Budget Alert: {category}", body)
# ------------------------------------------------------------
# Helper: get_user_budget, get_user_contacts, get_monthly_family_expenses
# ------------------------------------------------------------

def get_user_budget(username: str) -> Optional[float]:
    """
    Return the user's monthly budget (float) or None.
    Uses load_budget() which returns {'main_budget': value}.
    """
    try:
        b = load_budget(username)
        if not b:
            return None
        return b.get("main_budget")
    except Exception:
        return None


def get_user_contacts(username: str):
    """
    Returns (email, telegram_chat_id)

    - email: from users table (get_user_email)
    - telegram_chat_id: try instance/telegram_users.json, then instance/telegram_config.json
    """
    # 1. email
    try:
        email = get_user_email(username)
    except Exception:
        email = None

    # 2. telegram chat id
    telegram_chat_id = None
    try:
        user_map_path = os.path.join(BASE_DIR, "instance", "telegram_users.json")
        if os.path.exists(user_map_path):
            with open(user_map_path, "r") as f:
                data = json.load(f)
            telegram_chat_id = data.get(username)
    except Exception:
        telegram_chat_id = None

    if not telegram_chat_id:
        try:
            cfg = load_telegram_config()
            chat = cfg.get("chat_id", "") if isinstance(cfg, dict) else ""
            telegram_chat_id = chat if chat and str(chat).strip() else None
        except Exception:
            telegram_chat_id = None

    return (email, telegram_chat_id)


def get_monthly_family_expenses(username: str) -> float:
    """
    Sum expenses.amount for the current month for the given username.
    Returns float total (0.0 if none or on error).
    """
    from datetime import datetime
    now = datetime.now()
    year = str(now.year)
    month = f"{now.month:02d}"

    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT IFNULL(SUM(amount), 0) AS total
            FROM expenses
            WHERE username = ?
              AND substr(date, 1, 4) = ?
              AND substr(date, 6, 2) = ?
            """,
            (username, year, month)
        )
        row = cur.fetchone()
        conn.close()
        if row:
            # row can be sqlite3.Row, so access by key or index
            try:
                return float(row["total"])
            except Exception:
                return float(row[0])
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
    return 0.0

# ============================================================
# AUTO-SYNC BUDGET FROM FAMILY (FINAL & CLEAN)
# ============================================================
def sync_budget_from_family(username: str) -> float:
    """
    Sums monthly incomes of all family members for a user
    and writes the total into budgets.main_budget.
    """
    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("SELECT monthly_income FROM family WHERE username=?", (username,))
        rows = cur.fetchall()

        total = 0.0
        for r in rows:
            try:
                total += float(r[0] or 0.0)
            except:
                pass

        # ensure budgets table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                main_budget REAL,
                category_limits_json TEXT
            )
        """)

        # update or insert
        cur.execute("SELECT id FROM budgets WHERE username=?", (username,))
        row = cur.fetchone()

        if row:
            cur.execute("UPDATE budgets SET main_budget=? WHERE username=?", (total, username))
        else:
            cur.execute("""
                INSERT INTO budgets (username, main_budget, category_limits_json)
                VALUES (?, ?, ?)
            """, (username, total, "{}"))

        conn.commit()
        conn.close()
        return float(total)

    except Exception as e:
        print("sync_budget_from_family ERROR:", e)
        return 0.0
