"""
Budget Planner - multi-user version (Streamlit + Supabase)
 
- Users sign up / log in with email + password (handled by Supabase Auth).
- Each user's budgets are saved in Supabase and only they can see them.
- 1 to 4 people per budget, pick buckets and percentages, export to Excel/CSV.
 
Needs these Streamlit secrets:
    supabase_url = "https://xxxx.supabase.co"
    supabase_anon_key = "your anon / publishable key"
"""
 
import io
import re
from datetime import date
 
import pandas as pd
import streamlit as st
from supabase import create_client
 
# ---------- EDIT THESE ----------
PRESET_BUCKETS = ["Essential expenses", "Investments", "Variable expenses", "Emergency fund"]
 
DEFAULT_SPLIT = {                    # pre-filled percentages
    "Essential expenses": 40,
    "Investments": 30,
    "Variable expenses": 20,
    "Emergency fund": 10,
}
 
MAX_PEOPLE = 4
DEFAULT_NAMES = ["Person 1", "Person 2", "Person 3", "Person 4"]   # pre-filled name boxes
# --------------------------------
 
 
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
 
 
st.set_page_config(page_title="Budget Planner", page_icon="💰")
st.title("💰 Budget Planner")
 
SB_URL = secret("supabase_url")
SB_KEY = secret("supabase_anon_key")
if not SB_URL or not SB_KEY:
    st.error("Supabase isn't set up yet. Add supabase_url and supabase_anon_key to the app's Secrets.")
    st.stop()
 
 
# ---------------- Supabase ----------------
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
    out = []
    for r in rows:
        d = {"Month": r["month"], "Saved on": str(r["created_at"])[:10]}
        for i in range(MAX_PEOPLE):
            p = r["people"][i] if i < len(r["people"]) else None
            d[f"Person {i + 1}"] = p["name"] if p else ""
            d[f"Income {i + 1}"] = p["income"] if p else None
        d["Total income"] = r["total_income"]
        d["Overhead"] = r["overhead"]
        d["Left to budget"] = r["left_to_budget"]
        for c in PRESET_BUCKETS:
            d[c] = r["allocations"].get(c, 0)
        out.append(d)
    return pd.DataFrame(out)
 
 
# ---------------- Login / Sign up ----------------
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
 
 
if "user_email" not in st.session_state:
    auth_screen()
    st.stop()
 
with st.sidebar:
    st.write(f"Logged in as **{st.session_state.user_email}**")
    if st.button("Log out"):
        try:
            client().auth.sign_out()
        except Exception:
            pass
        st.session_state.clear()
        st.rerun()
 
# ---------------- App ----------------
tab_plan, tab_history = st.tabs(["This month", "History"])
 
with tab_plan:
    # These sit outside the form so they update straight away
    count = st.radio("How many people?", list(range(1, MAX_PEOPLE + 1)), index=1, horizontal=True)
 
    st.markdown("**Budget split**")
    chosen = st.multiselect("Buckets to include", PRESET_BUCKETS, default=PRESET_BUCKETS)
    split = {}
    grid = st.columns(2)
    for i, cat in enumerate(chosen):
        split[cat] = grid[i % 2].number_input(
            f"{cat} (%)", min_value=0.0, max_value=100.0, value=float(DEFAULT_SPLIT[cat]),
            step=5.0, format="%g", key=f"pct_{cat}",
        )
    split_total = sum(split.values())
    split_ok = bool(chosen) and abs(split_total - 100) < 0.01
    if not chosen:
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
 
with tab_history:
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
 
        cols = [c for c in ("Investments", "Emergency fund") if c in df.columns]
        if cols and len(df) > 1:
            st.subheader("Cumulative amount set aside")
            chart = df.iloc[::-1][cols].cumsum().reset_index(drop=True)
            chart.index = pd.RangeIndex(1, len(chart) + 1, name="Entry #")
            st.line_chart(chart)
 
        st.subheader("Export")
        buf = io.BytesIO()
        df.to_excel(buf, index=False, engine="openpyxl")
        e1, e2 = st.columns(2)
        e1.download_button(
            "Download Excel", buf.getvalue(), file_name="my_budget_history.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
