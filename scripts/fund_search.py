"""Search Yahoo Finance for a fund, and validate one ticker before it is ever tracked.

Run by path, from the Finan folder:

    py -3 scripts\\fund_search.py search "VanEck Semiconductor"
    py -3 scripts\\fund_search.py validate VVSM.DE

`search` answers a plain-language fund name with the listings Yahoo knows about, enriched with
the three things that decide whether a listing is usable here - its currency, its exchange and how
much history it has - and marks one of them `recommended`. Non-EUR listings are shown rather than
hidden, because seeing that the London line of the same ETF is quoted in pence is what makes the
recommendation make sense.

`validate` is the gate: a ticker that does not pass it must never reach config.json.
The hard refusal is "not quoted in EUR". Everything downstream - site/data.json's hard-coded
"currency": "EUR", the report, the notification - states EUR without converting anything, so a
GBX listing (pence) would silently show closes, an all-time high and a drawdown that are all
wrong by a factor of 100 against the user's real holding.

The preview numbers come from finan.metrics.compute_metrics over finan.market_data.fetch_history -
the same code that will fill the card - so what the user approves here is what they will see.
fetch_history deliberately does not write the price cache, so searching leaves data/ untouched.
"""
import sys
from pathlib import Path

# Make `import finan` work when this file is run by path (py -3 scripts/fund_search.py) from
# any cwd: running a script puts its own folder on sys.path, not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse      # noqa: E402  (the bootstrap above must run before the finan imports)
import contextlib    # noqa: E402
import io            # noqa: E402
import json          # noqa: E402
import logging       # noqa: E402
import re            # noqa: E402
from dataclasses import asdict, dataclass  # noqa: E402
from typing import Optional                # noqa: E402

import requests      # noqa: E402
import yfinance as yf                                      # noqa: E402

from finan.market_data import fetch_history                # noqa: E402
from finan.metrics import compute_metrics                  # noqa: E402
from finan.settings import CONFIG_PATH                     # noqa: E402

SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
# Yahoo's search endpoint answers 404 to a bare python-requests agent and 200 to a browser one.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0.0.0 Safari/537.36")
TIMEOUT_SECONDS = 10

# A 200-day average needs more than 250 trading days to mean anything, and an all-time high taken
# from a three-year window is not an all-time high - it is a three-year high, so say so.
MIN_HISTORY_DAYS = 250
GOOD_HISTORY_DAYS = 750

# Every ticker that arrives from a CLI argument, a workflow input or a config entry is matched
# against this before any network or file use. Yahoo symbols are letters, digits and a few
# separators: EUNL.DE, BRK-B, EURUSD=X, ^STOXX50E.
_TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-=^]{0,19}$")

# GBp and GBX are the same thing: one hundredth of a pound.
_PENCE_CURRENCIES = ("GBP", "GBX")

PENCE_REASON = (
    "this listing is quoted in pence (GBX), not EUR, so its closes, its all-time high and every "
    "drawdown computed from it would be wrong. Search again and pick the Xetra (.DE) or "
    "Amsterdam (.AS) listing of the same fund instead."
)


class SearchError(Exception):
    """The search service could not be reached or answered something unusable.

    The message is plain language for a phone screen and never carries a header, a secret or a
    URL the caller did not supply, because it is printed into a public GitHub Actions summary.
    """


@dataclass
class Candidate:
    """One listing from a search, enriched with what decides whether it is usable."""
    ticker: str
    name: str
    exchange: str
    quote_type: str
    currency: Optional[str] = None
    last_price: Optional[float] = None
    history_days: int = 0
    verified: bool = True
    note: str = ""
    recommended: bool = False

    @property
    def is_eur(self) -> bool:
        return (self.currency or "").upper() == "EUR"


