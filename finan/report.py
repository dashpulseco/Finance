"""Self-contained HTML report (inline CSS, base64 PNG charts, no external assets)."""

import base64
import html
import io
from datetime import datetime
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set before importing pyplot)
import pandas as pd  # noqa: E402

_STYLE = """
body { font-family: Segoe UI, Arial, sans-serif; margin: 24px; color: #222; background: #fafafa; }
h1 { font-size: 1.5em; margin-bottom: 4px; }
h2 { font-size: 1.1em; margin-top: 28px; }
p.meta { color: #555; margin-top: 0; }
.banner { padding: 10px 14px; border-radius: 4px; margin: 10px 0; }
.banner.amber { background: #fff3cd; border: 1px solid #e0b84c; color: #6b4e00; }
.banner.green { background: #d4edda; border: 1px solid #7bc48a; color: #155724; }
table { border-collapse: collapse; font-size: 0.85em; background: #fff; }
th, td { border: 1px solid #ccc; padding: 6px 8px; text-align: right; vertical-align: top; }
th { background: #eee; text-align: center; }
td.text { text-align: left; }
td.no-signal { background: #e6e6e6; color: #444; }
td.invest { background: #c8f0c8; color: #14532d; font-weight: bold; }
td.strong { background: #2e7d32; color: #fff; font-weight: bold; }
td.nodata { background: #fff3cd; color: #6b4e00; }
.badge { display: inline-block; margin-top: 4px; padding: 1px 6px; border-radius: 3px; font-size: 0.85em;
         font-weight: normal; background: #fff3cd; border: 1px solid #e0b84c; color: #6b4e00; }
section img { max-width: 100%; border: 1px solid #ddd; background: #fff; }
footer { margin-top: 32px; font-size: 0.85em; color: #555; border-top: 1px solid #ccc; padding-top: 10px; }
"""


