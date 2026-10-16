"""Add, remove or rename a tracked fund in config.json - safely.

Run by path, from the Finan folder:

    py -3 scripts\\manage_funds.py add --ticker VVSM.DE --display-name "Semiconductors"
    py -3 scripts\\manage_funds.py rename --ticker VVSM.DE --fund-held "Watchlist"
    py -3 scripts\\manage_funds.py remove --ticker VVSM.DE

Add `--dry-run` to any of them to see the exact diff and write nothing.

Two things make this safe to point at the one file the whole tracker depends on:

* `add` goes through scripts/fund_search.validate first, so a non-EUR listing, a ticker with no
  history or a duplicate is refused in plain language BEFORE anything is written. A listing quoted
  in pence would make every drawdown wrong by a factor of 100, which is why that refusal is hard.
* the write is atomic AND gated: the new text goes to config.json.tmp, finan.settings.load_config
  is run against that temp file, and only a clean load earns the os.replace. A refused load
  deletes the temp file and leaves config.json untouched.

config.json is rewritten with json.dumps(indent=2): top-level key order is preserved, and the only
formatting change is that the three hand-written compact `indices` entries become standard 2-space
blocks on the first write (decision D5 of plan-addfund.md).
"""
import sys
from pathlib import Path

# Make `import finan` and `import fund_search` work when this file is run by path
# (py -3 scripts/manage_funds.py) from any cwd: running a script puts its own folder on sys.path
# (that is what finds fund_search), not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse      # noqa: E402  (the bootstrap above must run before the finan imports)
import difflib       # noqa: E402
import json          # noqa: E402
import os            # noqa: E402
import re            # noqa: E402
from datetime import date  # noqa: E402

import fund_search                                             # noqa: E402
from finan.settings import CONFIG_PATH, load_config, resolve_thresholds   # noqa: E402

# settings.load_config requires a non-empty fund_held, so a fund being watched rather than held
# gets this stand-in rather than a config the next run would refuse to load (decision D7).
WATCHLIST_FUND_HELD = "Watchlist (not held)"

# Yahoo's exchange codes, spelled the way the three existing ticker_note lines spell them.
EXCHANGE_LABELS = {
    "GER": "Xetra",
    "AMS": "Amsterdam",
    "MIL": "Milan",
    "EBS": "SIX Swiss",
    "PAR": "Paris",
    "FRA": "Frankfurt",
}

# Trailing noise on an official fund name: "VanEck Semiconductor UCITS ETF" -> "VanEck
# Semiconductor". The display name is a card title on a phone, so the share class and the wrapper
# are the first things worth dropping.
_NAME_NOISE = ("(acc)", "acc", "(dist)", "dist", "etf", "etfs", "ucits", "plc")

# The two percentage flags, by the config key they land on, so a refusal names the flag the user
# actually typed.
_THRESHOLD_FLAGS = {
    "ath_drawdown_invest_pct": "--invest-pct",
    "ath_drawdown_strong_invest_pct": "--strong-invest-pct",
}


def clean_display_name(name: str) -> str:
    """The official name with repeated whitespace and the trailing wrapper words removed."""
    text = re.sub(r"\s+", " ", (name or "")).strip()
    trimming = True
    while trimming:
        trimming = False
        lowered = text.lower()
        for noise in _NAME_NOISE:
            if lowered.endswith(" " + noise):
                text = text[: len(text) - len(noise)].strip(" ,-")
                trimming = True
                break
    return text


def exchange_label(code) -> str:
    """A human exchange name, falling back to the raw code so an unknown venue still reads."""
    raw = (code or "").strip()
    return EXCHANGE_LABELS.get(raw.upper(), raw) or "unknown exchange"


def build_ticker_note(verdict: dict, today=None) -> str:
    """"<short name>, <exchange>, <currency>. Verified <date>." - the style of the existing notes."""
    today = today or date.today()
    return (f"{verdict.get('short_name') or verdict['ticker']}, "
            f"{exchange_label(verdict.get('exchange'))}, "
            f"{verdict.get('currency') or '?'}. Verified {today.isoformat()}.")


def read_config_text(path: Path) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def find_index(config: dict, ticker: str):
    """The position of *ticker* in config['indices'], compared case-insensitively, or None."""
    for position, entry in enumerate(config.get("indices") or []):
        if isinstance(entry, dict) and str(entry.get("ticker", "")).upper() == ticker.upper():
            return position
    return None


# --------------------------------------------------------------------------- output


class Refusal(Exception):
    """A reason the requested change was not made, in plain language. Exit code 1."""


def emit(title: str, lines: list, fmt: str) -> None:
    if fmt == "markdown":
        print(f"### {title}")
        print("")
        for line in lines:
            print(f"- {line}")
    else:
        print(title)
        for line in lines:
            print(f"  - {line}")


