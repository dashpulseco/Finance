"""Price history download and per-ticker CSV cache.

Cache layout: data/<TICKER>.csv with columns ``date,close`` (ISO date, float).
Incremental runs re-fetch from the last cached date (inclusive) so a provisional
last row is overwritten by the final close.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from finan.settings import DATA_DIR

logging.getLogger("yfinance").setLevel(logging.ERROR)
log = logging.getLogger(__name__)

# Xetra closes 17:30 CET; a row dated today before this hour is an intraday quote.
INTRADAY_CUTOFF_HOUR = 18


def _empty_series() -> pd.Series:
    return pd.Series(dtype="float64", index=pd.DatetimeIndex([]), name="close")


def load_cache(path: Path) -> pd.Series:
    """Return the cached closes as a Series (naive datetime index, sorted).

    Returns an empty Series when the file is missing or unreadable.
    """
    if not path.exists():
        return _empty_series()
    try:
        df = pd.read_csv(path)
        if list(df.columns) != ["date", "close"]:
            raise ValueError(f"unexpected columns {list(df.columns)}")
        idx = pd.to_datetime(df["date"], format="%Y-%m-%d")
        series = pd.Series(df["close"].astype("float64").to_numpy(), index=idx, name="close")
        series = series.dropna()
        series = series[~series.index.duplicated(keep="last")].sort_index()
        return series
    except Exception as exc:  # corrupt cache -> treat as missing
        log.warning("Cache %s unreadable (%s); ignoring it", path, exc)
        return _empty_series()


def save_cache(path: Path, series: pd.Series) -> None:
    """Write the series atomically (write .tmp then os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        {"date": series.index.strftime("%Y-%m-%d"), "close": series.to_numpy()}
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False, header=["date", "close"], lineterminator="\n")
    os.replace(tmp, path)


def fetch_history(ticker: str, start=None) -> Optional[pd.Series]:
    """Download closes from Yahoo Finance. Returns None on any failure."""
    try:
        t = yf.Ticker(ticker)
        if start is None:
            df = t.history(period="max", auto_adjust=False)
        else:
            start_str = pd.Timestamp(start).strftime("%Y-%m-%d")
            df = t.history(start=start_str, auto_adjust=False)
        if df is None or df.empty or "Close" not in df.columns:
            log.warning("Download for %s returned no rows", ticker)
            return None
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        df.index = idx.normalize()
        closes = df["Close"].dropna().astype("float64")
        closes = closes[~closes.index.duplicated(keep="last")].sort_index()
        closes.name = "close"
        return closes
    except Exception as exc:
        log.warning("Download for %s failed: %s", ticker, exc)
        return None


def _apply_intraday_guard(series: pd.Series, now: Optional[datetime] = None) -> pd.Series:
    """Drop the last row if it is dated today and local time is before 18:00."""
    if series.empty:
        return series
    now = now or datetime.now()
    last_day = series.index[-1].date()
    if last_day == now.date() and now.hour < INTRADAY_CUTOFF_HOUR:
        return series.iloc[:-1]
    return series


def update_history(ticker: str, force_refresh: bool = False):
    """Return (closes, source, note) for *ticker*.

    source is "live" when fresh data was merged and cached, "cache" when the
    download failed but a cache exists, "none" when there is no data at all.
    """
    cache_path = DATA_DIR / f"{ticker}.csv"
    cached = load_cache(cache_path)

    if force_refresh or cached.empty:
        fresh = fetch_history(ticker, start=None)
    else:
        fresh = fetch_history(ticker, start=cached.index[-1])

    if fresh is None or fresh.empty:
        if cached.empty:
            return _empty_series(), "none", "download failed and no cache"
        return cached, "cache", f"download failed; using cached data to {cached.index[-1].date()}"

    merged = pd.concat([cached, fresh])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    merged = _apply_intraday_guard(merged)
    merged.name = "close"

    save_cache(cache_path, merged)
    return merged, "live", ""