def _get_json(url: str, params: dict):
    """The ONE network entry point: GET *url* as a browser would, with exactly one retry.

    Raises SearchError with a plain-language message on a transport failure, a non-200 answer or
    a body that is not JSON. Nothing about the request itself is echoed into the message.
    """
    problem = "it did not answer"
    for _ in range(2):                      # first try, then exactly one retry
        try:
            response = requests.get(url, params=params,
                                    headers={"User-Agent": USER_AGENT},
                                    timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            problem = f"the connection failed ({type(exc).__name__})"
            continue
        if response.status_code != 200:
            problem = f"it answered HTTP {response.status_code}"
            continue
        try:
            return response.json()
        except ValueError:
            problem = "it answered with something that is not JSON"
    raise SearchError(f"Could not reach the fund search service: {problem}. "
                      "It is usually busy rather than broken - try again in a minute.")


def _fast_value(fast_info, *names):
    """Read one field from a yfinance FastInfo, trying each spelling in *names*.

    yfinance 1.7.0 keys FastInfo in camelCase but exposes snake_case attributes: `keys()` reports
    `lastPrice` while `fast_info.lastPrice` raises AttributeError and `fast_info.last_price`
    works. Probed on this host, so both spellings are tried and neither is assumed.
    """
    for name in names:
        try:
            value = fast_info[name]
        except Exception:
            try:
                value = getattr(fast_info, name)
            except Exception:
                continue
        if value is not None:
            return value
    return None


def _symbol_facts(ticker: str):
    """(currency, exchange, last_price) from yfinance's fast quote. Any of them may be None."""
    fast_info = yf.Ticker(ticker).fast_info
    currency = _fast_value(fast_info, "currency")
    exchange = _fast_value(fast_info, "exchange")
    last_price = _fast_value(fast_info, "lastPrice", "last_price")
    return (str(currency) if currency else None,
            str(exchange) if exchange else None,
            float(last_price) if isinstance(last_price, (int, float)) else None)


def _official_name(ticker: str) -> str:
    """The name Yahoo publishes for *ticker*, or "" when it cannot be looked up.

    Cosmetic only - it becomes the default display name and part of ticker_note - so a search
    failure here must never turn into a refusal.
    """
    try:
        data = _get_json(SEARCH_URL, {"q": ticker, "quotesCount": 10})
        quotes = data.get("quotes") if isinstance(data, dict) else None
        for quote in quotes or []:
            if isinstance(quote, dict) and str(quote.get("symbol", "")).upper() == ticker.upper():
                name = quote.get("shortname") or quote.get("longname") or ""
                return re.sub(r"\s+", " ", str(name)).strip()
    except SearchError:
        return ""
    except Exception:
        return ""
    return ""


@contextlib.contextmanager
def _quiet():
    """Swallow yfinance's own chatter for the duration of one candidate's enrichment.

    A listing yfinance cannot resolve (a thin venue like .MU often has no daily history) makes it
    write a line of its own to stderr. The outcome is already carried by the candidate's
    `could not verify` note, so that line adds nothing - and in the GitHub Actions run summary,
    which is where the user reads this on a phone, it lands ABOVE the table and reads like a
    failure when the search in fact succeeded. Both sinks have to be covered: a logging
    StreamHandler captured sys.stderr when it was created, so redirect_stderr alone misses it.
    """
    logger = logging.getLogger("yfinance")
    previous = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        logger.setLevel(previous)


def _enrich(candidate: Candidate) -> None:
    """Fill in currency, exchange, last price and history length for one candidate.

    A candidate whose enrichment fails is KEPT, marked unverified: a search that drops a listing
    because Yahoo hiccupped is worse than a search that admits it could not check one.
    """
    try:
        with _quiet():
            currency, exchange, last_price = _symbol_facts(candidate.ticker)
            series = fetch_history(candidate.ticker)
        candidate.currency = currency
        candidate.exchange = exchange or candidate.exchange
        candidate.last_price = last_price
        candidate.history_days = 0 if series is None else int(len(series))
        if currency is None or candidate.history_days == 0:
            candidate.verified = False
            candidate.note = "could not verify"
    except Exception:
        candidate.verified = False
        candidate.note = "could not verify"


def _mark_recommended(candidates: list) -> None:
    """Mark the first EUR listing with real history, else the first EUR listing at all."""
    eur = [c for c in candidates if c.is_eur]
    if not eur:
        return
    pick = next((c for c in eur if c.history_days >= GOOD_HISTORY_DAYS), eur[0])
    pick.recommended = True


def search(query: str, limit: int = 10) -> list:
    """Search Yahoo by fund name and return enriched Candidates, best first.

    EUR listings sort first, then longest history first, which is also the order the
    `recommended` marker is chosen in. Non-EUR listings stay in the list, clearly marked.
    """
    query = (query or "").strip()
    if not query:
        raise SearchError("Give a fund name to search for, for example \"VanEck Semiconductor\".")
    limit = max(1, min(int(limit), 25))

    data = _get_json(SEARCH_URL, {"q": query, "quotesCount": limit})
    quotes = data.get("quotes") if isinstance(data, dict) else None
    if not isinstance(quotes, list):
        raise SearchError("The fund search service answered in an unexpected shape. "
                          "Try again in a minute.")

    candidates = []
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        symbol = quote.get("symbol")
        if not isinstance(symbol, str) or not _TICKER_RE.match(symbol):
            continue
        name = quote.get("shortname") or quote.get("longname") or symbol
        candidates.append(Candidate(
            ticker=symbol,
            name=re.sub(r"\s+", " ", str(name)).strip(),
            exchange=str(quote.get("exchange") or "").strip(),
            quote_type=str(quote.get("quoteType") or "").strip(),
        ))
    for candidate in candidates:
        _enrich(candidate)

    candidates.sort(key=lambda c: (0 if c.is_eur else 1, -c.history_days))
    _mark_recommended(candidates)
    return candidates


def _tracked_tickers(config_path: Path) -> set:
    """Upper-cased tickers already in config.json, or an empty set when it cannot be read.

    Read with json.load rather than settings.load_config on purpose: a config that fails
    validation must still produce the clear "already tracked" refusal instead of a stack trace
    about an unrelated key.
    """
    try:
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        return set()
    indices = config.get("indices") if isinstance(config, dict) else None
    if not isinstance(indices, list):
        return set()
    return {str(idx.get("ticker", "")).strip().upper()
            for idx in indices if isinstance(idx, dict)}


def _metrics_dict(metrics) -> dict:
    """The preview numbers, JSON-ready: dates as ISO strings, everything else a plain float."""
    return {
        "last_close": metrics.last_close,
        "last_date": metrics.last_date.isoformat(),
        "ath": metrics.ath,
        "ath_date": metrics.ath_date.isoformat(),
        "below_ath_pct": metrics.below_ath_pct,
        "high_52w": metrics.high_52w,
        "sma200": metrics.sma200,
        "history_start": metrics.history_start.isoformat(),
    }


def _verdict(ticker: str, ok: bool, level: str, reasons: list, **extra) -> dict:
    """Every verdict carries the same keys, so a caller never has to test for their presence."""
    verdict = {
        "ok": ok,
        "level": level,
        "reasons": list(reasons),
        "ticker": ticker,
        "short_name": "",
        "currency": None,
        "exchange": None,
        "history_days": 0,
        "metrics": None,
    }
    verdict.update(extra)
    return verdict


def validate(ticker: str, config_path=None) -> dict:
    """Decide whether *ticker* may be tracked. Returns a verdict dict, never raises on a refusal.

    Checks in this order, so an obvious refusal costs no download: shape, already tracked,
    currency, any data at all, enough data. level is "ok", "warn" (usable, with a caveat) or
    "refused".
    """
    raw = (ticker or "").strip()
    shown = raw[:40] if raw else "(empty)"
    config_path = Path(config_path) if config_path else CONFIG_PATH

    if not _TICKER_RE.match(raw):
        return _verdict(shown, False, "refused", [
            f"\"{shown}\" is not a ticker symbol. A ticker is short and has no spaces, like "
            "EUNL.DE or VVSM.DE - run the `search` command with the fund's name to find it."])

    if raw.upper() in _tracked_tickers(config_path):
        return _verdict(raw, False, "refused",
                        [f"{raw} is already tracked - it is in config.json."])

    # Quiet for the same reason as _enrich: an unknown symbol makes yfinance write its own
    # "No data found" lines to stderr, which would sit above this function's own, more
    # actionable refusal reason in the Actions run summary the user reads on the phone.
    try:
        with _quiet():
            currency, exchange, _last_price = _symbol_facts(raw)
    except Exception:
        currency, exchange = None, None

    if currency is None:
        return _verdict(raw, False, "refused", [
            f"could not determine what currency {raw} is quoted in, so it cannot be checked "
            "against the EUR-only rule. Check the ticker with the `search` command."],
            exchange=exchange)
    if currency.upper() in _PENCE_CURRENCIES:
        return _verdict(raw, False, "refused", [f"{raw} is quoted in {currency}: {PENCE_REASON}"],
                        currency=currency, exchange=exchange)
    if currency.upper() != "EUR":
        return _verdict(raw, False, "refused", [
            f"{raw} is quoted in {currency}, not EUR. This tracker states every price in EUR and "
            "converts nothing, so a non-EUR listing would mix a foreign price with your EUR "
            "holding. Search for the Xetra (.DE) or Amsterdam (.AS) listing of the same fund."],
            currency=currency, exchange=exchange)

    with _quiet():
        series = fetch_history(raw)
    if series is None or series.empty:
        return _verdict(raw, False, "refused", [
            f"{raw} returned no price history at all. Either the ticker is wrong or Yahoo has no "
            "data for it - run the `search` command and use a ticker from the list."],
            currency=currency, exchange=exchange)

    history_days = int(len(series))
    if history_days < MIN_HISTORY_DAYS:
        return _verdict(raw, False, "refused", [
            f"{raw} has only {history_days} days of price history. The signal needs an all-time "
            f"high and a 200-day average to mean something, so at least {MIN_HISTORY_DAYS} days "
            "are required."],
            currency=currency, exchange=exchange, history_days=history_days)

    reasons = []
    level = "ok"
    if history_days < GOOD_HISTORY_DAYS:
        level = "warn"
        reasons.append(
            f"{raw} has {history_days} days of history (about "
            f"{history_days / 252:.1f} years), so its \"all-time high\" only covers that window. "
            "The signal still works, it is just a shorter yardstick than the other funds have.")

    return _verdict(raw, True, level, reasons,
                    short_name=_official_name(raw) or raw,
                    currency=currency,
                    exchange=exchange,
                    history_days=history_days,
                    metrics=_metrics_dict(compute_metrics(series)))


# --------------------------------------------------------------------------- output


def _years(days: int) -> str:
    return f"{days / 252:.1f} yr"          # 252 trading days to the year


def _price(value) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _pct(value) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _flag(candidate: Candidate) -> str:
    """The short marker explaining a candidate's standing, for both output formats."""
    if candidate.recommended:
        return "recommended"
    if not candidate.verified:
        return candidate.note or "could not verify"
    if candidate.is_eur:
        return "EUR, usable"
    if (candidate.currency or "").upper() in _PENCE_CURRENCIES:
        return "pence - do not use"
    return f"{candidate.currency} - not EUR"


def print_search_text(query: str, candidates: list) -> None:
    if not candidates:
        print(f"No listing found for \"{query}\". Try fewer words, or the fund house's name.")
        return
    print(f"{len(candidates)} listings for \"{query}\", EUR first:")
    print("")
    for number, c in enumerate(candidates, start=1):
        marker = " <- recommended" if c.recommended else ""
        print(f"{number:2}. {c.ticker}  {c.currency or '?'}  {c.exchange or '?'}  "
              f"{c.history_days} days ({_years(c.history_days)}){marker}")
        print(f"    {c.name}")
        if not c.recommended:
            print(f"    {_flag(c)}")
    print("")
    print("Only EUR listings can be added: a London line of the same fund is quoted in pence, so "
          "every drawdown computed from it would be wrong.")


def print_search_markdown(query: str, candidates: list) -> None:
    print(f"### Listings for \"{query}\"")
    print("")
    if not candidates:
        print("No listing found. Try fewer words, or the fund house's name.")
        return
    print("| # | Ticker | Exch | Cur | History | Name |")
    print("| --: | --- | --- | --- | --- | --- |")
    for number, c in enumerate(candidates, start=1):
        ticker = f"`{c.ticker}`" + (" **recommended**" if c.recommended else "")
        print(f"| {number} | {ticker} | {c.exchange or '?'} | {c.currency or '?'} | "
              f"{_years(c.history_days)} | {c.name} |")
    print("")
    print("Only EUR listings can be added: a London line of the same fund is quoted in pence, so "
          "every drawdown computed from it would be wrong.")
    recommended = next((c for c in candidates if c.recommended), None)
    if recommended:
        print("")
        print(f"Run this workflow again with **action = add** and **ticker = "
              f"`{recommended.ticker}`** to track it.")


def print_validate_text(verdict: dict) -> None:
    ticker = verdict["ticker"]
    if not verdict["ok"]:
        print(f"{ticker} cannot be added:")
        for reason in verdict["reasons"]:
            print(f"  - {reason}")
        return
    metrics = verdict["metrics"] or {}
    print(f"{ticker} can be added.")
    print(f"  name            {verdict['short_name']}")
    print(f"  exchange        {verdict['exchange'] or '?'}")
    print(f"  currency        {verdict['currency']}")
    print(f"  history         {verdict['history_days']} days since "
          f"{metrics.get('history_start')}")
    print(f"  last close      {_price(metrics.get('last_close'))} on {metrics.get('last_date')}")
    print(f"  all-time high   {_price(metrics.get('ath'))} on {metrics.get('ath_date')}")
    print(f"  below ATH       {_pct(metrics.get('below_ath_pct'))}")
    print(f"  52-week high    {_price(metrics.get('high_52w'))}")
    print(f"  200-day average {_price(metrics.get('sma200'))}")
    for reason in verdict["reasons"]:
        print(f"  note            {reason}")


def print_validate_markdown(verdict: dict) -> None:
    ticker = verdict["ticker"]
    if not verdict["ok"]:
        print(f"### `{ticker}` cannot be added")
        print("")
        for reason in verdict["reasons"]:
            print(f"- {reason}")
        return
    metrics = verdict["metrics"] or {}
    print(f"### `{ticker}` can be added")
    print("")
    print("| | |")
    print("| --- | --- |")
    print(f"| Name | {verdict['short_name']} |")
    print(f"| Exchange | {verdict['exchange'] or '?'} |")
    print(f"| Currency | {verdict['currency']} |")
    print(f"| History | {verdict['history_days']} days since {metrics.get('history_start')} |")
    print(f"| Last close | {_price(metrics.get('last_close'))} on {metrics.get('last_date')} |")
    print(f"| All-time high | {_price(metrics.get('ath'))} on {metrics.get('ath_date')} |")
    print(f"| Below ATH | {_pct(metrics.get('below_ath_pct'))} |")
    print(f"| 52-week high | {_price(metrics.get('high_52w'))} |")
    print(f"| 200-day average | {_price(metrics.get('sma200'))} |")
    for reason in verdict["reasons"]:
        print("")
        print(f"- {reason}")


# --------------------------------------------------------------------------- CLI


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="fund_search.py",
        description="Search Yahoo Finance for a fund, or validate one ticker before adding it.")
    sub = parser.add_subparsers(dest="command", required=True)

    search_parser = sub.add_parser("search", help="find the listings of a fund by name")
    search_parser.add_argument("query", help="the fund name, e.g. \"VanEck Semiconductor\"")
    search_parser.add_argument("--limit", type=int, default=10,
                               help="how many listings to ask for (default 10, max 25)")
    search_parser.add_argument("--format", dest="fmt", choices=("text", "markdown", "json"),
                               default="text", help="output format (default text)")

    validate_parser = sub.add_parser("validate", help="check one ticker against the EUR-only rule")
    validate_parser.add_argument("ticker", help="the ticker from a search row, e.g. VVSM.DE")
    validate_parser.add_argument("--config", default=None,
                                 help="config.json to check for duplicates (default the "
                                      "project's own)")
    validate_parser.add_argument("--format", dest="fmt", choices=("text", "markdown", "json"),
                                default="text", help="output format (default text)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.command == "search":
        try:
            candidates = search(args.query, limit=args.limit)
        except SearchError as exc:
            print(str(exc))
            return 1
        if args.fmt == "json":
            print(json.dumps({"query": args.query, "count": len(candidates),
                              "candidates": [asdict(c) for c in candidates]},
                             indent=2, ensure_ascii=False))
        elif args.fmt == "markdown":
            print_search_markdown(args.query, candidates)
        else:
            print_search_text(args.query, candidates)
        return 0

    try:
        verdict = validate(args.ticker, config_path=args.config)
    except SearchError as exc:
        print(str(exc))
        return 1
    if args.fmt == "json":
        print(json.dumps(verdict, indent=2, ensure_ascii=False))
    elif args.fmt == "markdown":
        print_validate_markdown(verdict)
    else:
        print_validate_text(verdict)
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