def emit_diff(path: Path, before: str, after: str, fmt: str) -> None:
    diff = list(difflib.unified_diff(before.splitlines(keepends=True),
                                     after.splitlines(keepends=True),
                                     fromfile=f"{path.name} (now)",
                                     tofile=f"{path.name} (after)"))
    if fmt == "markdown":
        print("```diff")
    for line in diff:
        print(line.rstrip("\n"))
    if fmt == "markdown":
        print("```")
    if not diff:
        print("(no change)")


# --------------------------------------------------------------------------- writing


def write_config(path: Path, config: dict, fmt: str, dry_run: bool, before: str) -> None:
    """Serialise, gate on load_config, then os.replace. Raises Refusal instead of writing junk."""
    after = json.dumps(config, indent=2, ensure_ascii=False) + "\n"

    if dry_run:
        emit_diff(path, before, after, fmt)
        print("")
        print(f"--dry-run: {path.name} was not touched.")
        return

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(after)
    try:
        load_config(tmp)
    except (ValueError, OSError) as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise Refusal(f"the new {path.name} would not load ({exc}), so nothing was written. "
                      f"{path.name} is unchanged.")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- commands


def _thresholds_from_args(args, config: dict, entry: dict) -> None:
    """Turn --invest-pct / --strong-invest-pct into entry['thresholds'], or leave it absent.

    The strong > invest invariant is enforced here, on the EFFECTIVE values, so the error arrives
    before the write rather than from the load_config gate afterwards. One flag alone is allowed:
    the other value then comes from the global block.
    """
    overrides = {}
    if args.invest_pct is not None:
        overrides["ath_drawdown_invest_pct"] = float(args.invest_pct)
    if args.strong_invest_pct is not None:
        overrides["ath_drawdown_strong_invest_pct"] = float(args.strong_invest_pct)
    if not overrides:
        return
    for key, value in overrides.items():
        if value <= 0:
            raise Refusal(f"{_THRESHOLD_FLAGS[key]} must be a positive number of percent, "
                          "like 18.")
    entry["thresholds"] = overrides

    global_thresholds = config.get("thresholds")
    if isinstance(global_thresholds, dict) and all(
            key in global_thresholds for key in ("ath_drawdown_invest_pct",
                                                 "ath_drawdown_strong_invest_pct",
                                                 "high_52w_drawdown_pct", "flag_below_sma200")):
        effective = resolve_thresholds(global_thresholds, entry)
        invest = float(effective["ath_drawdown_invest_pct"])
        strong = float(effective["ath_drawdown_strong_invest_pct"])
    else:
        invest = overrides.get("ath_drawdown_invest_pct", 0.0)
        strong = overrides.get("ath_drawdown_strong_invest_pct", 0.0)
    if strong <= invest:
        raise Refusal(f"--strong-invest-pct ({strong:g}) must be greater than --invest-pct "
                      f"({invest:g}): STRONG INVEST is the deeper fall of the two. "
                      "A flag you did not pass takes the value from the global thresholds.")


def cmd_add(args, path: Path, config: dict, before: str) -> None:
    verdict = fund_search.validate(args.ticker, config_path=path)
    if not verdict["ok"]:
        raise Refusal(" ".join(verdict["reasons"]) or f"{args.ticker} was refused.")
    ticker = verdict["ticker"]
    if find_index(config, ticker) is not None:        # belt and braces: validate checks this too
        raise Refusal(f"{ticker} is already in {path.name}.")

    display_name = (args.display_name or "").strip() or clean_display_name(verdict["short_name"])
    if not display_name:
        raise Refusal(f"could not work out a name for {ticker}. Pass --display-name \"...\".")

    entry = {
        "name": display_name,
        "ticker": ticker,
        "fund_held": (args.fund_held or "").strip() or WATCHLIST_FUND_HELD,
        "ticker_note": build_ticker_note(verdict),
    }
    _thresholds_from_args(args, config, entry)
    config.setdefault("indices", []).append(entry)

    write_config(path, config, args.fmt, args.dry_run, before)
    if args.dry_run:
        return

    metrics = verdict["metrics"] or {}
    lines = [f"name: {entry['name']}",
             f"fund held: {entry['fund_held']}",
             f"note: {entry['ticker_note']}",
             f"history: {verdict['history_days']} days since {metrics.get('history_start')}",
             f"last close: {metrics.get('last_close'):.2f} on {metrics.get('last_date')}"
             if metrics.get("last_close") is not None else "last close: n/a",
             f"{metrics.get('below_ath_pct', 0):.1f}% below its all-time high of "
             f"{metrics.get('ath', 0):.2f}"]
    if "thresholds" in entry:
        lines.append("own thresholds: " + ", ".join(f"{key} {value:g}"
                                                    for key, value in entry["thresholds"].items()))
    if verdict["level"] == "warn":
        lines.extend(verdict["reasons"])
    lines.append("the next run of the tracker will download its full history and show a card "
                 "for it")
    emit(f"Added {ticker} to {path.name}.", lines, args.fmt)


