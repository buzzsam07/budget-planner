"""Pure logic for the Budget Planner. No Streamlit and no database calls in here."""

import calendar
import io
import math
import re
from datetime import date

import pandas as pd

# ---------- Settings you may want to edit ----------
PRESET_BUCKETS = ["Essential expenses", "Investments", "Variable expenses", "Emergency fund", "Tax set-aside"]
DEFAULT_ON = ["Essential expenses", "Investments", "Variable expenses", "Emergency fund"]
DEFAULT_SPLIT = {                      # pre-filled percentages
    "Essential expenses": 40,
    "Investments": 30,
    "Variable expenses": 20,
    "Emergency fund": 10,
    "Tax set-aside": 10,
}
# "spend" buckets are money you spend (balance goes down as you spend).
# "saving" buckets are money you put away (balance fills up as you deposit).
DEFAULT_TYPES = {
    "Essential expenses": "spend",
    "Variable expenses": "spend",
    "Investments": "saving",
    "Emergency fund": "saving",
    "Tax set-aside": "saving",
}
MAX_PEOPLE = 4
MAX_CUSTOM = 8
# ---------------------------------------------------

MONTHS = list(calendar.month_abbr)[1:]
KIND_LABELS = {
    "spend": "Expense",
    "deposit": "Deposit (invested / saved)",
    "withdraw": "Withdrawal",
}
RESERVED_NAMES = {"month", "saved on", "total income", "overhead", "left to budget"} | {
    f"{p} {i}" for i in range(1, MAX_PEOPLE + 1) for p in ("person", "income")
}


# ---------------- Small helpers ----------------
def money(x):
    """Format in Indian grouping: ₹1,25,000."""
    n = int(round(abs(float(x))))
    s = str(n)
    if len(s) > 3:
        s = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3]) + "," + s[-3:]
    return ("-" if float(x) < 0 else "") + "₹" + s


def to_date(v):
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def period_label(v):
    return to_date(v).strftime("%b %Y")


def bucket_type(bucket, types=None):
    t = (types or {}).get(bucket)
    return t if t in ("spend", "saving") else DEFAULT_TYPES.get(bucket, "spend")


def allowed_kinds(btype):
    return ["spend"] if btype == "spend" else ["deposit", "withdraw"]


def parse_custom_buckets(raw, max_custom=MAX_CUSTOM):
    """Turn 'Travel, Insurance' into (['Travel', 'Insurance'], [skipped names])."""
    customs, skipped = [], []
    taken = {c.lower() for c in PRESET_BUCKETS}
    for part in raw.split(","):
        nm = part.strip()[:30]
        if not nm:
            continue
        if nm.lower() in RESERVED_NAMES or nm.lower() in taken or len(customs) >= max_custom:
            skipped.append(nm)
        else:
            customs.append(nm)
            taken.add(nm.lower())
    return customs, skipped


def shares(people):
    """{'Sam': 0.67, 'Anna': 0.33} from the saved people list."""
    total = sum(float(p["income"]) for p in people)
    return {p["name"]: (float(p["income"]) / total if total else 0.0) for p in people}


# ---------------- Balances ----------------
def bucket_balances(budget, txs, carry=None):
    """Planned vs logged for every bucket of one budget.

    carry: {bucket: amount} brought over from the previous month (spend buckets only).
    """
    carry = carry or {}
    types = budget.get("bucket_types") or {}
    out = []
    for bucket, planned in (budget.get("allocations") or {}).items():
        planned = float(planned)
        c = float(carry.get(bucket, 0))
        available = planned + c
        mine = [t for t in txs if t["bucket"] == bucket]
        logged = sum(float(t["amount"]) for t in mine if t["kind"] in ("spend", "deposit"))
        withdrawn = sum(float(t["amount"]) for t in mine if t["kind"] == "withdraw")
        if available > 0:
            pct = logged / available
        else:
            pct = 1.0 if (logged > 0 or available < 0) else 0.0
        out.append({
            "bucket": bucket,
            "type": bucket_type(bucket, types),
            "planned": planned,
            "carry": c,
            "available": available,
            "logged": logged,
            "withdrawn": withdrawn,
            "remaining": available - logged,
            "pct": pct,
            "tracked": bool(mine),
        })
    return out


