"""The ``site/data.json`` contract: one module owns the schema.

Everything this module writes is published to a PUBLIC GitHub Pages site backed by a PUBLIC
repository, so "only public market data" is enforced here rather than reviewed: the five frozen
allow-list sets below are the whole published surface, :func:`build_site_data` writes every key
literally (no ``dict(config)``, no ``**config``, no nested passthrough) and
:func:`assert_public` walks the finished payload before :func:`write_site_data` touches the disk.
Six scopes are checked, because ``ALLOWED_THRESHOLDS`` governs two of them: the top-level
``thresholds`` block and the per-index one each index now carries.

:func:`assert_public` is a ceiling and not an exact shape - EXCEPT for the two threshold blocks,
which are checked in both directions (missing keys as well as unexpected ones). The PWA scales a
card's sparkline by ``-thresholds.strong_invest_pct``, so a dropped or renamed key there is a
rendering bug on the phone rather than a merely incomplete payload.

:func:`build_site_data` takes no ``stale``/``stale_reason`` keywords. It always builds a *fresh*
payload with ``stale=False`` and ``stale_reason=""``; the stale case is not a fresh payload at all
but a republication of the committed one, which ``jobs/daily_job.py``'s ``_stale_fallback``
produces by loading that file and overwriting four top-level fields plus ``schedule.next_check``.

Module-scope imports are stdlib plus pandas plus the pure ``finan`` layers only - never
``finan.report`` (matplotlib) or ``finan.notify`` (winotify), which do not exist on the Linux
runner, and not ``calendar`` either: the only calendar arithmetic in this project is
``pipeline.contribution_due``'s ``monthrange``.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from finan.pipeline import IndexResult, RunResult
from finan.rules import INVEST, STRONG

log = logging.getLogger("finan")

SCHEMA_VERSION = 2       # 1 -> 2: every index now carries its own effective "thresholds" block

# The published surface, written out literally so assert_public and load_site_data share exactly
# one source of truth. A field cannot reach the phone unless its name is in one of these sets.
ALLOWED_TOP_LEVEL = frozenset({
    "schema_version", "generated_at", "data_as_of", "app_name", "app_url", "headline",
    "overall_level", "contribution_due", "contribution_day",
    "data_status", "stale", "stale_reason", "thresholds", "schedule", "indices",
})                                                                    # 15 keys
ALLOWED_INDEX = frozenset({
    "name", "fund_held", "ticker", "currency", "last_close", "last_date", "ath", "ath_date",
    "below_ath_pct", "high_52w", "below_52w_pct", "sma200", "vs_sma200_pct", "change_1m_pct",
    "ytd_pct", "ytd_base_date", "history_start", "verdict", "tier", "level", "supporting",
    "source", "note", "drawdown_history", "thresholds",
})                                                                    # 25 keys
ALLOWED_HISTORY = frozenset({"date", "drawdown_pct"})
# Shared by both scopes - the top-level block and every index's own - so the PWA reads one shape.
ALLOWED_THRESHOLDS = frozenset({"invest_pct", "strong_invest_pct", "high_52w_pct",
                                "flag_below_sma200"})
ALLOWED_SCHEDULE = frozenset({"cron_utc", "note", "next_check"})

# "<minute> <hour> * * <weekdays>" and nothing else: a step (*/2), a day-of-month restriction or a
# named weekday (MON) is refused rather than mis-read. See next_run_utc.
_CRON_RE = re.compile(r"^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+([0-7](?:[,-][0-7])*)$")


def _num(value, places):
    """Round to *places*, matching report.py's _pct (1 dp) and _eur (2 dp) exactly."""
    if value is None:
        return None
    out = round(float(value), places)
    return 0.0 if out == 0 else out     # collapses -0.0, which json.dump would emit verbatim


def _dd(value) -> float:
    return min(0.0, round(float(value), 2)) or 0.0   # clamp to <= 0, collapse -0.0


def _iso(value) -> Optional[str]:
    return None if value is None else value.isoformat()


def index_level(result: IndexResult) -> str:
    """CSS/badge state for one index: "nodata" | "strong" | "invest" | "none".

    Derived structurally from the absence of metrics and from signal.tier, never by comparing
    verdict text (report._verdict_class keeps its string comparison; it renders a different view).
    """
    if result.metrics is None:
        return "nodata"
    if result.signal.tier == STRONG:
        return "strong"
    if result.signal.tier == INVEST:
        return "invest"
    return "none"


