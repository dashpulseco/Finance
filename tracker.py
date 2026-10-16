"""Finan Index Tracker - daily rule-based "is it time to invest?" check.

Usage: py -3 tracker.py [--no-toast] [--force-refresh]

The collection work lives in finan.pipeline, shared with jobs/daily_job.py. What stays here is the
local Windows presentation: report.html, the console summary and the toast.
"""

import argparse
import logging
import sys
import traceback

from finan.notify import sanitize_toast_text, send_toast
from finan.pipeline import collect, ensure_dirs, log_run_line, setup_logging
from finan.report import render_report
from finan.rules import build_toast_body
from finan.settings import REPORT_PATH, load_config

log = logging.getLogger("finan")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Finan Index Tracker")
    parser.add_argument("--no-toast", action="store_true", help="skip the Windows toast")
    parser.add_argument("--force-refresh", action="store_true",
                        help="re-download full history instead of the incremental tail")
    return parser.parse_args(argv)


def run(args) -> int:
    config = load_config()
    run_result = collect(config, force_refresh=args.force_refresh)

    body_lines = build_toast_body(run_result.indices, run_result.metrics_list, run_result.signals,
                                  run_result.sources, config["contribution_reminder_day"],
                                  run_result.today)

    render_report(REPORT_PATH, run_result.headline, run_result.generated_at, run_result.indices,
                  run_result.metrics_list, run_result.signals, run_result.sources,
                  run_result.notes, config["thresholds"], run_result.contribution_day,
                  series_list=run_result.series_list,
                  thresholds_list=run_result.thresholds_list)

    print(run_result.headline)
    for idx, sig in zip(run_result.indices, run_result.signals):
        print(f"{idx['name']}: {sig.verdict}")
    print(f"Report: {REPORT_PATH}")

    toast_backend = "skipped"
    if not args.no_toast:
        launch_url = REPORT_PATH.resolve().as_uri()
        try:
            toast_backend = send_toast(sanitize_toast_text(run_result.headline),
                                       sanitize_toast_text("\n".join(body_lines)),
                                       launch_url, config["app_name"])
        except RuntimeError as exc:
            # Report and log were produced, so the run still counts as a success.
            log.error("Toast not shown: %s", exc)
            toast_backend = "failed"

    log_run_line(run_result, toast=toast_backend)
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    ensure_dirs()
    setup_logging()
    try:
        return run(args)
    except Exception:
        log.error("Tracker failed:\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