def compute_all_balances(budgets, txs, carryover=False):
    """{budget_id: [balances]} - optionally rolling leftovers/overspend into the next month."""
    by_budget = {}
    for t in txs:
        by_budget.setdefault(t["budget_id"], []).append(t)
    result, carry = {}, {}
    ordered = sorted(budgets, key=lambda b: (to_date(b["period"]), str(b.get("created_at", ""))))
    for b in ordered:
        bal = bucket_balances(b, by_budget.get(b["id"], []), carry if carryover else {})
        result[b["id"]] = bal
        carry = {x["bucket"]: x["remaining"] for x in bal if x["type"] == "spend"}
    return result


def saved_totals(txs):
    """Running total per bucket across all months: deposits minus withdrawals."""
    totals = {}
    for t in txs:
        a = float(t["amount"])
        if t["kind"] == "deposit":
            totals[t["bucket"]] = totals.get(t["bucket"], 0.0) + a
        elif t["kind"] == "withdraw":
            totals[t["bucket"]] = totals.get(t["bucket"], 0.0) - a
    return totals


def contributions(budget, txs):
    """Who has put in (or spent) their share of each bucket. Joint entries are split by income share."""
    people = budget.get("people") or []
    sh = shares(people)
    rows = []
    for bucket, planned in (budget.get("allocations") or {}).items():
        mine = [t for t in txs if t["bucket"] == bucket and t["kind"] in ("spend", "deposit")]
        if not mine:
            continue
        joint = sum(float(t["amount"]) for t in mine if (t.get("paid_by") or "Joint") not in sh)
        for p in people:
            n = p["name"]
            logged = sum(float(t["amount"]) for t in mine if t.get("paid_by") == n) + joint * sh[n]
            expected = float(planned) * sh[n]
            rows.append({"Bucket": bucket, "Person": n, "Expected share": expected,
                         "Logged": logged, "Difference": logged - expected})
    return rows


# ---------------- Goals ----------------
def monthly_essentials(budgets):
    """Average of overhead + essential spending over the latest (up to) 3 budgets."""
    recent = sorted(budgets, key=lambda b: to_date(b["period"]), reverse=True)[:3]
    if not recent:
        return 0.0, 0
    vals = [float(b.get("overhead") or 0) + float((b.get("allocations") or {}).get("Essential expenses", 0))
            for b in recent]
    return sum(vals) / len(vals), len(vals)


def goal_status(goal, totals, budgets):
    saved = float(goal.get("starting_amount") or 0) + totals.get(goal["bucket"], 0.0)
    essentials, n_months = 0.0, 0
    if goal.get("target_type") == "months_essentials":
        essentials, n_months = monthly_essentials(budgets)
        target = essentials * int(goal.get("months") or 0)
    else:
        target = float(goal.get("target_amount") or 0)
    pct = min(saved / target, 1.0) if target > 0 else 0.0

    recent = sorted(budgets, key=lambda b: to_date(b["period"]), reverse=True)[:3]
    planned = [float((b.get("allocations") or {}).get(goal["bucket"], 0)) for b in recent]
    avg_planned = sum(planned) / len(planned) if planned else 0.0
    months_left = None
    if target > 0 and saved >= target:
        months_left = 0
    elif target > saved and avg_planned > 0:
        months_left = math.ceil((target - saved) / avg_planned)
    return {"saved": saved, "target": target, "pct": pct, "months_left": months_left,
            "essentials": essentials, "essentials_months": n_months, "avg_planned": avg_planned}


# ---------------- Alerts and insights ----------------
def insights(budget, balances, today):
    """Alerts for the current month only. Returns [(level, text)] with level error/warning/info."""
    if not budget.get("period"):
        return []
    p = to_date(budget["period"])
    if (p.year, p.month) != (today.year, today.month):
        return []
    dim = calendar.monthrange(today.year, today.month)[1]
    days_left = dim - today.day
    elapsed = today.day
    msgs = []
    for b in balances:
        if b["available"] <= 0 and b["planned"] <= 0:
            continue
        name = b["bucket"]
        if b["type"] == "spend":
            if b["logged"] > b["available"]:
                msgs.append(("error", f"{name}: over budget by {money(b['logged'] - b['available'])}."))
            elif b["pct"] >= 0.8:
                msgs.append(("warning", f"{b['pct']:.0%} of {name} spent with {days_left} days left."))
            elif b["tracked"] and elapsed >= 5:
                projected = b["logged"] / elapsed * dim
                if projected > b["available"] * 1.05:
                    msgs.append(("info", f"At this pace {name} will run about "
                                         f"{money(projected - b['available'])} over by month end."))
        elif b["tracked"] and b["remaining"] > 0 and days_left <= 7:
            msgs.append(("info", f"{money(b['remaining'])} still to put into {name} with {days_left} days left."))
    return msgs