def drawdown_history(series: pd.Series, years: int = 3) -> list:
    """Weekly-close drawdown from the running ATH, as [{date, drawdown_pct}] in ascending order."""
    if series.empty:
        return []
    dd = (series / series.cummax() - 1) * 100      # same formula as report.drawdown_chart_png_b64
    dd = dd[dd.index > series.index[-1] - pd.DateOffset(years=years)]
    if dd.empty:
        return []
    weekly = dd.resample("W-FRI").last().dropna()  # 157 points for 3 years (measured)
    points = [{"date": ts.date().isoformat(), "drawdown_pct": _dd(v)}
              for ts, v in weekly.items()]

    # The last point must be the real last close, not a bucket label.
    last_iso = series.index[-1].date().isoformat()
    last_point = {"date": last_iso, "drawdown_pct": _dd(dd.iloc[-1])}
    if points and points[-1]["date"] >= last_iso:
        points[-1] = last_point                    # relabel the final bucket (always taken)
    else:
        # Unreachable with W-FRI labelling: the final label is the Friday on or after the last
        # trading day, so it is never < last_iso. Kept so that changing the resample anchor
        # cannot silently drop the tip.
        points.append(last_point)
    return points


def _cron_days_to_python(field: str) -> set:
    """Cron weekdays (0=Sunday..6=Saturday, 7 also Sunday) -> Python weekdays (0=Monday)."""
    cron_days = set()
    for token in field.split(","):
        if "-" in token:
            lo, hi = (int(x) for x in token.split("-"))
            cron_days.update(range(lo, hi + 1))
        else:
            cron_days.add(int(token))
    return {6 if d in (0, 7) else d - 1 for d in cron_days}   # cron 1..6 -> python 0..5


def _no_next_check(cron_utc: str, reason: str) -> None:
    log.info("site.schedule_cron_utc %r: %s; next_check will be null", cron_utc, reason)
    return None


def next_run_utc(cron_utc: str, now_utc: datetime) -> Optional[datetime]:
    """The next UTC instant the restricted cron expression fires, or None.

    Pure: *now_utc* is an argument and no clock is read, so it is testable without freezing time.
    None means "cannot say" - the footer then shows only the free-text schedule note.
    """
    match = _CRON_RE.match(cron_utc)
    if not match:
        return _no_next_check(cron_utc, "unsupported expression")
    minute, hour = int(match.group(1)), int(match.group(2))
    if not 0 <= minute <= 59 or not 0 <= hour <= 23:
        return _no_next_check(cron_utc, "minute or hour out of range")
    weekdays = _cron_days_to_python(match.group(3))
    if not weekdays:
        # A descending range such as 5-1 expands to no days at all.
        return _no_next_check(cron_utc, "weekday field matches no day")

    start = now_utc.date()
    for offset in range(14):            # worst case: a once-a-week cron whose time has passed
        day = start + timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
        if candidate > now_utc:         # strictly after, so a run at its own cron time looks ahead
            return candidate
    return None


