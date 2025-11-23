# ml_models.py
"""
Simple ML helpers for Family Expense Tracker (FET)
- Expense time-series model (daily -> predict next-month total)
- Savings regression model (income, expense -> savings)
- Category analysis for spending suggestions

Requirements:
pip install pandas scikit-learn joblib
"""

from datetime import timedelta
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
import joblib
from pathlib import Path

MODEL_DIR = Path("models")
MODEL_DIR.mkdir(exist_ok=True)

# -------------------------
# Utility: save / load
# -------------------------
def save_model(model, name: str):
    path = MODEL_DIR / f"{name}.joblib"
    joblib.dump(model, path)
    return str(path)

def load_model(name: str):
    path = MODEL_DIR / f"{name}.joblib"
    if path.exists():
        return joblib.load(path)
    return None

# -------------------------
# 1) FUTURE EXPENSE (time-series, daily -> next-month total)
# -------------------------
def prepare_daily_series(trans_df):
    """
    trans_df: DataFrame with columns ['date','amount']
    Returns aggregated daily DataFrame with 'date' as datetime and 'amount' summed per day.
    """
    df = trans_df.copy()
    df['date'] = pd.to_datetime(df['date'])
    df = df.groupby('date', as_index=False)['amount'].sum().sort_values('date')
    # fill missing dates with 0 to make series continuous
    idx = pd.date_range(df['date'].min(), df['date'].max(), freq='D')
    df = df.set_index('date').reindex(idx, fill_value=0).rename_axis('date').reset_index()
    return df

def train_expense_model_daily(trans_df, model_name="expense_daily_rf", n_estimators=100):
    """
    Train a RandomForest on daily amounts (simple forecasting by using day index).
    Returns the trained model and the prepared dataframe.
    """
    df = prepare_daily_series(trans_df)
    df['day_num'] = (df['date'] - df['date'].min()).dt.days
    X = df[['day_num']].values
    y = df['amount'].values

    if len(X) < 10:
        # If too few days, RandomForest may overfit; fallback to linear regression
        model = LinearRegression()
    else:
        model = RandomForestRegressor(n_estimators=n_estimators, random_state=42)

    model.fit(X, y)
    save_model(model, model_name)
    return model, df

def predict_next_n_days_total(model, df_daily, n_days=30):
    """
    Predict total expense for the next n_days after the last day in df_daily.
    df_daily must be the output of prepare_daily_series (with 'day_num').
    Approach: predict each day individually and sum (simple).
    """
    last_day = df_daily['day_num'].max()
    future_day_nums = np.arange(last_day + 1, last_day + 1 + n_days).reshape(-1, 1)
    preds = model.predict(future_day_nums)
    # ensure no negative predictions
    preds = np.clip(preds, a_min=0, a_max=None)
    return float(preds.sum()), preds  # return total and daily array

# -------------------------
# 2) SAVINGS PREDICTION (income, expense -> savings)
# -------------------------
def train_savings_model(monthly_df, model_name="savings_lr"):
    """
    monthly_df: DataFrame with ['month' (YYYY-MM or number), 'income', 'expense']
    Trains LinearRegression to predict savings = income - expense (or learns relationship).
    """
    df = monthly_df.copy()
    df = df.dropna(subset=['income','expense'])
    X = df[['income','expense']].values
    y = (df['income'] - df['expense']).values  # target savings

    model = LinearRegression()
    model.fit(X, y)
    save_model(model, model_name)
    return model

def predict_savings(model, income, expense):
    pred = float(model.predict([[income, expense]])[0])
    return pred

# -------------------------
# 3) CATEGORY ANALYSIS / SUGGESTIONS
# -------------------------
def analyze_spending_categories(trans_df, top_n=5):
    """
    trans_df: DataFrame with ['category', 'amount']
    Returns category sums, percentages, sorted high to low
    """
    df = trans_df.copy()
    df = df.dropna(subset=['category','amount'])
    cat = df.groupby('category', as_index=False)['amount'].sum()
    total = cat['amount'].sum() if not cat.empty else 0.0
    cat['percentage'] = (cat['amount'] / (total + 1e-9)) * 100
    cat = cat.sort_values('percentage', ascending=False).reset_index(drop=True)
    top = cat.head(top_n)
    return cat, top

# -------------------------
# Quick evaluation helpers
# -------------------------
def evaluate_model(model, X, y_true):
    y_pred = model.predict(X)
    return {"mae": float(mean_absolute_error(y_true, y_pred))}

# -------------------------
# Example convenience function to run full pipeline (used by app)
# -------------------------
def run_pipeline_and_predict(trans_df, monthly_df, days_ahead=30):
    """
    High-level helper: trains models (if needed) and returns:
      - predicted next <days_ahead> total expense
      - predicted savings for next month (using predicted expense)
      - category analysis DataFrame (top categories)
    """
    # train expense model
    expense_model, df_daily = train_expense_model_daily(trans_df)
    total_future_exp, daily_preds = predict_next_n_days_total(expense_model, df_daily, n_days=days_ahead)

    # train savings model if monthly_df present
    savings_pred = None
    savings_model = None
    if monthly_df is not None and not monthly_df.empty:
        savings_model = train_savings_model(monthly_df)
        # For savings, we need expected income; take last known income as proxy
        last_income = float(monthly_df.sort_values('month').iloc[-1]['income'])
        savings_pred = predict_savings(savings_model, last_income, total_future_exp)

    # category analysis
    cat_all, cat_top = analyze_spending_categories(trans_df)

    return {
        "predicted_next_days_total_expense": float(total_future_exp),
        "predicted_next_month_savings": float(savings_pred) if savings_pred is not None else None,
        "category_summary": cat_all,
        "top_categories": cat_top,
        "daily_predictions_array": daily_preds
    }
