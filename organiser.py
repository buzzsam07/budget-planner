"""
BuzzKar internal task organiser: log in, get tickets assigned, work on them, close them.
Run:  streamlit run organiser.py
"""
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import db

CATEGORIES = ["Content/Copy", "Design", "Video", "Meta Ads", "Google Ads",
              "SEO", "Client call/Meeting", "Research", "Admin", "Other"]
PRIORITIES = ["Low", "Medium", "High", "Urgent"]
STATUSES = ["Open", "In Progress", "In Review", "Closed"]
ICON = {"Low": "🟢", "Medium": "🟡", "High": "🟠", "Urgent": "🔴"}
IST = ZoneInfo("Asia/Kolkata")

st.set_page_config(page_title="BuzzKar Organiser", page_icon="🗂️", layout="wide")


@st.cache_resource
def _init():
    db.init_db()
    return True


_init()

# One-time messages (set before a rerun) show as a toast that disappears by itself
if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"), icon="✅")


# ---------- auth screens ----------
def login_screen():
    box = st.empty()
    with box.container():
        st.title("🗂️ BuzzKar Organiser")
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in")
    if submitted:
        with st.spinner("Logging in..."):
            user = db.verify_login(username.strip(), password)
        if user:
            st.session_state["user"] = user
            box.empty()  # remove the login form right away; the app carries on below
        else:
            st.error("Wrong username or password.")


def password_form(user, key):
    """Returns True when the password was changed."""
    with st.form(key, clear_on_submit=True):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password (min 8 characters)", type="password")
        again = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Update password"):
            if not db.verify_login(user["username"], current):
                st.error("Current password is wrong.")
            elif new != again:
                st.error("New passwords don't match.")
            else:
                err = db.change_password(user["username"], new)
                if err:
                    st.error(err)
                else:
                    st.session_state["user"]["must_change"] = False
                    return True
    return False


if "user" not in st.session_state:
    login_screen()
if "user" not in st.session_state:
    st.stop()

user = st.session_state["user"]
me = user["name"]
is_admin = user["role"] == "admin"

if user["must_change"]:
    box = st.empty()
    with box.container():
        st.title("🔑 Set a new password")
        st.info("First login: please choose your own password before continuing.")
        changed = password_form(user, "pw_forced")
    if not changed:
        st.stop()
    box.empty()

# ---------- sidebar ----------
st.sidebar.markdown(f"Signed in as **{me}**" + (" (admin)" if is_admin else ""))
with st.sidebar.expander("Change password"):
    if password_form(user, "pw_side"):
        st.toast("Password updated", icon="✅")
if st.sidebar.button("Log out"):
    st.session_state.pop("user", None)
    st.rerun()

team = db.team_names()


# ---------- ticket helpers ----------
def fmt_ts(ts):
    return ts.astimezone(IST).strftime("%d %b %Y, %I:%M %p")


def table_view(df):
    if df.empty:
        st.info("Nothing here.")
        return
    show = df[["id", "title", "client", "priority", "status", "assignee", "due_date"]].copy()
    show["due_date"] = show["due_date"].dt.strftime("%d %b %Y").fillna("—")
    st.dataframe(show, use_container_width=True, hide_index=True)


def pick_ticket(df, key):
    if df.empty:
        return None
    options = {f"#{r.id} {ICON[r.priority]} {r.title}  —  {r.assignee} [{r.status}]": int(r.id)
               for r in df.itertuples()}
    return options[st.selectbox("Open a ticket", list(options), key=key)]


def ticket_panel(tid, prefix):
    t = db.get_ticket(tid)
    st.divider()
    st.subheader(f"#{t['id']} · {t['title']}")
    due = t["due_date"].strftime("%d %b %Y") if t["due_date"] else "no due date"
    st.caption(f"{ICON[t['priority']]} {t['priority']} · {t['client'] or 'No client'} · "
               f"{t['category']} · due {due} · created by {t['created_by']}")
    st.write(t["description"] or "_No description_")

    c1, c2, c3 = st.columns([2, 2, 1])
    status = c1.selectbox("Status", STATUSES, index=STATUSES.index(t["status"]), key=f"{prefix}_st_{tid}")
    names = team if t["assignee"] in team else team + [t["assignee"]]
    assignee = c2.selectbox("Assignee", names, index=names.index(t["assignee"]), key=f"{prefix}_as_{tid}")
    c3.write("")
    c3.write("")
    if c3.button("Save", key=f"{prefix}_save_{tid}"):
        db.set_status(tid, status, me)
        db.set_assignee(tid, assignee, me)
        st.rerun()

    if t["status"] != "Closed":
        if st.button("✅ Close ticket", key=f"{prefix}_close_{tid}", type="primary"):
            db.set_status(tid, "Closed", me)
            st.rerun()

    with st.form(f"{prefix}_upd_{tid}", clear_on_submit=True):
        note = st.text_area("Add an update")
        hours = st.number_input("Hours spent (optional)", min_value=0.0, max_value=24.0, step=0.25)
        if st.form_submit_button("Post update"):
            if note.strip():
                db.add_update(tid, me, note.strip(), hours)
                st.rerun()
            else:
                st.error("Write something first.")

    st.markdown("**History**")
    for u in db.get_updates(tid):
        hrs = f" · {u['hours']:g}h" if u["hours"] else ""
        st.markdown(f"- `{fmt_ts(u['created_at'])}` **{u['author']}**{hrs}: {u['note']}")