def _index_entry(result: IndexResult) -> dict:
    """The 25 published per-index keys, written out one by one.

    below_ath_pct and below_52w_pct are published as POSITIVE magnitudes, exactly as
    metrics.compute_metrics computes them and exactly as report.html prints them, so the two views
    compare as plain numeric equality; the PWA negates at render time. vs_sma200_pct, change_1m_pct
    and ytd_pct are published already signed.

    "thresholds" is result.thresholds - the EFFECTIVE block pipeline.collect() evaluated this index
    against - published on every index, always, even when it equals the global one. The payload
    then states the thresholds actually used for that card and no reader has to resolve anything
    (see plan D3): site/app.js performs no arithmetic of its own.
    """
    m = result.metrics
    t = result.thresholds
    return {
        "name": result.index["name"],
        "fund_held": result.index["fund_held"],
        "ticker": result.index["ticker"],
        # Hard-coded, not read from anywhere: every tracked ticker is an EUR listing. That is not
        # an assumption left to trust - scripts/fund_search.py's validate() is the enforcement
        # point, refusing any candidate whose currency is not EUR before it can reach config.json.
        "currency": "EUR",
        "last_close": _num(m.last_close, 2) if m is not None else None,
        "last_date": _iso(m.last_date) if m is not None else None,
        "ath": _num(m.ath, 2) if m is not None else None,
        "ath_date": _iso(m.ath_date) if m is not None else None,
        "below_ath_pct": _num(m.below_ath_pct, 1) if m is not None else None,
        "high_52w": _num(m.high_52w, 2) if m is not None else None,
        "below_52w_pct": _num(m.below_52w_pct, 1) if m is not None else None,
        "sma200": _num(m.sma200, 2) if m is not None else None,
        "vs_sma200_pct": _num(m.vs_sma200_pct, 1) if m is not None else None,
        "change_1m_pct": _num(m.change_1m_pct, 1) if m is not None else None,
        "ytd_pct": _num(m.ytd_pct, 1) if m is not None else None,
        "ytd_base_date": _iso(m.ytd_base_date) if m is not None else None,
        "history_start": _iso(m.history_start) if m is not None else None,
        "verdict": result.signal.verdict,
        "tier": result.signal.tier,
        "level": index_level(result),
        "supporting": list(result.signal.supporting),
        "source": result.source,
        "note": result.note,
        "drawdown_history": drawdown_history(result.series),
        "thresholds": {
            "invest_pct": t["ath_drawdown_invest_pct"],
            "strong_invest_pct": t["ath_drawdown_strong_invest_pct"],
            "high_52w_pct": t["high_52w_drawdown_pct"],
            "flag_below_sma200": t["flag_below_sma200"],
        },
    }


def build_site_data(run: RunResult) -> dict:
    """The only constructor of a fresh payload. Every key is typed out here on purpose."""
    config = run.config
    site = config["site"]
    thresholds = config["thresholds"]
    sources = run.sources
    next_check = next_run_utc(site["schedule_cron_utc"], datetime.now(timezone.utc))

    stale = False               # a fresh payload is never stale; _stale_fallback owns that state
    if stale:                                       # only _stale_fallback publishes "stale"
        data_status = "stale"
    elif any(s == "none" for s in sources):         # most-degraded first: partial beats cached
        data_status = "partial"
    elif any(s == "cache" for s in sources):
        data_status = "cached"
    else:
        data_status = "live"

    return {
        "schema_version": SCHEMA_VERSION,
        # RunResult.generated_at stays the naive datetime.now() report.py expects; .astimezone()
        # attaches the local offset so the published instant is unambiguous.
        "generated_at": run.generated_at.astimezone().isoformat(timespec="seconds"),
        "data_as_of": _iso(run.data_as_of),
        "app_name": config["app_name"],
        "app_url": site["app_url"],
        "headline": run.headline,
        "overall_level": run.overall_level,
        # RunResult.contribution_day (the exact calendar match) is deliberately not published.
        "contribution_due": run.contribution_due,
        "contribution_day": config["contribution_reminder_day"],
        "data_status": data_status,
        "stale": stale,
        "stale_reason": "",
        # Neither nested object is a passthrough: config["thresholds"] and config["site"] are
        # user-editable, so the renames below are the only keys that cross the boundary.
        "thresholds": {
            "invest_pct": thresholds["ath_drawdown_invest_pct"],
            "strong_invest_pct": thresholds["ath_drawdown_strong_invest_pct"],
            "high_52w_pct": thresholds["high_52w_drawdown_pct"],
            "flag_below_sma200": thresholds["flag_below_sma200"],
        },
        "schedule": {
            "cron_utc": site["schedule_cron_utc"],
            "note": site["schedule_note"],
            "next_check": (None if next_check is None
                           else next_check.isoformat(timespec="seconds")),
        },
        "indices": [_index_entry(r) for r in run.results],
    }


def _assert_keys(section, allowed: frozenset, where: str) -> None:
    if not isinstance(section, dict):
        raise ValueError(f"{where}: not an object")
    extra = section.keys() - allowed
    if extra:
        raise ValueError(f"{where}: key(s) {sorted(extra)} are not in the published allow-list")


def _assert_exact_keys(section, allowed: frozenset, where: str) -> None:
    """Like _assert_keys, but raises on a MISSING key too: an exact shape, not a ceiling."""
    if not isinstance(section, dict):
        raise ValueError(f"{where}: not an object")
    missing = allowed - section.keys()
    extra = section.keys() - allowed
    if missing or extra:
        raise ValueError(f"{where}: keys wrong (missing={sorted(missing)}, "
                         f"unexpected={sorted(extra)})")


