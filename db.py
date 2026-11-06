"""Database + auth layer for the BuzzKar Organiser (Supabase / Postgres)."""
import os

import bcrypt
import pandas as pd
import psycopg2
import psycopg2.errors
import psycopg2.extras
import psycopg2.pool

TICKET_COLS = ["id", "title", "description", "client", "category", "priority", "status",
               "assignee", "created_by", "due_date", "created_at", "updated_at", "closed_at"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    name TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    must_change BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    client TEXT,
    category TEXT,
    priority TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Open',
    assignee TEXT NOT NULL,
    created_by TEXT NOT NULL,
    due_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS updates (
    id SERIAL PRIMARY KEY,
    ticket_id INT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    note TEXT NOT NULL,
    hours REAL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Lock the tables away from Supabase's public REST API. Our app connects
-- directly as the database owner, which is not affected by this.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE updates ENABLE ROW LEVEL SECURITY;
"""


# ---------- connection ----------
def _db_url():
    try:
        import streamlit as st
        url = st.secrets.get("DATABASE_URL")
        if url:
            return url
    except Exception:
        pass
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see README steps).")
    return url


# Connections are reused (a pool) instead of opening a new one per query,
# which is what made pages slow to load.
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 5, _db_url(), connect_timeout=10)
    return _pool


def _reset_pool():
    global _pool
    if _pool is not None:
        try:
            _pool.closeall()
        except Exception:
            pass
    _pool = None


def _run(sql, params, fetch):
    pool = _get_pool()
    conn = pool.getconn()
    broken = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            if fetch == "one":
                result = cur.fetchone()
            elif fetch == "all":
                result = cur.fetchall()
            else:
                result = None
        conn.commit()
        return result
    except psycopg2.OperationalError:
        broken = True
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn, close=broken or bool(conn.closed))


def _exec(sql, params=(), fetch=None):
    try:
        return _run(sql, params, fetch)
    except psycopg2.OperationalError:
        # Connection went stale (idle timeout etc.): rebuild the pool and retry once
        _reset_pool()
        return _run(sql, params, fetch)


def init_db():
    _exec(SCHEMA)


# ---------- users & auth ----------
def _hash(pw):
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def password_error(pw):
    if len(pw) < 8:
        return "Use at least 8 characters."
    if len(pw.encode()) > 72:
        return "Password is too long (max 72 characters)."
    return None


def get_user(username):
    return _exec("SELECT * FROM users WHERE lower(username) = lower(%s)", (username,), fetch="one")


def create_user(username, name, password, role="member"):
    """Returns an error string, or None on success. New users must change password on first login."""
    err = password_error(password)
    if err:
        return err
    try:
        _exec("INSERT INTO users (username, name, password_hash, role, must_change) "
              "VALUES (%s, %s, %s, %s, TRUE)", (username, name, _hash(password), role))
    except psycopg2.errors.UniqueViolation:
        return "That username or display name already exists."
    return None


def verify_login(username, password):
    row = get_user(username)
    if row and bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return {"username": row["username"], "name": row["name"],
                "role": row["role"], "must_change": row["must_change"]}
    return None


def change_password(username, new_pw):
    err = password_error(new_pw)
    if err:
        return err
    row = get_user(username)
    if bcrypt.checkpw(new_pw.encode(), row["password_hash"].encode()):
        return "Pick a password different from your current one."
    _exec("UPDATE users SET password_hash = %s, must_change = FALSE WHERE id = %s",
          (_hash(new_pw), row["id"]))
    return None


def reset_password(username, temp_pw):
    """Admin reset: sets a temporary password the user must change at next login."""
    err = password_error(temp_pw)
    if err:
        return err
    _exec("UPDATE users SET password_hash = %s, must_change = TRUE WHERE lower(username) = lower(%s)",
          (_hash(temp_pw), username))
    return None


def team_names():
    return [r["name"] for r in _exec("SELECT name FROM users ORDER BY name", fetch="all")]


def list_users():
    rows = _exec("SELECT username, name, role, must_change FROM users ORDER BY id", fetch="all")
    return pd.DataFrame(rows, columns=["username", "name", "role", "must_change"])


# ---------- tickets ----------
def create_ticket(title, description, client, category, priority, assignee, created_by, due):
    row = _exec(
        "INSERT INTO tickets (title, description, client, category, priority, assignee, created_by, due_date) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (title, description, client, category, priority, assignee, created_by, due), fetch="one")
    add_update(row["id"], created_by, f"Ticket created and assigned to {assignee}")
    return row["id"]


def get_ticket(tid):
    return _exec("SELECT * FROM tickets WHERE id = %s", (tid,), fetch="one")


def list_tickets(assignee=None, open_only=False):
    q, p = "SELECT * FROM tickets WHERE TRUE", []
    if assignee:
        q += " AND assignee = %s"
        p.append(assignee)
    if open_only:
        q += " AND status <> 'Closed'"
    q += (" ORDER BY CASE priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,"
          " due_date NULLS LAST, id DESC")
    df = pd.DataFrame(_exec(q, p, fetch="all"), columns=TICKET_COLS)
    df["due_date"] = pd.to_datetime(df["due_date"])
    df["closed_at"] = pd.to_datetime(df["closed_at"], utc=True)
    return df


def get_updates(tid):
    return _exec("SELECT author, note, hours, created_at FROM updates "
                 "WHERE ticket_id = %s ORDER BY id DESC", (tid,), fetch="all")


def add_update(tid, author, note, hours=0):
    _exec("INSERT INTO updates (ticket_id, author, note, hours) VALUES (%s,%s,%s,%s)",
          (tid, author, note, hours))
    _exec("UPDATE tickets SET updated_at = now() WHERE id = %s", (tid,))


def set_status(tid, status, who):
    t = get_ticket(tid)
    if not t or t["status"] == status:
        return
    _exec("UPDATE tickets SET status = %s, updated_at = now(), "
          "closed_at = CASE WHEN %s = 'Closed' THEN now() ELSE NULL END WHERE id = %s",
          (status, status, tid))
    add_update(tid, who, f"Status: {t['status']} → {status}")


def set_assignee(tid, assignee, who):
    t = get_ticket(tid)
    if not t or t["assignee"] == assignee:
        return
    _exec("UPDATE tickets SET assignee = %s, updated_at = now() WHERE id = %s", (assignee, tid))
    add_update(tid, who, f"Reassigned: {t['assignee']} → {assignee}")


def delete_ticket(tid):
    _exec("DELETE FROM tickets WHERE id = %s", (tid,))


def hours_by_person():
    rows = _exec("SELECT author, SUM(hours) AS hours FROM updates GROUP BY author ORDER BY hours DESC",
                 fetch="all")
    return pd.DataFrame(rows, columns=["author", "hours"])
