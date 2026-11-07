"""
Budget Planner - Streamlit + Supabase

Plan      calculator: split income into buckets (works without an account)
Track     log expenses / investments / savings per bucket, alerts, recurring items
Goals     progress bars for savings buckets (e.g. 6 months of essentials)
History   saved months, charts, exports
Account   login, households (share one budget), backup, password, delete account

Files this app needs in the same folder: bp_core.py, bp_db.py, requirements.txt,
and .streamlit/config.toml (theme). Database setup is in setup.sql.
"""

import time
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import bp_core as core
import bp_db as db

try:
    import extra_streamlit_components as stx
except Exception:                      # without it the app still works, just no "stay signed in"
    stx = None

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
COOKIE = "bp_rt"
MAX_PEOPLE = core.MAX_PEOPLE
money = core.money

st.set_page_config(page_title="Budget Planner", page_icon="💰", layout="centered")
st.markdown(
    """
    <style>
    .block-container {padding-top: 2.2rem; max-width: 760px;}
    h1 {font-weight: 650; letter-spacing: -0.02em;}
    [data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.25); border-radius: 14px; padding: .7rem 1rem;}
    [data-testid="stMetricLabel"] {opacity: .7;}
    footer {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Budget Planner")

AUTH = db.enabled()

# ---------------- Stay signed in (cookie) ----------------
cookies = None
if AUTH and stx is not None:
    try:
        cookies = stx.CookieManager(key="bp_cookies")
    except Exception:
        cookies = None


def logged_in():
    return "uid" in st.session_state


def set_logged_in(user, keep):
    st.session_state.uid = user.id
    st.session_state.user_email = user.email
    st.session_state.keep_signed = bool(keep and cookies is not None)
    for k in ("data", "hid", "households"):
        st.session_state.pop(k, None)


def logout():
    if cookies is not None:
        try:
            cookies.delete(COOKIE, key="del_rt")
        except Exception:
            pass
    db.sign_out()
    st.session_state.clear()
    st.session_state.restore_tried = True      # don't instantly sign back in from the cookie
    time.sleep(0.6)                            # give the browser a moment to drop the cookie
    st.rerun()


# Restore a saved login when the app opens
if AUTH and not logged_in() and cookies is not None and not st.session_state.get("restore_tried"):
    saved = cookies.get(COOKIE)
    if saved:
        st.session_state.restore_tried = True
        restored = None
        try:
            _, restored = db.restore_session(saved)
        except Exception:
            try:
                cookies.delete(COOKIE, key="del_stale")
            except Exception:
                pass
        if restored:
            set_logged_in(restored, keep=True)
            st.rerun()

# Keep the cookie in step with Supabase's rotating refresh token
if cookies is not None and logged_in() and st.session_state.get("keep_signed"):
    try:
        token = db.current_refresh_token()
        if token and token != st.session_state.get("saved_rt"):
            n = st.session_state.get("rt_ver", 0)
            cookies.set(COOKIE, token, key=f"set_rt_{n}",
                        expires_at=datetime.now() + timedelta(days=30), same_site="lax")
            st.session_state.saved_rt = token
            st.session_state.rt_ver = n + 1
    except Exception:
        pass

# ---------------- Household + data ----------------
hid = data = household = None
if AUTH and logged_in():
    try:
        households = db.my_households()
        ids = [h["id"] for h in households]
        if st.session_state.get("hid") not in ids:
            st.session_state.hid = ids[0]
        hid = st.session_state.hid
        household = next(h for h in households if h["id"] == hid)
        data = db.load_data(hid)
    except Exception as e:
        data = None
        st.error(f"Couldn't load your data: {e}. If this keeps happening, log out and back in.")

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))


# ---------------- Small helpers ----------------
def need_login():
    """Show a message and return True when a tab can't be used yet."""
    if not AUTH:
        st.info("Accounts aren't switched on for this app.")
        return True
    if not logged_in():
        st.info("Log in from the Account tab to use this.")
        return True
    if data is None:
        st.warning("Your data couldn't be loaded.")
        return True
    return False


def guard(key, options):
    """Drop a stale selection so a selectbox never holds a value that isn't in its options."""
    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]


