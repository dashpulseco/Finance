"""The shared collection pipeline: one run, two entry points.

``tracker.py`` (local Windows: toast + report.html) and ``jobs/daily_job.py`` (cloud: site/data.json
+ push) both go through :func:`collect`, so the numbers, the headline and the one machine-greppable
``RUN`` log line are produced in exactly one place.

Module-scope imports are stdlib plus pandas plus the pure ``finan`` layers only. In particular
``finan.report`` (matplotlib) and ``finan.notify`` (winotify) are never imported here, at module
scope or otherwise: the cloud job runs on Linux, where neither is installed.
"""

import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from finan.market_data import update_history
from finan.metrics import Metrics, compute_metrics
from finan.rules import Signal, build_headline, evaluate, overall_level
from finan.settings import DATA_DIR, LOGS_DIR, resolve_thresholds

log = logging.getLogger("finan")


@dataclass
class IndexResult:
    """Everything one configured index produced in one run."""
    index: dict                 # the config entry: name, ticker, fund_held, ticker_note
    series: pd.Series           # cached+fresh closes (possibly empty)
    metrics: Optional[Metrics]  # None when series is empty
    signal: Signal
    source: str                 # "live" | "cache" | "none"
    note: str
    thresholds: dict            # the EFFECTIVE four-key dict this index was evaluated against
                                # (settings.resolve_thresholds of the global block and any
                                # per-index override); nothing downstream re-resolves it


@dataclass
class RunResult:
    """One whole run. Both entry points render from this and nothing else."""
    config: dict
    today: date
    generated_at: datetime      # datetime.now(), naive, as report.py already expects
    results: list
    headline: str
    contribution_day: bool      # today.day == contribution_reminder_day (exact, legacy semantics)
    contribution_due: bool      # effective reminder flag, weekday-shifted

    # Parallel-list views, so existing call sites (render_report, build_toast_body) keep working
    # unchanged against positional lists.
    @property
    def indices(self) -> list:
        return [r.index for r in self.results]

    @property
    def series_list(self) -> list:
        return [r.series for r in self.results]

    @property
    def metrics_list(self) -> list:
        return [r.metrics for r in self.results]

    @property
    def thresholds_list(self) -> list:
        return [r.thresholds for r in self.results]

    @property
    def signals(self) -> list:
        return [r.signal for r in self.results]

    @property
    def sources(self) -> list:
        return [r.source for r in self.results]

    @property
    def notes(self) -> list:
        return [r.note for r in self.results]

    @property
    def overall_level(self) -> str:
        return overall_level(self.signals, self.sources)

    @property
    def data_as_of(self) -> Optional[date]:
        # The same expression report.render_report computes for its as_of local, so the report's
        # column header and the site's "data as of" line cannot drift apart.
        dates = [m.last_date for m in self.metrics_list if m is not None]
        return max(dates) if dates else None

    @property
    def all_missing(self) -> bool:
        return bool(self.results) and all(r.source == "none" for r in self.results)


def ensure_dirs() -> None:
    """Create the generated-output directories. Must run before setup_logging()."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(log_file: str = "tracker.log") -> None:
    """Configure the root logger. basicConfig is a no-op once handlers exist, which is what
    makes it safe for a second entry point to call."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOGS_DIR / log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def contribution_due(today: date, day: int, weekday_only: bool = True) -> bool:
    """Is today the effective monthly contribution reminder date?

    Clamp the configured day to the month's length, then shift a weekend target forward - unless
    forward would leave the month, in which case fall back to the month's last weekday. That keeps
    the invariant simple and true: exactly one reminder date per calendar month, always inside it.
    """
    last = calendar.monthrange(today.year, today.month)[1]
    target = date(today.year, today.month, min(day, last))     # 31 -> 28/29/30
    if weekday_only and target.weekday() >= 5:                 # Sat/Sun
        while target.weekday() >= 5:
            target += timedelta(days=1)                        # forward to the next weekday...
            if target.month != today.month:                    # ...unless that leaves the month
                target = date(today.year, today.month, last)   # then take the month's last weekday
                while target.weekday() >= 5:
                    target -= timedelta(days=1)
                break
    return target == today


def collect(config: dict, force_refresh: bool = False,
            today: Optional[date] = None) -> RunResult:
    """Refresh every configured index and evaluate the rules. No I/O beyond the price cache."""
    today = today or date.today()
    results = []
    for idx in config["indices"]:
        closes, source, note = update_history(idx["ticker"], force_refresh=force_refresh)
        if note:
            log.warning("%s (%s): %s", idx["name"], idx["ticker"], note)
        metrics = compute_metrics(closes) if not closes.empty else None
        # Resolved ONCE per index here - this is the single call site - and the same object is
        # both evaluated against and carried on the result, so no consumer can disagree with the
        # thresholds the verdict was actually produced from.
        effective = resolve_thresholds(config["thresholds"], idx)
        results.append(IndexResult(index=idx, series=closes, metrics=metrics,
                                   signal=evaluate(metrics, effective),
                                   source=source, note=note, thresholds=effective))
    return RunResult(
        config=config, today=today, generated_at=datetime.now(), results=results,
        headline=build_headline([r.signal for r in results], [r.source for r in results],
                                config["app_name"]),
        contribution_day=(today.day == config["contribution_reminder_day"]),
        contribution_due=contribution_due(today, config["contribution_reminder_day"]),
    )


def log_run_line(run: RunResult, **extra) -> None:
    """Write the one machine-greppable line per run.

    The headline and the keyword extras share the final `|`-separated segment, separated by single
    spaces: log_run_line(run, toast="winotify") reproduces the pre-refactor string exactly.
    """
    parts = [f"RUN source={','.join(run.sources)}"]
    for idx, m, sig in zip(run.indices, run.metrics_list, run.signals):
        if m is None:
            parts.append(f'{idx["name"]} close=n/a belowATH=n/a verdict="{sig.verdict}"')
        else:
            parts.append(
                f'{idx["name"]} close={m.last_close:.2f} ({m.last_date.isoformat()}) '
                f'belowATH={m.below_ath_pct:.1f}% verdict="{sig.verdict}"'
            )
    tail = f'headline="{run.headline}"'
    for key, value in extra.items():
        tail += f" {key}={value}"
    parts.append(tail)
    log.info(" | ".join(parts))
