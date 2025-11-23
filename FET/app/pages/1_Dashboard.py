import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta

from app.utils.db import (
    load_budget,
    category_breakdown,
    load_expenses,
    load_goals,
    # sync helper (exists in cleaned db.py)
    sync_budget_from_family,
)
from app.utils.session_ui import show_logout_button

show_logout_button()  # put this near top of page (after imports)

# --- ML helpers (ensure ml_models.py is placed where app can import it) ---
try:
    from app.ml_models import (
        run_pipeline_and_predict,
        prepare_daily_series,
        train_expense_model_daily,
        predict_next_n_days_total,
    )
except Exception:
    try:
        # fallback if ml_models.py is at project root
        from ml_models import (
            run_pipeline_and_predict,
            prepare_daily_series,
            train_expense_model_daily,
            predict_next_n_days_total,
        )
    except Exception:
        run_pipeline_and_predict = None
        prepare_daily_series = None
        train_expense_model_daily = None
        predict_next_n_days_total = None

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")

# -------------------------------------------------
# USER SESSION
# -------------------------------------------------
username = st.session_state.get("username", "")
if not username:
    st.warning("Please login from the sidebar.")
    st.stop()

# -------------------------------------------------
# PAGE TITLE
# -------------------------------------------------
st.markdown("<h1>📊 Dashboard</h1>", unsafe_allow_html=True)

# -------------------------------------------------
# LOAD BUDGET & EXPENSE DATA
# -------------------------------------------------
now = datetime.now()
y = now.year
m = now.month

# load data from DB
budget = load_budget(username) or {}
cat_spend = category_breakdown(username, y, m) or {}
expenses = load_expenses(username) or []
goals = load_goals(username) or []

# If DB budget missing, try to compute from family incomes (auto-sync)
try:
    if not budget or budget.get("main_budget") in (None, 0):
        _ = sync_budget_from_family(username)
        budget = load_budget(username) or {}
except Exception:
    # if sync fails, proceed with whatever budget we have
    pass

# -------------------------------------------------
# SAFETY HELPERS
# -------------------------------------------------
def safe_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


import json
try:
    category_limits = json.loads(budget.get("category_limits_json") or "{}")
    if not isinstance(category_limits, dict):
        category_limits = {}
except Exception:
    category_limits = {}

main_budget = safe_float(budget.get("main_budget", 0))
monthly_spent = sum([safe_float(v, 0.0) for v in cat_spend.values()]) if cat_spend else 0.0
monthly_saved = main_budget - monthly_spent if main_budget > 0 else 0.0

# -------------------------------------------------
# BUDGET ALERTS
# -------------------------------------------------
def render_alerts():
    alerts = []

    for cat, limit in category_limits.items():
        limit_val = safe_float(limit, 0.0)
        spent_val = safe_float(cat_spend.get(cat, 0), 0.0)

        if limit_val <= 0:
            continue

        pct = (spent_val / limit_val) * 100 if limit_val else 0

        if pct >= 100:
            alerts.append(f"🔴 **{cat}** exceeded limit ({pct:.1f}%).")
        elif pct >= 80:
            alerts.append(f"⚠️ **{cat}** nearing limit ({pct:.1f}%).")

    if alerts:
        st.markdown("### ⚠️ Budget Alerts")
        for a in alerts:
            st.error(a)
    else:
        st.markdown("### 🟢 Budget Alerts")
        st.success("No category alerts! 🎉 You're within limits.")


render_alerts()

# -------------------------------------------------
# SUMMARY CARDS
# -------------------------------------------------
col1, col2, col3 = st.columns(3)

col1.metric("Monthly Budget", f"₹ {main_budget:,.2f}")
col2.metric("Monthly Spent", f"₹ {monthly_spent:,.2f}")
col3.metric("Monthly Saved", f"₹ {monthly_saved:,.2f}")

# -------------------------------------------------
# Add ML Predictions panel (appended, non-invasive)
# -------------------------------------------------
st.markdown("---")
st.subheader("🔮 Predictions & Suggestions")

# Build transactions DataFrame from `expenses` (defensive)
def build_transactions_df(expenses_list):
    """
    Expected: each expense item is a dict with at least 'date', 'amount', 'category'
    Falls back gracefully if keys differ.
    """
    if not expenses_list:
        return pd.DataFrame(columns=["date", "amount", "category"])

    if isinstance(expenses_list, pd.DataFrame):
        df = expenses_list.copy()
    else:
        try:
            df = pd.DataFrame(expenses_list)
        except Exception:
            try:
                df = pd.DataFrame([{"date": e[0], "amount": e[1], "category": e[2] if len(e) > 2 else None} for e in expenses_list])
            except Exception:
                df = pd.DataFrame(columns=["date", "amount", "category"])

    # normalize column names
    if "amount" not in df.columns:
        for alt in ["amt", "value", "cost"]:
            if alt in df.columns:
                df = df.rename(columns={alt: "amount"})
                break

    if "date" not in df.columns:
        for alt in ["txn_date", "transaction_date", "created_at"]:
            if alt in df.columns:
                df = df.rename(columns={alt: "date"})
                break

    if "category" not in df.columns:
        df["category"] = df.get("category", "Uncategorized")

    # types
    df["amount"] = df["amount"].apply(lambda v: safe_float(v, 0.0))
    try:
        df["date"] = pd.to_datetime(df["date"])
    except Exception:
        df["date"] = pd.to_datetime(now)

    df = df[["date", "amount", "category"]]
    return df


trans_df = build_transactions_df(expenses)

