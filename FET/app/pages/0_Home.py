# pages/0_Home.py
import streamlit as st
from datetime import datetime

# Try app.utils first (clean project layout), fallback to utils for backward compatibility
try:
    from app.utils.formatting import rupee, format_date
except Exception:
    from utils.formatting import rupee, format_date

try:
    from app.utils.expenses import monthly_summary, load_expenses
except Exception:
    from utils.expenses import monthly_summary, load_expenses

# Use cleaned DB helpers (load_budget + sync_budget_from_family) from app.utils.db
try:
    from app.utils.db import load_budget, sync_budget_from_family
except Exception:
    # fall back to older module paths if necessary
    try:
        from utils.budget import load_budget  # older helper
    except Exception:
        def load_budget(u): return {"main_budget": None, "category_limits_json": "{}"}
    def sync_budget_from_family(u): return 0.0

# If there is a helper that explicitly computes family monthly income in older utils,
# use it as fallback to compute a budget if sync isn't available.
family_monthly_income = None
try:
    from app.utils.family import load_family as _lf
    def family_monthly_income(username: str) -> float:
        members = _lf(username) or []
        total = 0.0
        for m in members:
            try:
                total += float(m.get("monthly_income", 0) if isinstance(m, dict) else (m[3] if len(m) > 3 else 0))
            except Exception:
                pass
        return total
except Exception:
    try:
        from utils.family_utils import family_monthly_income  # older helper
    except Exception:
        # fallback stub
        def family_monthly_income(username: str) -> float:
            return 0.0


st.set_page_config(page_title="Home", page_icon="🏡", layout="wide")
st.title("🏡 Family Expense Tracker — Home")

# require login
if "username" not in st.session_state or not st.session_state.username:
    st.info("Welcome! Please login or register (use the sidebar pages).")
    st.markdown("If you're new, open the Register tab in the sidebar to create an account.")
    st.stop()

username = st.session_state.username

# quick stats
binfo = load_budget(username) or {"main_budget": None, "category_limits_json": "{}"}
main_budget = binfo.get("main_budget")

# If no DB budget, try to sync from family incomes (this writes to DB via sync)
if not main_budget:
    try:
        sync_budget_from_family(username)
        binfo = load_budget(username) or {"main_budget": None, "category_limits_json": "{}"}
        main_budget = binfo.get("main_budget")
    except Exception:
        # fallback: use family_monthly_income helper if available
        try:
            main_budget = family_monthly_income(username)
        except Exception:
            main_budget = 0.0

# final safety default
try:
    main_budget = float(main_budget or 0.0)
except Exception:
    main_budget = 0.0

y = datetime.now().year
m = datetime.now().month

# monthly_summary is expected to return (spent, saved) or similar
try:
    spent, saved = monthly_summary(username, y, m, main_budget)
except Exception:
    # fallback: compute spent via load_expenses if monthly_summary not available
    try:
        df = load_expenses(username)
        spent = 0.0
        if hasattr(df, "iterrows"):
            # pandas DataFrame
            df["date"] = df["date"].astype(str)
            df["date_only"] = df["date"].str.slice(0, 10)
            spent = 0.0
            for _, r in df.iterrows():
                try:
                    dt = r["date_only"]
                    if dt.startswith(f"{y}-{m:02d}"):
                        spent += float(r["amount"] or 0.0)
                except Exception:
                    pass
        else:
            # list of dicts
            for r in df:
                try:
                    d = str(r.get("date", ""))[:7]
                    if d == f"{y}-{m:02d}":
                        spent += float(r.get("amount", 0) or 0.0)
                except Exception:
                    pass
        saved = main_budget - spent
    except Exception:
        spent, saved = 0.0, main_budget

col1, col2, col3 = st.columns([1.8, 1, 1])
with col1:
    st.markdown(f"### 👋 Hello, **{username}**")
    st.markdown("Plan together — Protect together — Prosper together.")
    st.markdown("---")
    st.markdown("Quick Actions")
    c1, c2, c3 = st.columns(3)
    if c1.button("📊 Dashboard"):
        st.session_state["page"] = "Dashboard"
        st.rerun()
    if c2.button("➕ Add Expense"):
        st.session_state["page"] = "Add Expense"
        st.rerun()
    if c3.button("⚙️ Settings"):
        st.session_state["page"] = "Settings"
        st.rerun()

with col2:
    st.markdown("### Monthly Budget")
    st.metric("Budget", rupee(main_budget))
    st.metric("Spent", rupee(spent))

with col3:
    st.markdown("### This month")
    st.metric("Saved", rupee(saved))
    # most recent 3 transactions
    try:
        hist = load_expenses(username)
        if hasattr(hist, "empty") and not hist.empty:
            hist["date"] = hist["date"].astype(str)
            recent = hist.sort_values("date", ascending=False).head(3)
            for _, r in recent.iterrows():
                st.write(f"- {r['date']} • {r['category']} • {rupee(r['amount'])}")
        elif isinstance(hist, list) and len(hist) > 0:
            # list of dicts fallback
            recent = sorted(hist, key=lambda x: x.get("date", ""), reverse=True)[:3]
            for r in recent:
                st.write(f"- {str(r.get('date',''))} • {r.get('category','')} • {rupee(r.get('amount',0))}")
    except Exception:
        pass

st.markdown("---")
st.caption(f"Logged in as {username} • {format_date(datetime.now())}")