# ---------------- Recurring items ----------------
def recurring_to_transactions(items, budget, household_id):
    """Transactions that this month's recurring items should create (duplicates are ignored on save)."""
    period = to_date(budget["period"])
    dim = calendar.monthrange(period.year, period.month)[1]
    rows = []
    for it in items:
        if not it.get("active", True) or it["bucket"] not in (budget.get("allocations") or {}):
            continue
        rows.append({
            "household_id": household_id,
            "budget_id": budget["id"],
            "tx_date": period.replace(day=min(int(it["day_of_month"]), dim)).isoformat(),
            "bucket": it["bucket"],
            "category": it.get("category") or it["name"],
            "kind": it["kind"],
            "amount": float(it["amount"]),
            "paid_by": it.get("paid_by"),
            "note": f"Recurring: {it['name']}",
            "recurring_id": it["id"],
        })
    return rows


# ---------------- Tables and exports ----------------
def budgets_to_df(budgets):
    """Flat table for History. Only people columns and buckets that were actually used."""
    if not budgets:
        return pd.DataFrame()
    max_people = max(len(b["people"]) for b in budgets)
    used = {c for b in budgets for c in b["allocations"]}
    buckets = [c for c in PRESET_BUCKETS if c in used]
    for b in sorted(budgets, key=lambda b: to_date(b["period"])):
        for c in b["allocations"]:
            if c not in buckets:
                buckets.append(c)
    out = []
    for b in budgets:
        d = {"Month": period_label(b["period"]), "Saved on": str(b["created_at"])[:10]}
        for i in range(max_people):
            p = b["people"][i] if i < len(b["people"]) else None
            d[f"Person {i + 1}"] = p["name"] if p else ""
            d[f"Income {i + 1}"] = p["income"] if p else None
        d["Total income"] = b["total_income"]
        d["Overhead"] = b["overhead"]
        d["Left to budget"] = b["left_to_budget"]
        for c in buckets:
            d[c] = b["allocations"].get(c)
        out.append(d)
    df = pd.DataFrame(out)
    df.attrs["buckets"] = buckets
    return df


def transactions_to_df(txs, budgets):
    label = {b["id"]: period_label(b["period"]) for b in budgets}
    rows = [{
        "Date": str(t["tx_date"]),
        "Budget month": label.get(t["budget_id"], ""),
        "Bucket": t["bucket"],
        "Type": KIND_LABELS.get(t["kind"], t["kind"]),
        "Amount": float(t["amount"]),
        "Category": t.get("category") or "",
        "Paid by": t.get("paid_by") or "",
        "Note": t.get("note") or "",
    } for t in txs]
    return pd.DataFrame(rows, columns=["Date", "Budget month", "Bucket", "Type", "Amount",
                                       "Category", "Paid by", "Note"])


def to_excel(sheets):
    """sheets: {'Sheet name': DataFrame} -> .xlsx bytes."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            (df if len(df.columns) else pd.DataFrame({"": []})).to_excel(xw, sheet_name=name[:31], index=False)
    return buf.getvalue()


def build_backup(data):
    """Everything in the household as one Excel workbook."""
    budgets = data["budgets"]
    recurring = pd.DataFrame([{
        "Name": r["name"], "Bucket": r["bucket"], "Type": KIND_LABELS.get(r["kind"], r["kind"]),
        "Amount": r["amount"], "Day of month": r["day_of_month"], "Paid by": r.get("paid_by") or "",
        "Active": r["active"],
    } for r in data["recurring"]])
    goals = pd.DataFrame([{
        "Goal": g["name"], "Bucket": g["bucket"], "Target type": g["target_type"],
        "Target amount": g.get("target_amount"), "Months of essentials": g.get("months"),
        "Starting amount": g.get("starting_amount"),
    } for g in data["goals"]])
    return to_excel({
        "Budgets": budgets_to_df(budgets),
        "Transactions": transactions_to_df(data["transactions"], budgets),
        "Recurring": recurring,
        "Goals": goals,
    })
