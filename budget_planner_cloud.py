"""
Budget Planner - hosted version (Streamlit + Google Sheets)

- Supports 1 to 4 people, names typed in the app.
- Pick which buckets to use from a dropdown and set a percentage for each (must total 100%).
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
PRESET_BUCKETS = ["Essential expenses", "Investments", "Variable expenses", "Emergency fund"]
 
DEFAULT_SPLIT = {                    # pre-filled percentages
    "Essential expenses": 40,
    "Investments": 30,
    "Variable expenses": 20,
    "Emergency fund": 10,
}
 
MAX_PEOPLE = 4
DEFAULT_NAMES = ["Sam", "Priyanka", "Person 3", "Person 4"]
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
            row = {"Month": res["month"]}
            for i in range(MAX_PEOPLE):
                if i < len(people):
                    row[f"Person {i + 1}"] = people[i][0]
                    row[f"Income {i + 1}"] = people[i][1]
                else:
                    row[f"Person {i + 1}"] = ""
                    row[f"Income {i + 1}"] = ""
            row["Total income"] = total
            row["Overhead"] = res["overhead"]
            row["Left to budget"] = res["budgetable"]
            for c in PRESET_BUCKETS:             # buckets not used this month save as 0
                row[c] = round(res["alloc"].get(c, 0.0), 2)
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