def cmd_remove(args, path: Path, config: dict, before: str) -> None:
    ticker = (args.ticker or "").strip()
    position = find_index(config, ticker)
    if position is None:
        raise Refusal(f"{ticker} is not tracked, so there is nothing to remove. "
                      f"Tracked now: {', '.join(_tickers(config)) or 'none'}.")
    indices = config["indices"]
    if len(indices) == 1:
        raise Refusal(f"{ticker} is the only fund left. The tracker needs at least one, so this "
                      "removal is refused - add the replacement first, then remove this one.")
    removed = indices.pop(position)

    write_config(path, config, args.fmt, args.dry_run, before)
    if args.dry_run:
        return
    emit(f"Removed {removed.get('ticker')} from {path.name}.",
         [f"it was \"{removed.get('name')}\"",
          f"data/{removed.get('ticker')}.csv was left in place, so re-adding this ticker starts "
          "from the cached history instead of downloading it again",
          f"{len(indices)} funds still tracked: {', '.join(_tickers(config))}"],
         args.fmt)


def cmd_rename(args, path: Path, config: dict, before: str) -> None:
    ticker = (args.ticker or "").strip()
    position = find_index(config, ticker)
    if position is None:
        raise Refusal(f"{ticker} is not tracked, so there is nothing to rename. "
                      f"Tracked now: {', '.join(_tickers(config)) or 'none'}.")
    new_name = (args.display_name or "").strip()
    new_held = (args.fund_held or "").strip()
    if not new_name and not new_held:
        raise Refusal("nothing to change: pass --display-name, --fund-held, or both.")

    entry = config["indices"][position]
    changes = []
    if new_name:
        changes.append(f"name: \"{entry.get('name')}\" -> \"{new_name}\"")
        entry["name"] = new_name
    if new_held:
        changes.append(f"fund held: \"{entry.get('fund_held')}\" -> \"{new_held}\"")
        entry["fund_held"] = new_held

    write_config(path, config, args.fmt, args.dry_run, before)
    if args.dry_run:
        return
    emit(f"Updated {entry['ticker']} in {path.name}.", changes, args.fmt)


def _tickers(config: dict) -> list:
    return [str(entry.get("ticker")) for entry in config.get("indices") or []
            if isinstance(entry, dict)]


# --------------------------------------------------------------------------- CLI


def _add_shared(parser) -> None:
    parser.add_argument("--config", default=None,
                        help="the config.json to edit (default the project's own)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the diff and write nothing")
    parser.add_argument("--format", dest="fmt", choices=("text", "markdown"), default="text",
                        help="output format (default text)")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="manage_funds.py",
        description="Add, remove or rename a tracked fund in config.json.")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="track a new fund (validated first)")
    add.add_argument("--ticker", required=True, help="the ticker from a fund_search row")
    add.add_argument("--display-name", default=None,
                     help="the card title (default: the official name, tidied)")
    add.add_argument("--fund-held", default=None,
                     help=f"the fund you actually buy (default \"{WATCHLIST_FUND_HELD}\")")
    add.add_argument("--invest-pct", type=float, default=None,
                     help="this fund's own INVEST threshold in percent below its all-time high")
    add.add_argument("--strong-invest-pct", type=float, default=None,
                     help="this fund's own STRONG INVEST threshold, deeper than --invest-pct")
    _add_shared(add)

    remove = sub.add_parser("remove", help="stop tracking a fund")
    remove.add_argument("--ticker", required=True, help="the ticker to remove")
    _add_shared(remove)

    rename = sub.add_parser("rename", help="change a tracked fund's name or the fund you hold")
    rename.add_argument("--ticker", required=True, help="the ticker to update")
    rename.add_argument("--display-name", default=None, help="the new card title")
    rename.add_argument("--fund-held", default=None, help="the new fund-held line")
    _add_shared(rename)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    path = Path(args.config) if args.config else CONFIG_PATH

    commands = {"add": cmd_add, "remove": cmd_remove, "rename": cmd_rename}
    try:
        before = read_config_text(path)
        config = json.loads(before)
        if not isinstance(config, dict):
            raise Refusal(f"{path.name} is not a JSON object.")
        commands[args.command](args, path, config, before)
    except Refusal as exc:
        emit(f"Nothing was changed in {path.name}:", [str(exc)], args.fmt)
        return 1
    except FileNotFoundError:
        emit("Nothing was changed:", [f"there is no config file at {path.name}."], args.fmt)
        return 1
    except json.JSONDecodeError as exc:
        emit("Nothing was changed:",
             [f"{path.name} is not valid JSON ({exc.msg} on line {exc.lineno}). "
              "Fix it by hand first - this tool refuses to overwrite a file it cannot read."],
             args.fmt)
        return 1
    except fund_search.SearchError as exc:
        emit(f"Nothing was changed in {path.name}:", [str(exc)], args.fmt)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