def known_buckets():
    """{bucket: 'spend'|'saving'} for presets plus every bucket used in saved budgets."""
    out = dict(core.DEFAULT_TYPES)
    if data:
        for b in sorted(data["budgets"], key=lambda b: core.to_date(b["period"])):
            for c in b["allocations"]:
                out[c] = core.bucket_type(c, b.get("bucket_types"))
    return out


def latest_people():
    if data and data["budgets"]:
        return [p["name"] for p in data["budgets"][0]["people"]]
    return []


# ---------------- Plan ----------------
def plan_tab():
    today = date.today()
    last_people = (data["budgets"][0]["people"] if data and data["budgets"] else [])
    count = st.radio("How many people?", list(range(1, MAX_PEOPLE + 1)),
                     index=max(0, min(MAX_PEOPLE - 1, (len(last_people) or 2) - 1)), horizontal=True)

    st.markdown("**Budget split**")
    chosen = st.multiselect("Buckets to include", core.PRESET_BUCKETS, default=core.DEFAULT_ON)
    custom_raw = st.text_input("Add your own buckets (comma separated, press Enter)",
                               placeholder="e.g. Travel, Insurance, Gifts")
    customs, skipped = core.parse_custom_buckets(custom_raw)
    if skipped:
        st.warning(f"Skipped (name already used, reserved, or over the limit of {core.MAX_CUSTOM}): "
                   f"{', '.join(skipped)}")
    custom_savings = (st.multiselect("Which of your own buckets are savings (money you put away)?", customs)
                      if customs else [])

    all_buckets = chosen + customs
    types = {c: core.bucket_type(c) for c in chosen}
    types.update({c: ("saving" if c in custom_savings else "spend") for c in customs})

    split = {}
    grid = st.columns(2)
    for i, cat in enumerate(all_buckets):
        split[cat] = grid[i % 2].number_input(
            f"{cat} (%)", min_value=0.0, max_value=100.0, value=float(core.DEFAULT_SPLIT.get(cat, 0)),
            step=5.0, format="%g", key=f"pct_{cat}")
    split_total = sum(split.values())
    split_ok = bool(all_buckets) and abs(split_total - 100) < 0.01
    if not all_buckets:
        st.warning("Pick at least one bucket.")
    elif split_ok:
        st.success("Split total: 100% ✓")
    else:
        st.error(f"Split total: {split_total:g}%. It needs to add up to 100%.")

    with st.form("inputs"):
        c1, c2 = st.columns(2)
        month_i = c1.selectbox("Month", list(range(12)), index=today.month - 1,
                               format_func=lambda i: core.MONTHS[i])
        year = c2.selectbox("Year", [today.year - 1, today.year, today.year + 1], index=1)
        entries = []
        for i in range(count):
            c_name, c_inc = st.columns(2)
            default_name = last_people[i]["name"] if i < len(last_people) else f"Person {i + 1}"
            name = c_name.text_input(f"Person {i + 1} name", value=default_name, key=f"name_{i}")
            income = c_inc.number_input(f"Person {i + 1} income (₹)", min_value=0.0, step=1000.0,
                                        format="%.0f", key=f"inc_{i}")
            entries.append((name, income))
        overhead = st.number_input("Overhead expenses (₹)", min_value=0.0, step=500.0, format="%.0f")
        submitted = st.form_submit_button("Calculate")

    if submitted:
        names = [n.strip() or f"Person {i + 1}" for i, (n, _) in enumerate(entries)]
        incomes = [inc for _, inc in entries]
        total = sum(incomes)
        period = date(year, month_i + 1, 1)
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
                "month": period.strftime("%b %Y"), "period": period.isoformat(),
                "people": list(zip(names, incomes)), "overhead": overhead, "total": total,
                "budgetable": budgetable, "split": dict(split), "types": dict(types),
                "alloc": {c: budgetable * p / 100 for c, p in split.items()}, "saved": False,
            }

    res = st.session_state.get("result")
    if not res:
        return
    total, people = res["total"], res["people"]
    sh = [inc / total for _, inc in people]

    m1, m2, m3 = st.columns(3)
    m1.metric("Combined income", money(total))
    m2.metric("Overheads", money(res["overhead"]))
    m3.metric("Left to budget", money(res["budgetable"]))
    st.caption("  |  ".join(f"{n} contributes {s:.0%}" for (n, _), s in zip(people, sh)))

    table = pd.DataFrame([{
        "Bucket": cat, "%": f"{pct:g}%", "Total": money(res["alloc"][cat]),
        **{n: money(res["alloc"][cat] * s) for (n, _), s in zip(people, sh)},
    } for cat, pct in res["split"].items()])
    st.table(table.set_index("Bucket"))

    export_df = pd.DataFrame([{
        "Bucket": cat, "%": pct, "Total": round(res["alloc"][cat], 2),
        **{n: round(res["alloc"][cat] * s, 2) for (n, _), s in zip(people, sh)},
    } for cat, pct in res["split"].items()])
    fname = "budget_" + res["month"].replace(" ", "_")
    d1, d2 = st.columns(2)
    d1.download_button("Download Excel", core.to_excel({"Budget": export_df}),
                       file_name=fname + ".xlsx", mime=XLSX_MIME)
    d2.download_button("Download CSV", export_df.to_csv(index=False).encode("utf-8"),
                       file_name=fname + ".csv", mime="text/csv")

    if AUTH and logged_in() and data is not None:
        existing = next((b for b in data["budgets"] if str(b["period"]) == res["period"]), None)
        replace = False
        if existing:
            st.info(f"A {res['month']} budget is already saved. Tick the box to update it "
                    "(entries you've logged are kept).")
            replace = st.checkbox(f"Replace the saved {res['month']} budget", key="replace_ok")
        can_save = (not res["saved"]) and (existing is None or replace)
        if st.button("Save to household", disabled=not can_save):
            payload = {
                "household_id": hid, "month": res["month"], "period": res["period"],
                "overhead": res["overhead"], "total_income": total, "left_to_budget": res["budgetable"],
                "people": [{"name": n, "income": inc} for n, inc in people],
                "split": res["split"],
                "allocations": {c: round(v, 2) for c, v in res["alloc"].items()},
                "bucket_types": res["types"],
            }
            try:
                saved = db.save_budget(payload, existing["id"] if (existing and replace) else None)
                added = db.add_recurring_transactions(
                    core.recurring_to_transactions(data["recurring"], saved, hid))
                res["saved"] = True
                msg = f"Saved {res['month']} to your household."
                if added:
                    msg += f" {added} recurring entr{'y was' if added == 1 else 'ies were'} added."
                st.session_state.flash = msg
            except Exception as e:
                st.error(f"Couldn't save: {e}. If this keeps happening, log out and back in.")
            else:
                st.rerun()
    elif AUTH:
        st.info("Want to save this month and track spending? Log in from the Account tab.")


