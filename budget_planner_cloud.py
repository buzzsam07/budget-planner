"""
Budget Planner - hosted version (Streamlit + Google Sheets)

- On Streamlit Community Cloud: password-protected, history saved in Google Sheets.
- On your own computer with no secrets set up: no password, history saved to a local CSV.
"""

import hmac
import os
import re
from datetime import date

import pandas as pd
import streamlit as st

# ---------- EDIT THESE ----------
NAMES = ("Sam", "Priyanka")          # change "Partner" to her name

SPLIT = {                            # must add up to 100
    "Investments": 50,
    "Variable expenses": 30,
    "Emergency fund": 20,
}
# --------------------------------

LOCAL_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget_history_web.csv")


def secret(key, default=None):
    """Read a Streamlit secret without crashing when none are configured."""
    try:
        return st.secrets[key]
    except Exception:
        return default


USE_SHEETS = secret("gcp_service_account") is not None


def money(x):
    """Format in Indian grouping: ₹1,25,000."""
    n = int(round(abs(x)))
    s = str(n)
    if len(s) > 3:
        s = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3]) + "," + s[-3:]
    return ("-" if x < 0 else "") + "₹" + s


# ---------------- Password ----------------
def check_password():
    pw = secret("app_password")
    if not pw:                      # no password set (local use)
        return True
    if st.session_state.get("authed"):
        return True
    entry = st.text_input("Password", type="password")
    if entry:
        if hmac.compare_digest(entry, str(pw)):
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Wrong password.")
    return False


# ---------------- Storage ----------------
@st.cache_resource
def get_worksheet():
    import gspread

    client = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    return client.open(secret("sheet_name", "Budget History")).sheet1


def load_history():
    if USE_SHEETS:
        return pd.DataFrame(get_worksheet().get_all_records())
    if os.path.exists(LOCAL_CSV):
        return pd.read_csv(LOCAL_CSV)
    return pd.DataFrame()


def save_row(row):
    if USE_SHEETS:
        ws = get_worksheet()
        if not ws.get_all_values():          # empty sheet, write headers first
            ws.append_row(list(row.keys()))
        ws.append_row(list(row.values()))
    else:
        exists = os.path.exists(LOCAL_CSV)
        pd.DataFrame([row]).to_csv(LOCAL_CSV, mode="a", header=not exists, index=False)


# ---------------- App ----------------
st.set_page_config(page_title="Budget Planner", page_icon="💰")
st.title("💰 Budget Planner")

if sum(SPLIT.values()) != 100:
    st.error(f"Split adds up to {sum(SPLIT.values())}%, it must be 100%. Fix SPLIT at the top of the file.")
    st.stop()

if not check_password():
    st.stop()

tab_plan, tab_history = st.tabs(["This month", "History"])

with tab_plan:
    with st.form("inputs"):
        month = st.text_input("Month", value=date.today().strftime("%b %Y"))
        c1, c2 = st.columns(2)
        inc1 = c1.number_input(f"{NAMES[0]}'s income (₹)", min_value=0.0, step=1000.0, format="%.0f")
        inc2 = c2.number_input(f"{NAMES[1]}'s income (₹)", min_value=0.0, step=1000.0, format="%.0f")
        overhead = st.number_input("Overhead expenses (₹)", min_value=0.0, step=500.0, format="%.0f")
        submitted = st.form_submit_button("Calculate")

    if submitted:
        total = inc1 + inc2
        if total <= 0:
            st.session_state.result = None
            st.error("Total income is zero.")
        elif overhead >= total:
            st.session_state.result = None
            st.error("Overheads are higher than income, nothing left to split.")
        else:
            budgetable = total - overhead
            st.session_state.result = {
                "month": month.strip(),
                "incomes": (inc1, inc2),
                "overhead": overhead,
                "total": total,
                "budgetable": budgetable,
                "alloc": {c: budgetable * p / 100 for c, p in SPLIT.items()},
                "saved": False,
            }

    res = st.session_state.get("result")
    if res:
        total = res["total"]
        shares = [i / total for i in res["incomes"]]

        m1, m2, m3 = st.columns(3)
        m1.metric("Combined income", money(total))
        m2.metric("Overheads", money(res["overhead"]))
        m3.metric("Left to budget", money(res["budgetable"]))
        st.caption(f"{NAMES[0]} contributes {shares[0]:.0%}  |  {NAMES[1]} contributes {shares[1]:.0%}")

        table = pd.DataFrame(
            [
                {
                    "Bucket": cat,
                    "%": f"{pct}%",
                    "Total": money(res["alloc"][cat]),
                    NAMES[0]: money(res["alloc"][cat] * shares[0]),
                    NAMES[1]: money(res["alloc"][cat] * shares[1]),
                }
                for cat, pct in SPLIT.items()
            ]
        )
        st.table(table.set_index("Bucket"))

        if st.button("Save to history", disabled=res["saved"]):
            row = {
                "Month": res["month"],
                f"{NAMES[0]} income": res["incomes"][0],
                f"{NAMES[1]} income": res["incomes"][1],
                "Total income": total,
                "Overhead": res["overhead"],
                "Left to budget": res["budgetable"],
                **{c: round(v, 2) for c, v in res["alloc"].items()},
            }
            try:
                save_row(row)
                res["saved"] = True
                st.success("Saved to history.")
            except Exception as e:
                st.error(f"Couldn't save: {e}")

with tab_history:
    try:
        history = load_history()
    except Exception as e:
        history = pd.DataFrame()
        st.error(f"Couldn't load history: {e}")

    if history.empty:
        st.info("Nothing saved yet. Calculate a month and hit Save.")
    else:
        st.dataframe(history, hide_index=True)

        saving_cols = [c for c in ("Investments", "Emergency fund") if c in history.columns]
        if saving_cols and len(history) > 1:
            st.subheader("Cumulative amount set aside")
            st.line_chart(history.set_index("Month")[saving_cols].cumsum())

        st.download_button(
            "Download as CSV",
            history.to_csv(index=False).encode("utf-8"),
            file_name="budget_history.csv",
            mime="text/csv",
        )
