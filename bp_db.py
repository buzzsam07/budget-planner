"""Supabase layer for the Budget Planner: login, households, loading and saving data.

Needs these Streamlit secrets (anon / publishable key only, never the service_role key):
    supabase_url = "https://xxxx.supabase.co"
    supabase_anon_key = "..."
"""

import streamlit as st

try:
    from supabase import create_client
except Exception:                       # library missing -> app runs as a login-free calculator
    create_client = None


def secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return None


def enabled():
    return bool(secret("supabase_url") and secret("supabase_anon_key") and create_client)


# ---------------- Client (one per user session, never shared) ----------------
def client():
    if "sb" not in st.session_state:
        st.session_state.sb = create_client(secret("supabase_url"), secret("supabase_anon_key"))
    return st.session_state.sb


def db():
    sb = client()
    sb.auth.get_session()               # refreshes the login token if it has expired
    return sb


def current_refresh_token():
    sess = client().auth.get_session()
    return sess.refresh_token if sess else None


# ---------------- Auth ----------------
def sign_in(email, password):
    return client().auth.sign_in_with_password({"email": email.strip(), "password": password})


def sign_up(email, password):
    return client().auth.sign_up({"email": email.strip(), "password": password})


def sign_out():
    try:
        client().auth.sign_out({"scope": "local"})     # this device only
    except Exception:
        pass


def restore_session(refresh_token):
    res = client().auth.refresh_session(refresh_token)
    return res.session, res.user


def send_reset_code(email):
    client().auth.reset_password_for_email(email.strip())


def reset_password_with_code(email, code, new_password):
    res = client().auth.verify_otp({"email": email.strip(), "token": code.strip(), "type": "recovery"})
    client().auth.update_user({"password": new_password})
    return res.user


def change_password(new_password):
    db().auth.update_user({"password": new_password})


# ---------------- Households ----------------
def my_households(force=False):
    if force or st.session_state.get("households") is None:
        rows = (db().table("household_members")
                .select("household_id,joined_at,households(name,settings)")
                .eq("user_id", st.session_state["uid"])
                .order("joined_at", desc=True).execute().data)
        hs = []
        for r in rows:
            h = r.get("households") or {}
            hs.append({"id": r["household_id"], "name": h.get("name") or "Household",
                       "settings": h.get("settings") or {}, "joined_at": str(r.get("joined_at") or "")})
        if not hs:
            db().rpc("create_household", {"p_name": "My household"}).execute()
            return my_households(force=True)
        st.session_state.households = hs
    return st.session_state.households


def household_members(hid):
    return (db().table("household_members").select("email,joined_at")
            .eq("household_id", hid).order("joined_at").execute().data)


def update_household(hid, **fields):
    db().table("households").update(fields).eq("id", hid).execute()
    st.session_state.households = None


def create_invite(hid):
    return db().rpc("create_invite", {"p_household": hid}).execute().data


def join_household(code):
    hid = db().rpc("join_household", {"p_code": code.strip()}).execute().data
    st.session_state.households = None
    return hid


def leave_household(hid):
    db().rpc("leave_household", {"p_household": hid}).execute()
    st.session_state.households = None
    invalidate()


def delete_account():
    db().rpc("delete_my_account").execute()


# ---------------- Reading data ----------------
def _fetch(table, hid, order):
    """All rows for a household (pages through PostgREST's 1000-row limit)."""
    out, start, page = [], 0, 1000
    while True:
        q = db().table(table).select("*").eq("household_id", hid)
        for col, desc in order:
            q = q.order(col, desc=desc)
        rows = q.range(start, start + page - 1).execute().data
        out += rows
        if len(rows) < page:
            return out
        start += page


def load_data(hid, force=False):
    cached = st.session_state.get("data")
    if force or not cached or cached.get("hid") != hid:
        st.session_state.data = {
            "hid": hid,
            "budgets": _fetch("budgets", hid, (("period", True), ("created_at", True))),
            "transactions": _fetch("transactions", hid, (("tx_date", True), ("created_at", True))),
            "recurring": _fetch("recurring_items", hid, (("created_at", False),)),
            "goals": _fetch("goals", hid, (("created_at", False),)),
        }
    return st.session_state.data


def invalidate():
    st.session_state.pop("data", None)


# ---------------- Writing data ----------------
def save_budget(payload, existing_id=None):
    if existing_id:
        rows = db().table("budgets").update(payload).eq("id", existing_id).execute().data
    else:
        rows = db().table("budgets").insert(payload).execute().data
    invalidate()
    return rows[0]


def delete_budget(budget_id):
    db().table("budgets").delete().eq("id", budget_id).execute()
    invalidate()


def add_transaction(row):
    db().table("transactions").insert(row).execute()
    invalidate()


def add_recurring_transactions(rows):
    """Insert recurring entries, silently skipping ones already created. Returns how many were new."""
    if not rows:
        return 0
    res = (db().table("transactions")
           .upsert(rows, on_conflict="budget_id,recurring_id", ignore_duplicates=True).execute())
    invalidate()
    return len(res.data or [])


def update_transaction(tx_id, fields):
    db().table("transactions").update(fields).eq("id", tx_id).execute()
    invalidate()


def delete_transaction(tx_id):
    db().table("transactions").delete().eq("id", tx_id).execute()
    invalidate()


def add_recurring(row):
    db().table("recurring_items").insert(row).execute()
    invalidate()


def set_recurring_active(item_id, active):
    db().table("recurring_items").update({"active": active}).eq("id", item_id).execute()
    invalidate()


def delete_recurring(item_id):
    db().table("recurring_items").delete().eq("id", item_id).execute()
    invalidate()


def add_goal(row):
    db().table("goals").insert(row).execute()
    invalidate()


def delete_goal(goal_id):
    db().table("goals").delete().eq("id", goal_id).execute()
    invalidate()