# ---------------- Track ----------------
def track_tab():
    if need_login():
        return
    budgets = data["budgets"]
    if not budgets:
        st.info("Save a month in the Plan tab first, then come back here to log what you spend and invest.")
        return

    today = date.today()
    carryover = bool(household["settings"].get("carryover"))
    all_bal = core.compute_all_balances(budgets, data["transactions"], carryover)

    ids = [b["id"] for b in budgets]
    labels = {b["id"]: f"{core.period_label(b['period'])} · income {money(b['total_income'])}" for b in budgets}
    guard("track_budget", ids)
    default_idx = next((i for i, b in enumerate(budgets)
                        if core.to_date(b["period"]).replace(day=1) == today.replace(day=1)), 0)
    bid = st.selectbox("Month", ids, index=default_idx, format_func=lambda i: labels[i], key="track_budget")
    budget = next(b for b in budgets if b["id"] == bid)
    balances = all_bal[bid]
    txs = [t for t in data["transactions"] if t["budget_id"] == bid]

    for level, text in core.insights(budget, balances, today):
        {"error": st.error, "warning": st.warning, "info": st.info}[level](text)

    # ----- Balances -----
    st.subheader("Balances")
    cols = st.columns(2)
    for i, b in enumerate(balances):
        with cols[i % 2].container(border=True):
            kind_tag = "saving" if b["type"] == "saving" else "spending"
            st.markdown(f"**{b['bucket']}**  \n<span style='opacity:.6;font-size:.8rem'>{kind_tag}</span>",
                        unsafe_allow_html=True)
            if b["remaining"] >= 0:
                big, small = money(b["remaining"]), (
                    f"left of {money(b['available'])}" if b["type"] == "spend"
                    else f"still to put in of {money(b['available'])}")
                st.markdown(f"<div style='font-size:1.5rem;font-weight:600'>{big}</div>", unsafe_allow_html=True)
            else:
                small = "over budget" if b["type"] == "spend" else "put in beyond the plan"
                st.markdown(f"<div style='font-size:1.5rem;font-weight:600;color:#d64545'>"
                            f"{money(abs(b['remaining']))}</div>", unsafe_allow_html=True)
            st.progress(min(max(b["pct"], 0.0), 1.0))
            note = small
            if b["carry"]:
                note += f" · includes {money(b['carry'])} carried over"
            if not b["tracked"]:
                note += " · no entries yet"
            st.caption(note)

    new_carry = st.toggle("Carry leftovers and overspending into the next month (spending buckets)",
                          value=carryover)
    if new_carry != carryover:
        try:
            db.update_household(hid, settings={**household["settings"], "carryover": new_carry})
            st.rerun()
        except Exception as e:
            st.error(f"Couldn't change that setting: {e}")

    # ----- Add an entry -----
    st.subheader("Add an entry")
    bucket_names = [b["bucket"] for b in balances]
    guard("tx_bucket", bucket_names)
    bucket = st.selectbox("Bucket", bucket_names, key="tx_bucket")
    btype = next(b["type"] for b in balances if b["bucket"] == bucket)
    kinds = core.allowed_kinds(btype)
    pay_names = [p["name"] for p in budget["people"]] + ["Joint"]
    p0 = core.to_date(budget["period"])
    default_date = today if (today.year, today.month) == (p0.year, p0.month) else p0
    with st.form("tx_form", clear_on_submit=True):
        kind = (st.selectbox("Type", kinds, format_func=lambda k: core.KIND_LABELS[k])
                if len(kinds) > 1 else kinds[0])
        c1, c2 = st.columns(2)
        amount = c1.number_input("Amount (₹)", min_value=0.0, step=100.0, format="%.2f")
        tx_date = c2.date_input("Date", value=default_date)
        category = st.text_input("Category (optional)", placeholder="e.g. Groceries, Mutual fund")
        paid_by = st.selectbox("Paid by", pay_names)
        note = st.text_input("Note (optional)")
        add = st.form_submit_button("Add entry")
    if add:
        if amount <= 0:
            st.error("Enter an amount above zero.")
        else:
            try:
                db.add_transaction({
                    "household_id": hid, "budget_id": bid, "tx_date": tx_date.isoformat(),
                    "bucket": bucket, "category": category.strip() or None, "kind": kind,
                    "amount": amount, "paid_by": paid_by, "note": note.strip() or None})
                st.session_state.flash = f"Added {money(amount)} to {bucket}."
            except Exception as e:
                st.error(f"Couldn't add that: {e}")
            else:
                st.rerun()

    # ----- Entries -----
    st.subheader("Entries")
    if txs:
        tdf = core.transactions_to_df(txs, budgets).drop(columns=["Budget month"])
        st.dataframe(tdf, hide_index=True)
        with st.expander("Edit or delete an entry"):
            lab = {t["id"]: f"{t['tx_date']} · {t['bucket']} · {money(t['amount'])}"
                            + (f" · {t['category']}" if t.get("category") else "") for t in txs}
            guard("edit_tx", list(lab))
            tid = st.selectbox("Entry", list(lab), format_func=lambda i: lab[i], key="edit_tx")
            t = next(x for x in txs if x["id"] == tid)
            e1, e2 = st.columns(2)
            new_amt = e1.number_input("Amount (₹)", min_value=0.01, value=float(t["amount"]),
                                      step=100.0, format="%.2f", key=f"ea_{tid}")
            new_date = e2.date_input("Date", value=core.to_date(t["tx_date"]), key=f"ed_{tid}")
            new_cat = st.text_input("Category", value=t.get("category") or "", key=f"ec_{tid}")
            new_note = st.text_input("Note", value=t.get("note") or "", key=f"en_{tid}")
            b1, b2 = st.columns(2)
            if b1.button("Save changes", key=f"es_{tid}"):
                try:
                    db.update_transaction(tid, {"amount": new_amt, "tx_date": new_date.isoformat(),
                                                "category": new_cat.strip() or None,
                                                "note": new_note.strip() or None})
                    st.session_state.flash = "Entry updated."
                except Exception as e:
                    st.error(f"Couldn't update: {e}")
                else:
                    st.rerun()
            if b2.button("Delete entry", key=f"edel_{tid}"):
                try:
                    db.delete_transaction(tid)
                    st.session_state.flash = "Entry deleted."
                except Exception as e:
                    st.error(f"Couldn't delete: {e}")
                else:
                    st.rerun()
    else:
        st.caption("No entries yet for this month.")

    # ----- Who has put in what -----
    rows = core.contributions(budget, txs)
    if rows:
        st.subheader("Who has put in what")
        cdf = pd.DataFrame(rows)
        for c in ("Expected share", "Logged", "Difference"):
            cdf[c] = cdf[c].map(money)
        st.dataframe(cdf, hide_index=True)
        st.caption("Expected share is each person's share of income. Joint entries are split the same way.")

    # ----- Recurring items -----
    with st.expander("Recurring items (rent, SIPs, subscriptions)"):
        items = data["recurring"]
        if not items:
            st.caption("Nothing yet. Items you add here are logged automatically whenever you save a month.")
        for it in items:
            r1, r2, r3 = st.columns([5, 2, 2])
            r1.markdown(f"**{it['name']}**  \n<span style='opacity:.6;font-size:.85rem'>"
                        f"{it['bucket']} · {money(it['amount'])} · day {it['day_of_month']}"
                        f"{' · ' + it['paid_by'] if it.get('paid_by') else ''}</span>",
                        unsafe_allow_html=True)
            active = r2.checkbox("Active", value=it["active"], key=f"ra_{it['id']}")
            if active != it["active"]:
                try:
                    db.set_recurring_active(it["id"], active)
                except Exception as e:
                    st.error(f"Couldn't change that: {e}")
                else:
                    st.rerun()
            if r3.button("Delete", key=f"rd_{it['id']}"):
                try:
                    db.delete_recurring(it["id"])
                except Exception as e:
                    st.error(f"Couldn't delete: {e}")
                else:
                    st.rerun()

        if items and st.button("Add this month's recurring entries now", key="apply_rec"):
            try:
                n = db.add_recurring_transactions(core.recurring_to_transactions(items, budget, hid))
                st.session_state.flash = (f"Added {n} recurring entr{'y' if n == 1 else 'ies'} to "
                                          f"{core.period_label(budget['period'])}." if n
                                          else "Everything recurring is already logged for this month.")
            except Exception as e:
                st.error(f"Couldn't add them: {e}")
            else:
                st.rerun()

        st.markdown("**Add a recurring item**")
        kb = known_buckets()
        rbuckets = list(kb)
        guard("rc_bucket", rbuckets)
        rb = st.selectbox("Bucket", rbuckets, key="rc_bucket")
        rkinds = core.allowed_kinds(kb[rb])
        with st.form("rc_form", clear_on_submit=True):
            rname = st.text_input("Name", placeholder="e.g. Rent, Index fund SIP, Netflix")
            rkind = (st.selectbox("Type", rkinds, format_func=lambda k: core.KIND_LABELS[k])
                     if len(rkinds) > 1 else rkinds[0])
            f1, f2 = st.columns(2)
            ramount = f1.number_input("Amount (₹)", min_value=0.0, step=100.0, format="%.2f")
            rday = f2.number_input("Day of month", min_value=1, max_value=28, value=1, step=1)
            rpaid = st.selectbox("Paid by", (latest_people() or []) + ["Joint"])
            rcat = st.text_input("Category (optional)")
            radd = st.form_submit_button("Add recurring item")
        if radd:
            if not rname.strip() or ramount <= 0:
                st.error("Give it a name and an amount above zero.")
            else:
                try:
                    db.add_recurring({"household_id": hid, "name": rname.strip(), "bucket": rb,
                                      "category": rcat.strip() or None, "kind": rkind, "amount": ramount,
                                      "day_of_month": int(rday), "paid_by": rpaid})
                    st.session_state.flash = f"Added recurring item: {rname.strip()}."
                except Exception as e:
                    st.error(f"Couldn't add it: {e}")
                else:
                    st.rerun()


