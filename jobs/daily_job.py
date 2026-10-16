"""Finan daily cloud job: compute today's signals, publish site/data.json, notify the phone.

PRIVACY: everything this script writes under site/ is published to a PUBLIC GitHub Pages site.
Only public market data may go in there - index levels, highs, drawdowns, changes and verdicts.
Never add portfolio values, contribution amounts, holdings, account numbers or personal data.
Secrets (VAPID private key, push subscriptions, ntfy topic) are read from environment variables
only; they are never written to site/, never logged and never committed.
"""
import sys
from pathlib import Path

# Make `import finan` work when this file is run by path (py -3 jobs/daily_job.py) from any cwd:
# running a script puts its own folder on sys.path, not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse      # noqa: E402  (the bootstrap above must run before the finan imports)
import logging       # noqa: E402
import os            # noqa: E402
import traceback     # noqa: E402
from datetime import datetime, timezone                                           # noqa: E402

from finan import remote_notify                                                   # noqa: E402
from finan.pipeline import collect, ensure_dirs, log_run_line, setup_logging      # noqa: E402
from finan.settings import SITE_DATA_PATH, load_config                            # noqa: E402
from finan.sitedata import (build_site_data, load_site_data, next_run_utc,        # noqa: E402
                            write_site_data)

log = logging.getLogger("finan")


def parse_args(argv=None):
    """Parse the four flags. A contradictory combination is a usage error (exit 2), never a
    silently ignored flag - which would hide a typo in the workflow file."""
    parser = argparse.ArgumentParser(description="Finan daily cloud job")
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument("--no-notify", action="store_true",
                      help="refresh prices and write site/data.json; send no notification")
    stage.add_argument("--notify-only", action="store_true",
                      help="notify from the committed site/data.json; no market access")
    parser.add_argument("--force-refresh", action="store_true",
                        help="re-download full history instead of the incremental tail")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the notification text, log it at INFO, send nothing")
    args = parser.parse_args(argv)
    # --no-notify with --notify-only is already rejected by the mutually exclusive group.
    if args.no_notify and args.dry_run:
        parser.error("--dry-run asks for a dry run of the notification stage, "
                     "which --no-notify switches off")
    if args.notify_only and args.force_refresh:
        parser.error("--force-refresh asks for a full history re-download, "
                     "which --notify-only switches off")
    return args


def _warn_on_cron_drift(config: dict) -> None:
    """Cheap guard against the two copies of the schedule drifting apart.

    The workflow cron is what actually fires and cannot be read from inside the job, so the YAML
    owns the value and passes it as FINAN_CRON_UTC; config.json holds the copy the PWA footer
    reads. A mismatched footer is cosmetic and must never stop the day's notification, so this is
    a WARNING. The variable is unset locally, which makes the guard a no-op outside CI. Neither
    value is a secret.
    """
    expected = os.environ.get("FINAN_CRON_UTC")
    if expected and expected != config["site"]["schedule_cron_utc"]:
        log.warning("schedule_cron_utc=%r does not match the workflow cron %r; "
                    "the PWA footer will advertise a check that does not happen",
                    config["site"]["schedule_cron_utc"], expected)


def _stale_fallback(config: dict, run_result) -> dict:
    """Republish the committed payload, flagged stale, when no index could be refreshed.

    Fatal (through the raised ValueError) if the committed file is missing, unparseable, carries a
    different schema_version or fails the shape checks: there is nothing honest left to publish.
    Every index block and data_as_of are kept; four top-level fields plus schedule.next_check are
    overwritten, and each index's source becomes "cache" - the truth about the republished
    numbers, and what app.js's per-index banner rule branches on. note is left untouched because
    it is yesterday's per-index message and no update_history call produced a new one.

    *config* is part of the signature the flow block calls, but next_check is deliberately
    recomputed from the payload's OWN schedule.cron_utc rather than from config: the republished
    schedule block then stays internally consistent, cron_utc and next_check describing the same
    expression even if config.json has since been edited.
    """
    payload = load_site_data(SITE_DATA_PATH)
    reason = (f"price download failed on {run_result.today}; "
              f"showing data as of {payload['data_as_of']}" if payload["data_as_of"] else
              f"price download failed on {run_result.today}; no previous close to show")
    log.error("no index could be refreshed; republishing the committed payload: %s", reason)
    remote_notify.annotate(f"no index could be refreshed; republishing the committed "
                           f"site/data.json ({reason})")

    next_check = next_run_utc(payload["schedule"]["cron_utc"], datetime.now(timezone.utc))
    payload["generated_at"] = run_result.generated_at.astimezone().isoformat(timespec="seconds")
    payload["data_status"] = "stale"
    payload["stale"] = True
    payload["stale_reason"] = reason
    payload["schedule"]["next_check"] = (None if next_check is None
                                         else next_check.isoformat(timespec="seconds"))
    for index in payload["indices"]:
        index["source"] = "cache"
    return payload


def run(args) -> int:
    config = load_config()                                      # fatal on ValueError
    _warn_on_cron_drift(config)
    if args.notify_only:
        # From disk, not from git: in the workflow this is the file the compute step wrote moments
        # earlier, so the notification always describes the data that was just published.
        payload = load_site_data(SITE_DATA_PATH)
    else:
        run_result = collect(config, force_refresh=args.force_refresh)
        if run_result.all_missing:
            payload = _stale_fallback(config, run_result)
        else:
            payload = build_site_data(run_result)
        write_site_data(SITE_DATA_PATH, payload)
        log_run_line(run_result, data=payload["data_status"])
    if args.no_notify:
        return 0
    try:
        outcome = remote_notify.dispatch(payload, config, dry_run=args.dry_run)
    except remote_notify.ConfigError as exc:                    # malformed secret: never soft
        log.error("notification configuration error: %s", exc)
        remote_notify.annotate(f"notification configuration error: {exc}")
        return 1
    log.info("NOTIFY %s", outcome.summary())
    return outcome.exit_code


def main(argv=None) -> int:
    args = parse_args(argv)
    ensure_dirs()
    setup_logging("daily_job.log")
    try:
        return run(args)
    except Exception:
        log.error("Daily job failed:\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
