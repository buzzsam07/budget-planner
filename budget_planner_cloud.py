"""
Budget Planner - Streamlit + Supabase

- Anyone can use the planner with NO account and download their result (Excel/CSV).
- Logging in is optional: it unlocks saving each month and a personal history.
- Each logged-in user's history is private (Supabase row-level security).

Streamlit secrets (optional; without them the app runs as a login-free calculator):
    supabase_url = "https://xxxx.supabase.co"
    supabase_anon_key = "your anon / publishable key"
"""
import bp_core
import io
import re
from datetime import date

import pandas as pd
import streamlit as st

try:
    from supabase import create_client
except Exception:                      # library missing -> run without accounts
    create_client = None

# ---------- EDIT THESE ----------
PRESET_BUCKETS = ["Essential expenses", "Investments", "Variable expenses", "Emergency fund"]

DEFAULT_SPLIT = {                    # pre-filled percentages
    "Investments": 50,
    "Variable expenses": 30,
    "Emergency fund": 20,
}

MAX_PEOPLE = 4
DEFAULT_NAMES = ["Person 1", "Person 2", "Person 3", "Person 4"]   # pre-filled name boxes
MAX_CUSTOM = 8                       # most custom buckets one budget can have
# --------------------------------

# Custom buckets can't reuse the names of the history table's own columns
RESERVED_NAMES = {"month", "saved on", "total income", "overhead", "left to budget"} | {
    f"{p} {i}" for i in range(1, MAX_PEOPLE + 1) for p in ("person", "income")
}


def secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return None


def money(x):
    """Format in Indian grouping: ₹1,25,000."""
    n = int(round(abs(x)))
    s = str(n)
    if len(s) > 3:
        s = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3]) + "," + s[-3:]
    return ("-" if x < 0 else "") + "₹" + s


def to_excel(df):
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

st.set_page_config(page_title="Budget Planner", page_icon="💰")
st.title("💰 Budget Planner")

SB_URL = secret("supabase_url")
SB_KEY = secret("supabase_anon_key")
AUTH_ENABLED = bool(SB_URL and SB_KEY and create_client)
logged_in = "user_email" in st.session_state


# ---------------- Supabase (only used when logged in) ----------------
def client():
    """One Supabase client PER USER SESSION (never share one between users)."""
    if "sb" not in st.session_state:
        st.session_state.sb = create_client(SB_URL, SB_KEY)
    return st.session_state.sb


def db():
    sb = client()
    sb.auth.get_session()          # refreshes the login token if it has expired
    return sb


def load_rows(force=False):
    if force or st.session_state.get("rows") is None:
        res = db().table("budgets").select("*").order("created_at", desc=True).execute()
        st.session_state.rows = res.data
    return st.session_state.rows


def rows_to_df(rows):
    """Flat table for the History tab. Only includes as many people columns as
    were ever used, and only the buckets that appear in at least one saved month."""
    max_people = max(len(r["people"]) for r in rows)

    # presets first (fixed order), then any custom buckets in the order first used
    used = {c for r in rows for c in r["allocations"]}
    buckets = [c for c in PRESET_BUCKETS if c in used]
    for r in reversed(rows):                       # rows are newest-first
        for c in r["allocations"]:
            if c not in buckets:
                buckets.append(c)

    out = []
    for r in rows:
        d = {"Month": r["month"], "Saved on": str(r["created_at"])[:10]}
        for i in range(max_people):
            p = r["people"][i] if i < len(r["people"]) else None
            d[f"Person {i + 1}"] = p["name"] if p else ""
            d[f"Income {i + 1}"] = p["income"] if p else None
        d["Total income"] = r["total_income"]
        d["Overhead"] = r["overhead"]
        d["Left to budget"] = r["left_to_budget"]
        for c in buckets:
            d[c] = r["allocations"].get(c)         # blank if not used that month
        out.append(d)
    df = pd.DataFrame(out)
    df.attrs["buckets"] = buckets
    return df