def _assert_threshold_types(section: dict, where: str) -> None:
    """The three percentages must be non-bool numbers and the flag a bool.

    site/app.js scales a card's sparkline by -thresholds.strong_invest_pct, so a string here
    would not be a cosmetic problem: it would break the rendering on the phone.
    """
    for key in ("invest_pct", "strong_invest_pct", "high_52w_pct"):
        value = section[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{where}.{key}: must be a number")
    if not isinstance(section["flag_below_sma200"], bool):
        raise ValueError(f"{where}.flag_below_sma200: must be true or false")


def assert_public(payload: dict) -> None:
    """Raise ValueError naming the JSON path of any key outside the frozen allow-lists.

    A ceiling, not an exact shape: load_site_data checks the other direction. Adding a field to
    the schema means adding its name to a frozen set in the same commit, which is exactly the
    moment to ask whether it is public. The two threshold blocks are the exception - exact in both
    directions here as well, and type-checked, because the PWA computes its sparkline scale from
    one of their values.
    """
    _assert_keys(payload, ALLOWED_TOP_LEVEL, "payload")
    _assert_exact_keys(payload["thresholds"], ALLOWED_THRESHOLDS, "payload.thresholds")
    _assert_threshold_types(payload["thresholds"], "payload.thresholds")
    _assert_keys(payload["schedule"], ALLOWED_SCHEDULE, "payload.schedule")
    for i, index in enumerate(payload["indices"]):
        _assert_keys(index, ALLOWED_INDEX, f"payload.indices[{i}]")
        where = f"payload.indices[{i}].thresholds"
        if "thresholds" not in index:
            raise ValueError(f"{where}: missing")
        _assert_exact_keys(index["thresholds"], ALLOWED_THRESHOLDS, where)
        _assert_threshold_types(index["thresholds"], where)
        for j, point in enumerate(index["drawdown_history"]):
            _assert_keys(point, ALLOWED_HISTORY, f"payload.indices[{i}].drawdown_history[{j}]")


def write_site_data(path: Path, payload: dict) -> None:
    """Write the payload atomically as UTF-8 with LF line endings, allow-list checked first."""
    assert_public(payload)                                   # before anything touches the disk
    path.parent.mkdir(parents=True, exist_ok=True)           # as market_data.save_cache does
    tmp = path.with_suffix(path.suffix + ".tmp")             # APPENDED: site/data.json.tmp
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def load_site_data(path: Path) -> dict:
    """Load a published payload, checking its full shape. Raises ValueError naming *path*.

    Used by --notify-only and by the stale fallback, with the same checks on both paths: there is
    deliberately no lenient mode, because republishing a mis-shaped payload pushes it to the phone.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # JSONDecodeError is already a ValueError, but its message names only a line and column;
        # every failure from this function has to name the file it was reading.
        raise ValueError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: top level is not an object")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: schema_version is not {SCHEMA_VERSION}")
    missing = ALLOWED_TOP_LEVEL - payload.keys()
    extra = payload.keys() - ALLOWED_TOP_LEVEL
    if missing or extra:
        raise ValueError(f"{path}: top-level keys wrong "
                         f"(missing={sorted(missing)}, unexpected={sorted(extra)})")
    if not isinstance(payload["headline"], str):
        raise ValueError(f"{path}: headline must be a string")
    if not isinstance(payload["indices"], list):
        raise ValueError(f"{path}: indices must be a list")
    for key, allowed in (("thresholds", ALLOWED_THRESHOLDS), ("schedule", ALLOWED_SCHEDULE)):
        section = payload[key]
        if not isinstance(section, dict):
            raise ValueError(f"{path}: {key} must be an object")
        if section.keys() != allowed:
            raise ValueError(f"{path}: {key} keys wrong "
                             f"(missing={sorted(allowed - section.keys())}, "
                             f"unexpected={sorted(section.keys() - allowed)})")
    # Schema 2: every index states the thresholds its card was rendered against. Checked here as
    # well as in assert_public, so the republication path cannot push a block with a key renamed
    # or dropped to the phone.
    for i, index in enumerate(payload["indices"]):
        where = f"{path}: indices[{i}].thresholds"
        if not isinstance(index, dict) or "thresholds" not in index:
            raise ValueError(f"{where}: missing")
        _assert_exact_keys(index["thresholds"], ALLOWED_THRESHOLDS, where)
    return payload
