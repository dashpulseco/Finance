"""Signal rules, headline and toast body text."""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from finan.metrics import Metrics

STRONG = "STRONG INVEST"
INVEST = "INVEST"


@dataclass
class Signal:
    tier: Optional[str]
    verdict: str
    supporting: list = field(default_factory=list)
    below_ath_pct: Optional[float] = None


def evaluate(metrics: Optional[Metrics], thresholds: dict) -> Signal:
    """Turn Metrics into a Signal using the configured thresholds.

    *thresholds* is the per-index EFFECTIVE dict produced by settings.resolve_thresholds - the
    global block merged with that index's optional override - never config["thresholds"] directly.
    pipeline.collect() is the only caller that matters and it resolves before calling.
    """
    if metrics is None:
        return Signal(tier=None, verdict="No data", supporting=[], below_ath_pct=None)

    invest = float(thresholds["ath_drawdown_invest_pct"])
    strong = float(thresholds["ath_drawdown_strong_invest_pct"])
    high_52w_dd = float(thresholds["high_52w_drawdown_pct"])
    flag_sma = bool(thresholds.get("flag_below_sma200", True))

    below = metrics.below_ath_pct
    if below >= strong:
        tier = STRONG
    elif below >= invest:
        tier = INVEST
    else:
        tier = None
    verdict = f"{tier} signal: {below:.1f}% below ATH" if tier else "No signal"

    supporting = []
    if metrics.below_52w_pct >= high_52w_dd:
        supporting.append(f"{metrics.below_52w_pct:.1f}% below 52-week high")
    if flag_sma and metrics.vs_sma200_pct is not None and metrics.vs_sma200_pct < 0:
        supporting.append(f"{abs(metrics.vs_sma200_pct):.1f}% below 200-day MA")

    return Signal(tier=tier, verdict=verdict, supporting=supporting, below_ath_pct=below)


def short_name(name: str) -> str:
    """Short label for toast lines: strip a leading 'MSCI '."""
    name = name.strip()
    if name.upper().startswith("MSCI "):
        name = name[5:].strip()
    return name


def overall_level(signals: list, sources: list) -> str:
    """Overall state name: "nodata" | "strong" | "invest" | "none".

    The branch order is build_headline's, so the CSS state the site uses and the sentence the
    headline states can never disagree. The `signals and` guard is not decoration: with an empty
    index list `all(...)` over an empty `sources` is True, and build_headline deliberately falls
    through to "no signal today" in that case.
    """
    if signals and all(src == "none" for src in sources):
        return "nodata"
    if any(sig.tier == STRONG for sig in signals):
        return "strong"
    if any(sig.tier == INVEST for sig in signals):
        return "invest"
    return "none"


def build_headline(signals: list, sources: list, app_name: str = "Finan") -> str:
    """One-line overall headline (toast title / report h1)."""
    level = overall_level(signals, sources)
    if level == "nodata":
        return f"{app_name}: no data today"          # no suffixes in this branch

    n = len(signals)
    n_signals = sum(1 for sig in signals if sig.tier is not None)
    if level == "strong":
        headline = f"{app_name}: STRONG INVEST signal on {n_signals} of {n} indices"
    elif level == "invest":
        headline = f"{app_name}: INVEST signal on {n_signals} of {n} indices"
    else:
        headline = f"{app_name}: no signal today"
    if any(src == "cache" for src in sources):
        headline += " (cached data)"
    if any(src == "none" for src in sources):
        headline += " (partial data)"
    return headline


def build_toast_body(indices: list, metrics: list, signals: list, sources: list,
                     contribution_day: int, today: Optional[date] = None) -> list:
    """Short ASCII lines, one per index, plus the contribution reminder."""
    today = today or date.today()
    lines = []
    for idx, m, sig, src in zip(indices, metrics, signals, sources):
        label = short_name(idx["name"])
        if m is None:
            lines.append(f"{label}: no data")
            continue
        parts = []
        if src == "cache":
            parts.append(f"cached {m.last_date.isoformat()}")
        parts.append(f"-{m.below_ath_pct:.1f}% ATH")
        parts.append(sig.tier if sig.tier else "no signal")
        lines.append(f"{label}: " + ", ".join(parts))
    if today.day == contribution_day:
        lines.append("Monthly contribution day")
    return lines