def auth_screen():
    sb = client()
    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

    with tab_login:
        with st.form("login"):
            email = st.text_input("Email")
            pw = st.text_input("Password", type="password")
            go = st.form_submit_button("Log in")
        if go:
            try:
                res = sb.auth.sign_in_with_password({"email": email.strip(), "password": pw})
                st.session_state.user_email = res.user.email
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't log in: {e}")

    with tab_signup:
        with st.form("signup"):
            email2 = st.text_input("Email", key="su_email")
            pw2 = st.text_input("Password (8+ characters)", type="password", key="su_pw")
            pw3 = st.text_input("Confirm password", type="password", key="su_pw2")
            go2 = st.form_submit_button("Create account")
        if go2:
            if pw2 != pw3:
                st.error("Passwords don't match.")
            elif len(pw2) < 8:
                st.error("Use at least 8 characters.")
            else:
                try:
                    res = sb.auth.sign_up({"email": email2.strip(), "password": pw2})
                    if res.session:                      # email confirmation turned off
                        st.session_state.user_email = res.user.email
                        st.rerun()
                    else:
                        st.success("Account created. Check your email and click the confirmation link, then log in.")
                except Exception as e:
                    st.error(f"Couldn't sign up: {e}")


# ---------------- App ----------------
tab_plan, tab_history = st.tabs(["This month", "History"])

with tab_plan:
    # These sit outside the form so they update straight away
    count = st.radio("How many people?", list(range(1, MAX_PEOPLE + 1)), index=1, horizontal=True)

    st.markdown("**Budget split**")
    chosen = st.multiselect("Buckets to include", PRESET_BUCKETS, default=PRESET_BUCKETS)
    custom_raw = st.text_input(
        "Add your own buckets (comma separated, press Enter)",
        placeholder="e.g. Travel, Insurance, Gifts",
    )

    customs, skipped = [], []
    taken = {c.lower() for c in PRESET_BUCKETS}
    for part in custom_raw.split(","):
        nm = part.strip()[:30]
        if not nm:
            continue
        if nm.lower() in RESERVED_NAMES or nm.lower() in taken:
            skipped.append(nm)
        elif len(customs) >= MAX_CUSTOM:
            skipped.append(nm)
        else:
            customs.append(nm)
            taken.add(nm.lower())
    if skipped:
        st.warning(
            "Skipped (name already used, reserved, or over the limit of "
            f"{MAX_CUSTOM}): {', '.join(skipped)}"
        )

    all_buckets = chosen + customs
    split = {}
    grid = st.columns(2)
    for i, cat in enumerate(all_buckets):
        split[cat] = grid[i % 2].number_input(
            f"{cat} (%)", min_value=0.0, max_value=100.0, value=float(DEFAULT_SPLIT.get(cat, 0)),
            step=5.0, format="%g", key=f"pct_{cat}",
        )
    split_total = sum(split.values())
    split_ok = bool(all_buckets) and abs(split_total - 100) < 0.01
    if not all_buckets:
        st.warning("Pick at least one bucket.")
    elif split_ok:
        st.success("Split total: 100% ✓")
    else:
        st.error(f"Split total: {split_total:g}%. It needs to add up to 100%.")

    with st.form("inputs"):
        month = st.text_input("Month", value=date.today().strftime("%b %Y"))
        entries = []
        for i in range(count):
            c_name, c_inc = st.columns(2)
            name = c_name.text_input(f"Person {i + 1} name", value=DEFAULT_NAMES[i], key=f"name_{i}")
            income = c_inc.number_input(
                f"Person {i + 1} income (₹)", min_value=0.0, step=1000.0, format="%.0f", key=f"inc_{i}"
            )
            entries.append((name, income))
        overhead = st.number_input("Overhead expenses (₹)", min_value=0.0, step=500.0, format="%.0f")
        submitted = st.form_submit_button("Calculate")

    if submitted:
        names = [n.strip() or f"Person {i + 1}" for i, (n, _) in enumerate(entries)]
        incomes = [inc for _, inc in entries]
        total = sum(incomes)

        if not split_ok:
            st.session_state.result = None
            st.error("Fix the budget split first. It needs to total 100%.")
        elif len({n.lower() for n in names}) < len(names):
            st.session_state.result = None
            st.error("Each person needs a different name.")
        elif total <= 0:
            st.session_state.result = None
            st.error("Total income is zero.")
        elif overhead >= total:
            st.session_state.result = None
            st.error("Overheads are higher than income, nothing left to split.")
        else:
            budgetable = total - overhead
            st.session_state.result = {
                "month": month.strip(),
                "people": list(zip(names, incomes)),
                "overhead": overhead,
                "total": total,
                "budgetable": budgetable,
                "split": dict(split),
                "alloc": {c: budgetable * p / 100 for c, p in split.items()},
                "saved": False,
            }

    res = st.session_state.get("result")
    if res:
        total = res["total"]
        people = res["people"]
        shares = [inc / total for _, inc in people]

        m1, m2, m3 = st.columns(3)
        m1.metric("Combined income", money(total))
        m2.metric("Overheads", money(res["overhead"]))
        m3.metric("Left to budget", money(res["budgetable"]))
        st.caption("  |  ".join(f"{n} contributes {s:.0%}" for (n, _), s in zip(people, shares)))

        table = pd.DataFrame(
            [
                {
                    "Bucket": cat,
                    "%": f"{pct:g}%",
                    "Total": money(res["alloc"][cat]),
                    **{n: money(res["alloc"][cat] * s) for (n, _), s in zip(people, shares)},
                }
                for cat, pct in res["split"].items()
            ]
        )
        st.table(table.set_index("Bucket"))

        # Download this month's result (works without an account)
        export_df = pd.DataFrame(
            [
                {
                    "Bucket": cat,
                    "%": pct,
                    "Total": round(res["alloc"][cat], 2),
                    **{n: round(res["alloc"][cat] * s, 2) for (n, _), s in zip(people, shares)},
                }
                for cat, pct in res["split"].items()
            ]
        )
        fname = "budget_" + re.sub(r"[^A-Za-z0-9_-]+", "_", res["month"] or "month")
        d1, d2 = st.columns(2)
        d1.download_button("Download Excel", to_excel(export_df), file_name=fname + ".xlsx", mime=XLSX_MIME)
        d2.download_button(
            "Download CSV", export_df.to_csv(index=False).encode("utf-8"),
            file_name=fname + ".csv", mime="text/csv",
        )

        if logged_in:
            if st.button("Save to history", disabled=res["saved"]):
                try:
                    db().table("budgets").insert({
                        "month": res["month"],
                        "overhead": res["overhead"],
                        "total_income": total,
                        "left_to_budget": res["budgetable"],
                        "people": [{"name": n, "income": inc} for n, inc in people],
                        "split": res["split"],
                        "allocations": {c: round(v, 2) for c, v in res["alloc"].items()},
                    }).execute()
                    res["saved"] = True
                    st.session_state.rows = None          # reload history next time
                    st.success("Saved to history.")
                except Exception as e:
                    st.error(f"Couldn't save: {e}. If this keeps happening, log out and back in.")
        elif AUTH_ENABLED:
            st.info("Want to keep a history of your months? Log in or sign up in the History tab, then come back and save.")