# ---------- tabs ----------
labels = ["My tickets", "All tickets", "New ticket"] + (["Admin"] if is_admin else [])
tabs = st.tabs(labels)
tab_mine, tab_all, tab_new = tabs[0], tabs[1], tabs[2]

with tab_mine:
    mine = db.list_tickets(assignee=me, open_only=True)
    st.caption(f"{len(mine)} open ticket(s) assigned to you")
    table_view(mine)
    tid = pick_ticket(mine, "mine_pick")
    if tid:
        ticket_panel(tid, "mine")

with tab_all:
    df = db.list_tickets()
    f1, f2, f3 = st.columns(3)
    who = f1.multiselect("Assignee", team)
    stat = f2.multiselect("Status", STATUSES, default=["Open", "In Progress", "In Review"])
    search = f3.text_input("Search title / client")
    if who:
        df = df[df["assignee"].isin(who)]
    if stat:
        df = df[df["status"].isin(stat)]
    if search:
        df = df[df["title"].str.contains(search, case=False, na=False)
                | df["client"].fillna("").str.contains(search, case=False)]
    table_view(df)
    tid = pick_ticket(df, "all_pick")
    if tid:
        ticket_panel(tid, "all")

with tab_new:
    with st.form("new_ticket", clear_on_submit=True):
        title = st.text_input("Title")
        description = st.text_area("Details / instructions")
        c1, c2, c3 = st.columns(3)
        client = c1.text_input("Client / Project")
        category = c2.selectbox("Category", CATEGORIES)
        priority = c3.selectbox("Priority", PRIORITIES, index=1)
        assignee = c1.selectbox("Assign to", team, index=team.index(me) if me in team else 0)
        due = c2.date_input("Due date (optional)", value=None)
        if st.form_submit_button("Create ticket"):
            if not title.strip():
                st.error("Give the ticket a title.")
            else:
                new_id = db.create_ticket(title.strip(), description.strip(), client.strip(),
                                          category, priority, assignee, me, due)
                st.session_state["flash"] = f"Ticket #{new_id} created and assigned to {assignee}"
                st.rerun()

if is_admin:
    with tabs[3]:
        df = db.list_tickets()
        if df.empty:
            st.info("No tickets yet.")
        else:
            open_df = df[df["status"] != "Closed"]
            overdue = open_df[open_df["due_date"] < pd.Timestamp.today().normalize()]
            closed_week = df[df["closed_at"] >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)]

            m1, m2, m3 = st.columns(3)
            m1.metric("Open tickets", len(open_df))
            m2.metric("Overdue", len(overdue))
            m3.metric("Closed in last 7 days", len(closed_week))

            if not open_df.empty:
                st.subheader("Open tickets by person")
                st.bar_chart(open_df.groupby("assignee").size())
            if not overdue.empty:
                st.subheader("⚠️ Overdue")
                table_view(overdue)

            st.subheader("Hours logged by person")
            st.dataframe(db.hours_by_person(), use_container_width=True, hide_index=True)
            st.download_button("Download all tickets (CSV)", df.to_csv(index=False),
                               "tickets.csv", "text/csv")

        st.divider()
        st.subheader("Team accounts")
        st.dataframe(db.list_users(), use_container_width=True, hide_index=True)

        a, b = st.columns(2)
        with a:
            with st.form("add_user", clear_on_submit=True):
                st.markdown("**Add a team member**")
                n_user = st.text_input("Username (e.g. First.Last)")
                n_name = st.text_input("Display name (e.g. First)")
                n_role = st.selectbox("Role", ["member", "admin"])
                n_pw = st.text_input("Temporary password", type="password")
                if st.form_submit_button("Create account"):
                    err = db.create_user(n_user.strip(), n_name.strip(), n_pw, n_role) \
                        if n_user.strip() and n_name.strip() else "Fill in username and display name."
                    if err:
                        st.error(err)
                    else:
                        st.session_state["flash"] = "Account created. They'll set their own password on first login."
                        st.rerun()
        with b:
            with st.form("reset_pw", clear_on_submit=True):
                st.markdown("**Reset someone's password**")
                r_user = st.selectbox("Username", db.list_users()["username"].tolist())
                r_pw = st.text_input("New temporary password", type="password")
                if st.form_submit_button("Reset password"):
                    err = db.reset_password(r_user, r_pw)
                    if err:
                        st.error(err)
                    else:
                        st.toast("Password reset. They must change it on next login.", icon="✅")

        if not df.empty:
            st.divider()
            st.subheader("Delete a ticket")
            del_id = pick_ticket(df, "del_pick")
            if del_id and st.button("Delete permanently"):
                db.delete_ticket(del_id)
                st.rerun()