# ---------------- Goals ----------------
def goals_tab():
    if need_login():
        return
    totals = core.saved_totals(data["transactions"])
    goals = data["goals"]
    if not goals:
        st.info("Goals show how far a savings bucket has come. Entries you log as deposits and withdrawals "
                "move the bar, so log them in the Track tab.")
    for g in goals:
        s = core.goal_status(g, totals, data["budgets"])
        with st.container(border=True):
            st.markdown(f"**{g['name']}**  \n<span style='opacity:.6;font-size:.85rem'>{g['bucket']}</span>",
                        unsafe_allow_html=True)
            st.progress(s["pct"])
            st.caption(f"{money(s['saved'])} of {money(s['target'])} ({s['pct']:.0%})")
            if g["target_type"] == "months_essentials":
                st.caption(f"Target is {g['months']} months of essentials. Essentials are "
                           f"{money(s['essentials'])} a month (overhead plus essential expenses, averaged "
                           f"over your last {s['essentials_months']} saved budget(s)).")
            if s["months_left"] == 0:
                st.caption("Goal reached.")
            elif s["months_left"] is not None:
                st.caption(f"About {s['months_left']} month(s) to go at your planned "
                           f"{money(s['avg_planned'])} a month.")
            elif s["target"] > 0:
                st.caption("Include this bucket in a saved budget to see a time estimate.")
            if st.button("Delete goal", key=f"gd_{g['id']}"):
                try:
                    db.delete_goal(g["id"])
                except Exception as e:
                    st.error(f"Couldn't delete: {e}")
                else:
                    st.rerun()

    with st.expander("Add a goal", expanded=not goals):
        if not any(g["bucket"] == "Emergency fund" for g in goals):
            if st.button("Quick add: 6-month emergency fund"):
                try:
                    db.add_goal({"household_id": hid, "name": "Emergency fund", "bucket": "Emergency fund",
                                 "target_type": "months_essentials", "months": 6, "starting_amount": 0})
                    st.session_state.flash = "Added a 6-month emergency fund goal."
                except Exception as e:
                    st.error(f"Couldn't add it: {e}")
                else:
                    st.rerun()
        kb = known_buckets()
        saving_buckets = [c for c, t in kb.items() if t == "saving"]
        gname = st.text_input("Goal name", placeholder="e.g. Emergency fund, Holiday fund", key="g_name")
        gbucket = st.selectbox("Savings bucket it counts towards", saving_buckets, key="g_bucket")
        gtype = st.radio("Target", ["fixed", "months_essentials"], horizontal=True, key="g_type",
                         format_func=lambda x: "A fixed amount" if x == "fixed" else "Months of essentials")
        if gtype == "fixed":
            gtarget = st.number_input("Target amount (₹)", min_value=0.0, step=10000.0, format="%.0f", key="g_amt")
            gmonths = None
        else:
            gmonths = st.number_input("Number of months", min_value=1, max_value=60, value=6, step=1, key="g_mo")
            gtarget = None
        gstart = st.number_input("Already saved towards this (₹)", min_value=0.0, step=1000.0,
                                 format="%.0f", key="g_start")
        if st.button("Add goal", key="g_add"):
            if not gname.strip() or (gtype == "fixed" and not gtarget):
                st.error("Give the goal a name and a target.")
            else:
                try:
                    db.add_goal({"household_id": hid, "name": gname.strip(), "bucket": gbucket,
                                 "target_type": gtype, "target_amount": gtarget,
                                 "months": int(gmonths) if gmonths else None, "starting_amount": gstart})
                    st.session_state.flash = f"Added goal: {gname.strip()}."
                except Exception as e:
                    st.error(f"Couldn't add it: {e}")
                else:
                    st.rerun()