# monthly aggregates for savings model
def build_monthly_df_from_transactions(trans_df, monthly_income_proxy):
    if trans_df.empty:
        return pd.DataFrame(columns=["month", "income", "expense"])

    df = trans_df.copy()
    df["month"] = df["date"].dt.to_period("M").astype(str)
    monthly = df.groupby("month", as_index=False)["amount"].sum().rename(columns={"amount": "expense"})
    monthly["income"] = monthly_income_proxy
    monthly = monthly[["month", "income", "expense"]]
    return monthly


monthly_df = build_monthly_df_from_transactions(trans_df, main_budget)

st.caption("Note: predictions use your past transactions. If the app has few records, predictions will be simple proxies.")

col_a, col_b = st.columns([1, 3])
with col_a:
    run_preds = st.button("Run predictions")
with col_b:
    st.write("")

if run_preds:
    with st.spinner("Training lightweight models and predicting next 30 days..."):
        try:
            if run_pipeline_and_predict is not None:
                results = run_pipeline_and_predict(trans_df, monthly_df, days_ahead=30)
            else:
                raise ImportError("ml_models not available")
        except Exception:
            try:
                if train_expense_model_daily is not None and predict_next_n_days_total is not None:
                    expense_model, df_daily = train_expense_model_daily(trans_df)
                    total_future_exp, daily_preds = predict_next_n_days_total(expense_model, df_daily, n_days=30)
                else:
                    total_future_exp, daily_preds = 0.0, []

                results = {
                    "predicted_next_days_total_expense": float(total_future_exp),
                    "predicted_next_month_savings": float(main_budget - total_future_exp) if main_budget else None,
                    "category_summary": trans_df.groupby("category")["amount"].sum().reset_index().rename(columns={"amount": "amount"}),
                    "top_categories": trans_df.groupby("category")["amount"].sum().reset_index().rename(columns={"amount": "amount"}).sort_values("amount", ascending=False).head(5),
                    "daily_predictions_array": daily_preds if 'daily_preds' in locals() else [],
                }
            except Exception as e:
                st.error("Prediction failed: " + str(e))
                results = None

    if results:
        pred_exp = results.get("predicted_next_days_total_expense")
        pred_save = results.get("predicted_next_month_savings")
        top_cats = results.get("top_categories")
        daily_preds = results.get("daily_predictions_array")

        pcol1, pcol2, pcol3 = st.columns(3)
        pcol1.metric("Predicted next 30 days expense", f"₹ {pred_exp:,.2f}" if pred_exp is not None else "—")
        pcol2.metric("Predicted next month savings", f"₹ {pred_save:,.2f}" if pred_save is not None else "—")
        pcol3.metric("Data points used", f"{len(trans_df):,d}")

        if isinstance(top_cats, (pd.DataFrame, list)):
            st.subheader("Top spending categories (suggestions)")
            if isinstance(top_cats, list):
                top_df = pd.DataFrame(top_cats)
            else:
                top_df = top_cats.copy()
            if "percentage" not in top_df.columns and "amount" in top_df.columns:
                total_amt = top_df["amount"].sum() if not top_df.empty else 1
                top_df["percentage"] = (top_df["amount"] / (total_amt + 1e-9)) * 100
            st.dataframe(top_df.reset_index(drop=True), use_container_width=True)
        else:
            st.info("No category summary available.")

        try:
            if getattr(daily_preds, "__len__", None) and len(daily_preds) > 0:
                last_date = trans_df["date"].max() if not trans_df.empty else now
                future_dates = [last_date + timedelta(days=i+1) for i in range(len(daily_preds))]
                df_future = pd.DataFrame({"date": future_dates, "predicted_amount": list(daily_preds)})
                df_future["date_str"] = df_future["date"].dt.strftime("%Y-%m-%d")
                fig_line = px.line(df_future, x="date_str", y="predicted_amount", title="Predicted daily spend (next 30 days)", labels={"predicted_amount": "₹"})
                st.plotly_chart(fig_line, use_container_width=True)
        except Exception:
            pass

# -------------------------------------------------
# CHARTS SECTION (unchanged)
# -------------------------------------------------
st.markdown("---")
st.subheader("📈 This Month")

# BAR CHART – category spending
if cat_spend:
    df_chart = pd.DataFrame({
        "Category": list(cat_spend.keys()),
        "Amount": [safe_float(v, 0.0) for v in cat_spend.values()]
    })

    fig_bar = px.bar(
        df_chart,
        x="Category",
        y="Amount",
        title="Category-wise spending",
        labels={"Amount": "₹"},
    )
    st.plotly_chart(fig_bar, use_container_width=True)
else:
    st.info("No expenses recorded this month.")

# PIE CHART – spend % breakdown
if cat_spend:
    fig_pie = px.pie(
        df_chart,
        names="Category",
        values="Amount",
        title="Spending Distribution",
    )
    st.plotly_chart(fig_pie, use_container_width=True)

# -------------------------------------------------
# RECENT TRANSACTIONS
# -------------------------------------------------
st.markdown("---")
st.subheader("📄 Recent Transactions")

if expenses:
    df = pd.DataFrame(expenses)
    st.dataframe(df, use_container_width=True)
else:
    st.info("No transactions yet.")

# -------------------------------------------------
# ACTIVE GOALS (if exists)
# -------------------------------------------------
st.markdown("---")
st.subheader("🎯 Active Goal")

if goals:
    g = goals[-1]
    goal_name = g.get("goal_name") if isinstance(g, dict) else str(g)
    target_amount = safe_float(g.get("target_amount", 0)) if isinstance(g, dict) else 0.0
    st.markdown(f"**{goal_name}** → ₹ **{target_amount:,.0f}**")
else:
    st.info("No goals yet.")
