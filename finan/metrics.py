"""Per-index metrics computed from a close-price Series."""

from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd


@dataclass
class Metrics:
    last_close: float
    last_date: date
    ath: float
    ath_date: date
    below_ath_pct: float
    high_52w: float
    below_52w_pct: float
    sma200: Optional[float]
    vs_sma200_pct: Optional[float]
    change_1d_pct: Optional[float]
    change_1m_pct: Optional[float]
    ytd_pct: Optional[float]
    ytd_base_date: Optional[date]
    history_start: date


def compute_metrics(s: pd.Series) -> Metrics:
    """Compute Metrics from a sorted close Series with a naive DatetimeIndex."""
    if s.empty:
        raise ValueError("compute_metrics requires a non-empty series")

    last_close = float(s.iloc[-1])
    d = s.index[-1]
    last_date = d.date()

    ath = float(s.max())
    ath_date = s.idxmax().date()
    below_ath_pct = (ath - last_close) / ath * 100

    window_52w = s[s.index > d - pd.Timedelta(days=365)]
    high_52w = float(window_52w.max())
    below_52w_pct = (high_52w - last_close) / high_52w * 100

    if len(s) >= 200:
        sma200 = float(s.tail(200).mean())
        vs_sma200_pct = (last_close - sma200) / sma200 * 100
    else:
        sma200 = None
        vs_sma200_pct = None

    change_1d_pct = (last_close / float(s.iloc[-2]) - 1) * 100 if len(s) >= 2 else None

    base_1m = s.asof(d - pd.DateOffset(months=1))
    change_1m_pct = None if pd.isna(base_1m) else (last_close / float(base_1m) - 1) * 100

    before_year = s[s.index < pd.Timestamp(d.year, 1, 1)]
    if before_year.empty:
        ytd_pct = None
        ytd_base_date = None
    else:
        ytd_pct = (last_close / float(before_year.iloc[-1]) - 1) * 100
        ytd_base_date = before_year.index[-1].date()

    history_start = s.index[0].date()

    return Metrics(
        last_close=last_close,
        last_date=last_date,
        ath=ath,
        ath_date=ath_date,
        below_ath_pct=below_ath_pct,
        high_52w=high_52w,
        below_52w_pct=below_52w_pct,
        sma200=sma200,
        vs_sma200_pct=vs_sma200_pct,
        change_1d_pct=change_1d_pct,
        change_1m_pct=change_1m_pct,
        ytd_pct=ytd_pct,
        ytd_base_date=ytd_base_date,
        history_start=history_start,
    )