# ---------------- History ----------------
def history_tab():
    if need_login():
        return
    budgets = data["budgets"]
    if not budgets:
        st.info("Nothing saved yet. Calculate a month in the Plan tab and save it.")
        return
    df = core.budgets_to_df(budgets)
    st.dataframe(df, hide_index=True)

    bucket_cols = df.attrs.get("buckets", [])
    if bucket_cols and len(df) > 1:
        st.subheader("Planned amounts over time")
        default_cols = [c for c in ("Investments", "Emergency fund") if c in bucket_cols] or bucket_cols[:1]
        picked = st.multiselect("Buckets to chart", bucket_cols, default=default_cols)
        if picked:
            chart = df.iloc[::-1][picked].fillna(0).cumsum().reset_index(drop=True)
            chart.index = pd.RangeIndex(1, len(chart) + 1, name="Entry #")
            st.line_chart(chart)

    st.subheader("Export")
    tdf = core.transactions_to_df(data["transactions"], budgets)
    e1, e2 = st.columns(2)
    e1.download_button("Budgets (Excel)", core.to_excel({"Budgets": df}),
                       file_name="budgets.xlsx", mime=XLSX_MIME)
    e2.download_button("All entries (Excel)", core.to_excel({"Entries": tdf}),
                       file_name="entries.xlsx", mime=XLSX_MIME)

    with st.expander("Delete a saved month"):
        labels = {b["id"]: f"{core.period_label(b['period'])} · income {money(b['total_income'])}" for b in budgets}
        pick = st.selectbox("Month", list(labels), format_func=lambda i: labels[i], key="del_budget")
        st.caption("This also deletes every entry logged against that month.")
        if st.button("Delete this month", key="del_budget_btn"):
            try:
                db.delete_budget(pick)
                st.session_state.flash = "Month deleted."
            except Exception as e:
                st.error(f"Couldn't delete: {e}")
            else:
                st.rerun()