with tab_history:
    if not AUTH_ENABLED:
        st.info("History isn't switched on for this app.")
    elif not logged_in:
        st.info("You don't need an account to use the planner. Log in only if you want to save months and see your history.")
        auth_screen()
    else:
        h1, h2 = st.columns([3, 1])
        h1.write(f"Logged in as **{st.session_state.user_email}**")
        if h2.button("Log out"):
            try:
                client().auth.sign_out()
            except Exception:
                pass
            st.session_state.clear()
            st.rerun()

        try:
            rows = load_rows()
        except Exception as e:
            rows = []
            st.error(f"Couldn't load history: {e}. If this keeps happening, log out and back in.")

        if not rows:
            st.info("Nothing saved yet. Calculate a month and hit Save.")
        else:
            df = rows_to_df(rows)
            st.dataframe(df, hide_index=True)

            bucket_cols = df.attrs.get("buckets", [])
            if bucket_cols and len(df) > 1:
                st.subheader("Cumulative amount set aside")
                default_cols = [c for c in ("Investments", "Emergency fund") if c in bucket_cols] or bucket_cols[:1]
                picked = st.multiselect("Buckets to chart", bucket_cols, default=default_cols)
                if picked:
                    chart = df.iloc[::-1][picked].fillna(0).cumsum().reset_index(drop=True)
                    chart.index = pd.RangeIndex(1, len(chart) + 1, name="Entry #")
                    st.line_chart(chart)

            st.subheader("Export")
            e1, e2 = st.columns(2)
            e1.download_button(
                "Download Excel", to_excel(df), file_name="my_budget_history.xlsx", mime=XLSX_MIME
            )
            e2.download_button(
                "Download CSV", df.to_csv(index=False).encode("utf-8"),
                file_name="my_budget_history.csv", mime="text/csv",
            )

            with st.expander("Delete an entry"):
                labels = {
                    f"{r['month']}  ·  saved {str(r['created_at'])[:10]}  ·  {money(r['total_income'])}": r["id"]
                    for r in rows
                }
                pick = st.selectbox("Entry", list(labels))
                if st.button("Delete this entry"):
                    try:
                        db().table("budgets").delete().eq("id", labels[pick]).execute()
                        st.session_state.rows = None
                        st.rerun()
                    except Exception as e:
                        st.error(f"Couldn't delete: {e}")