def drawdown_chart_png_b64(series: pd.Series, invest_pct: float, strong_pct: float) -> str:
    """Drawdown from running ATH (%) for the last 3 years, as a base64 PNG string."""
    dd = (series / series.cummax() - 1) * 100
    last = series.index[-1]
    dd = dd[dd.index > last - pd.DateOffset(years=3)]

    fig, ax = plt.subplots(figsize=(7, 2.6), dpi=100)
    ax.plot(dd.index, dd.values, color="#1f4e79", linewidth=1.2)
    ax.axhline(-invest_pct, color="#e0a800", linestyle="--", linewidth=1,
               label=f"INVEST (-{invest_pct:g}%)")
    ax.axhline(-strong_pct, color="#c62828", linestyle="--", linewidth=1,
               label=f"STRONG INVEST (-{strong_pct:g}%)")
    ax.set_ylabel("Drawdown from ATH (%)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower left", fontsize=8, frameon=False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _pct(value: Optional[float], signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}" if signed else f"{value:.1f}"


def _eur(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _verdict_class(signal) -> str:
    if signal.verdict == "No data":
        return "nodata"
    if signal.tier == "STRONG INVEST":
        return "strong"
    if signal.tier == "INVEST":
        return "invest"
    return "no-signal"


def render_report(path, headline: str, generated_at: datetime, indices: list, metrics_list: list,
                  signals: list, sources: list, notes: list, thresholds: dict,
                  contribution_day_flag: bool, series_list: Optional[list] = None,
                  thresholds_list: Optional[list] = None) -> None:
    """Write the self-contained HTML report to *path* (UTF-8).

    *series_list* holds the close Series per index (used for the charts); when an
    entry is None or empty the chart section says that no data is available.

    *thresholds* is the GLOBAL block and is what the footer's signal definitions quote.
    *thresholds_list* holds each index's EFFECTIVE block (pipeline.RunResult.thresholds_list) and
    is what that index's chart draws its guide lines from; when it is None every index falls back
    to the global block, so the older positional call signature keeps working.
    """
    series_list = series_list or [None] * len(indices)
    invest = float(thresholds["ath_drawdown_invest_pct"])
    strong = float(thresholds["ath_drawdown_strong_invest_pct"])
    thresholds_list = thresholds_list or [thresholds] * len(indices)
    esc = html.escape

    present = [m for m in metrics_list if m is not None]
    as_of = max(m.last_date for m in present) if present else None
    as_of_txt = as_of.isoformat() if as_of else "n/a"
    ytd_bases = [m.ytd_base_date for m in present if m.ytd_base_date is not None]
    ytd_txt = max(ytd_bases).isoformat() if ytd_bases else "n/a"

    out = []
    out.append("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">")
    out.append(f"<title>{esc(headline)}</title>")
    out.append(f"<style>{_STYLE}</style>\n</head>\n<body>")
    out.append(f"<h1>{esc(headline)}</h1>")
    out.append(
        f"<p class=\"meta\">Generated {generated_at.strftime('%Y-%m-%d %H:%M')} local. "
        "Prices are closes of EUR-listed ETF proxies, not fund NAVs.</p>"
    )

    degraded = []
    for idx, src, note in zip(indices, sources, notes):
        if src in ("cache", "none"):
            label = "cached data" if src == "cache" else "no data"
            txt = f"{esc(idx['name'])} ({esc(idx['ticker'])}): {label}"
            if note:
                txt += f" - {esc(note)}"
            degraded.append(txt)
    if degraded:
        out.append("<div class=\"banner amber\"><strong>Warning:</strong> "
                   + "; ".join(degraded) + "</div>")
    if contribution_day_flag:
        out.append("<div class=\"banner green\"><strong>Monthly contribution day</strong></div>")

    headers = [
        "Index / fund held", "Proxy ETF", f"Close (EUR, as of {as_of_txt})",
        "All-time high (EUR, date)", "Below ATH (%)", "52-week high (EUR)",
        "Below 52w high (%)", "200-day SMA (EUR)", "vs 200-day SMA (%, + above / - below)",
        "1-day change (%)", "1-month change (%)", f"YTD change (%, since {ytd_txt})",
        "Verdict", "Supporting factors", "History since",
    ]
    out.append("<table>\n<thead><tr>" + "".join(f"<th>{esc(h)}</th>" for h in headers)
               + "</tr></thead>\n<tbody>")

    for idx, m, sig, src in zip(indices, metrics_list, signals, sources):
        cells = []
        cells.append(f"<td class=\"text\"><strong>{esc(idx['name'])}</strong><br>"
                     f"<small>{esc(idx['fund_held'])}</small></td>")
        cells.append(f"<td class=\"text\">{esc(idx['ticker'])}</td>")
        if m is None:
            cells.extend("<td>n/a</td>" for _ in range(10))
        else:
            close_txt = _eur(m.last_close)
            if as_of is not None and m.last_date < as_of:
                close_txt += f" ({m.last_date.isoformat()})"
            cells.append(f"<td>{close_txt}</td>")
            cells.append(f"<td>{_eur(m.ath)} ({m.ath_date.isoformat()})</td>")
            cells.append(f"<td>{_pct(m.below_ath_pct)}</td>")
            cells.append(f"<td>{_eur(m.high_52w)}</td>")
            cells.append(f"<td>{_pct(m.below_52w_pct)}</td>")
            cells.append(f"<td>{_eur(m.sma200)}</td>")
            cells.append(f"<td>{_pct(m.vs_sma200_pct, signed=True)}</td>")
            cells.append(f"<td>{_pct(m.change_1d_pct, signed=True)}</td>")
            cells.append(f"<td>{_pct(m.change_1m_pct, signed=True)}</td>")
            cells.append(f"<td>{_pct(m.ytd_pct, signed=True)}</td>")
        verdict_txt = esc(sig.verdict)
        if src == "cache" and m is not None:
            # Amber badge beside the verdict when the row was served from the cache.
            verdict_txt += f"<br><span class=\"badge\">cached {m.last_date.isoformat()}</span>"
        cells.append(f"<td class=\"{_verdict_class(sig)}\">{verdict_txt}</td>")
        supporting = "<br>".join(esc(s) for s in sig.supporting) if sig.supporting else "-"
        cells.append(f"<td class=\"text\">{supporting}</td>")
        cells.append(f"<td>{m.history_start.isoformat() if m else 'n/a'}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody>\n</table>")

    for idx, series, eff in zip(indices, series_list, thresholds_list):
        title = f"{idx['name']} - drawdown from running all-time high, last 3 years (%)"
        out.append(f"<section>\n<h2>{esc(title)}</h2>")
        if series is None or len(series) == 0:
            out.append("<p>No price data available for this index.</p>")
        else:
            png = drawdown_chart_png_b64(series, float(eff["ath_drawdown_invest_pct"]),
                                         float(eff["ath_drawdown_strong_invest_pct"]))
            out.append(f"<img alt=\"{esc(title)}\" src=\"data:image/png;base64,{png}\">")
        out.append("</section>")

    # One extra sentence, and only when an index really is evaluated against different numbers:
    # the definitions above stay on the global thresholds that most indices use.
    overrides = [
        f"{esc(idx['name'])} uses {float(eff['ath_drawdown_invest_pct']):g}% / "
        f"{float(eff['ath_drawdown_strong_invest_pct']):g}%"
        for idx, eff in zip(indices, thresholds_list)
        if float(eff["ath_drawdown_invest_pct"]) != invest
        or float(eff["ath_drawdown_strong_invest_pct"]) != strong
    ]
    override_txt = (f" Own thresholds: {'; '.join(overrides)}." if overrides else "")

    out.append(
        "<footer>\n<p><strong>Signal definitions:</strong> "
        f"INVEST when the close is at least {invest:g}% below the all-time high of the cached history; "
        f"STRONG INVEST when it is at least {strong:g}% below. Supporting factors: close at least "
        f"{float(thresholds['high_52w_drawdown_pct']):g}% below the 52-week high; close below the "
        f"200-day simple moving average.{override_txt}</p>\n"
        "<p>Rule-based reminder, not investment advice.</p>\n</footer>"
    )
    out.append("</body>\n</html>")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