# ---------------- Account ----------------
def auth_forms():
    t1, t2, t3 = st.tabs(["Log in", "Sign up", "Forgot password"])

    with t1:
        with st.form("login"):
            email = st.text_input("Email")
            pw = st.text_input("Password", type="password")
            keep = st.checkbox("Keep me signed in on this device")
            go = st.form_submit_button("Log in")
        if go:
            try:
                res = db.sign_in(email, pw)
            except Exception as e:
                st.error(f"Couldn't log in: {e}")
            else:
                set_logged_in(res.user, keep)
                st.rerun()

    with t2:
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
                    res = db.sign_up(email2, pw2)
                except Exception as e:
                    st.error(f"Couldn't sign up: {e}")
                else:
                    if res.session:
                        set_logged_in(res.user, False)
                        st.rerun()
                    else:
                        st.success("Account created. Check your email and click the confirmation link, then log in.")

    with t3:
        st.caption("We'll email you a code to set a new password.")
        fp_email = st.text_input("Email", key="fp_email")
        if st.button("Send code", key="fp_send"):
            try:
                db.send_reset_code(fp_email)
                st.success("If that email has an account, a code is on its way.")
            except Exception as e:
                st.error(f"Couldn't send the code: {e}")
        with st.form("fp_form"):
            code = st.text_input("Code from the email")
            new_pw = st.text_input("New password (8+ characters)", type="password")
            go3 = st.form_submit_button("Set new password")
        if go3:
            if len(new_pw) < 8:
                st.error("Use at least 8 characters.")
            else:
                try:
                    user = db.reset_password_with_code(fp_email, code, new_pw)
                except Exception as e:
                    st.error(f"That didn't work: {e}")
                else:
                    set_logged_in(user, False)
                    st.session_state.flash = "Password changed. You're signed in."
                    st.rerun()


def switch_household():
    st.session_state.hid = st.session_state.hh_switch


def account_panel():
    st.write(f"Signed in as **{st.session_state.user_email}**")
    if st.button("Log out", key="logout_btn"):
        logout()

    st.subheader("Household")
    households = st.session_state.get("households") or []
    if len(households) > 1:
        labels, seen = {}, {}
        for h in households:                      # same-named households get told apart by join date
            base = f"{h['name']} · joined {h['joined_at'][:10]}" if h.get("joined_at") else h["name"]
            seen[base] = seen.get(base, 0) + 1
            labels[h["id"]] = base if seen[base] == 1 else f"{base} (#{seen[base]})"
        st.session_state["hh_switch"] = hid       # keep the dropdown in step with the active household
        st.selectbox("Active household", list(labels), format_func=lambda i: labels[i],
                     key="hh_switch", on_change=switch_household)
    n1, n2 = st.columns([3, 1])
    new_name = n1.text_input("Household name", value=household["name"], key="hh_name")
    n2.write("")
    n2.write("")
    if n2.button("Rename", key="hh_rename") and new_name.strip() and new_name.strip() != household["name"]:
        try:
            db.update_household(hid, name=new_name.strip())
        except Exception as e:
            st.error(f"Couldn't rename: {e}")
        else:
            st.rerun()
    try:
        members = db.household_members(hid)
        st.caption("Members: " + ", ".join(m.get("email") or "member" for m in members))
    except Exception:
        pass

    st.markdown("**Invite your partner**")
    st.caption("Make a code, send it to them, and they enter it under 'Join a household' after they sign up. "
               "Codes last 7 days. Everyone in a household sees and edits the same budget.")
    if st.button("Create invite code", key="inv_btn"):
        try:
            st.session_state.invite_code = db.create_invite(hid)
        except Exception as e:
            st.error(f"Couldn't create a code: {e}")
    if st.session_state.get("invite_code"):
        st.code(st.session_state.invite_code)

    st.markdown("**Join a household**")
    j1, j2 = st.columns([3, 1])
    code = j1.text_input("Invite code", key="join_code", label_visibility="collapsed", placeholder="Invite code")
    if j2.button("Join", key="join_btn") and code.strip():
        try:
            new_hid = db.join_household(code)
        except Exception as e:
            st.error(f"Couldn't join: {e}")
        else:
            st.session_state.hid = new_hid
            st.session_state.pop("data", None)
            st.session_state.flash = "Joined the household. You're now looking at its budget."
            st.rerun()

    with st.expander("Leave this household"):
        st.caption("Your entries stay with the household. If you're the last member, the household and "
                   "everything in it is deleted.")
        sure = st.checkbox("Yes, leave this household", key="leave_ok")
        if st.button("Leave household", key="leave_btn", disabled=not sure):
            try:
                db.leave_household(hid)
                st.session_state.pop("hid", None)
                st.session_state.flash = "You left the household."
            except Exception as e:
                st.error(f"Couldn't leave: {e}")
            else:
                st.rerun()

    st.subheader("Backup")
    if data is not None:
        st.download_button("Download full backup (Excel)", core.build_backup(data),
                           file_name=f"budget_backup_{date.today().isoformat()}.xlsx", mime=XLSX_MIME)
        st.caption("Budgets, entries, recurring items and goals for this household, one sheet each.")

    st.subheader("Password")
    with st.form("pw_form", clear_on_submit=True):
        p1 = st.text_input("New password (8+ characters)", type="password")
        p2 = st.text_input("Confirm new password", type="password")
        pw_go = st.form_submit_button("Change password")
    if pw_go:
        if p1 != p2:
            st.error("Passwords don't match.")
        elif len(p1) < 8:
            st.error("Use at least 8 characters.")
        else:
            try:
                db.change_password(p1)
                st.success("Password changed.")
            except Exception as e:
                st.error(f"Couldn't change it: {e}")

    with st.expander("Delete my account"):
        st.caption("This permanently deletes your login. Households where you're the only member are deleted "
                   "with everything in them. Shared households stay for the other members. This can't be undone.")
        confirm = st.text_input("Type DELETE to confirm", key="del_confirm")
        if st.button("Delete my account", key="del_btn", disabled=confirm.strip() != "DELETE"):
            try:
                db.delete_account()
            except Exception as e:
                st.error(f"Couldn't delete the account: {e}")
            else:
                logout()


def account_tab():
    if not AUTH:
        st.info("Accounts aren't switched on for this app.")
    elif not logged_in():
        st.caption("You can use the Plan tab without an account. Log in to save months, track spending, "
                   "set goals and share a budget with a partner.")
        auth_forms()
    elif data is None:
        st.warning("Your data couldn't be loaded, so only login options are available.")
        if st.button("Log out", key="logout_err"):
            logout()
    else:
        account_panel()


# ---------------- Layout ----------------
tab_plan, tab_track, tab_goals, tab_history, tab_account = st.tabs(
    ["Plan", "Track", "Goals", "History", "Account"])
with tab_plan:
    plan_tab()
with tab_track:
    track_tab()
with tab_goals:
    goals_tab()
with tab_history:
    history_tab()
with tab_account:
    account_tab()
