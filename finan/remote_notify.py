"""Notification text and the two remote transports (Web Push, ntfy).

PRIVACY: this module is the only code that touches the secrets, and it never publishes them.
VAPID_PRIVATE_KEY, PUSH_SUBSCRIPTIONS and NTFY_TOPIC are read from the environment and nothing
else: no value, no push endpoint, no topic and no ``str(exc)`` from a transport exception (which
can embed the endpoint or the topic URL) is ever logged, annotated or written to disk. The
repository and its Actions logs are PUBLIC.

Module-scope imports are the standard library plus ``finan.rules`` (for ``short_name`` only).
``pywebpush``, ``py_vapid`` and ``requests`` are imported INSIDE their transport function, so a
Windows machine with only requirements-windows.txt applied still runs ``tracker.py`` and
``jobs/daily_job.py --no-notify``; a missing library is reported as ``skipped(no-lib)``, not a
crash. ``finan.report`` (matplotlib) and ``finan.notify`` (winotify) are never imported here.
"""

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from finan.rules import short_name

log = logging.getLogger("finan")

# ntfy topics are the credential: the topic is the whole path, so anyone who knows it can read and
# publish. Validated here rather than trusted, and never logged.
_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_TOPIC_SHORT = 16          # valid but guessable below this length

_BODY_LIMIT = 300          # far inside the ~4 KB encrypted Web Push limit; what iOS renders
_VAPID_KEY_BYTES = 32      # raw P-256 private scalar
_P256DH_BYTES = 65         # uncompressed point, leading 0x04
_AUTH_BYTES = 16

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_EXPIRED_STATUSES = frozenset({404, 410})
_RETRY_DELAY = 3           # seconds, one retry

# "push" is the summary token, "web_push" the config section and the TransportResult name.
_SUMMARY_KEY = {"web_push": "push", "ntfy": "ntfy"}


class ConfigError(Exception):
    """A secret or notification setting cannot possibly work. Fatal, never a transport failure."""


@dataclass
class TransportResult:
    """How one transport went. *detail* never contains a secret.

    For ``state="skipped"`` *detail* is the short reason token the summary grammar uses
    ("no-lib" | "no-key" | "no-subs" | "no-topic" | "bad-topic"); the human sentence goes to the
    WARNING that accompanies it. For every other state it is free text (an exception class name,
    an HTTP status) or empty.
    """
    name: str          # "web_push" | "ntfy"
    state: str         # "sent" | "partial" | "expired" | "dry-run" | "skipped" | "disabled" | "failed"
    sent: int = 0
    total: int = 0
    detail: str = ""


@dataclass
class NotifyOutcome:
    """The whole notification stage: one TransportResult per known transport, in dispatch order."""
    results: list = field(default_factory=list)

    def summary(self) -> str:
        """The exact ``NOTIFY`` line tokens, e.g. 'push=1/1 ntfy=off'.

        One token per result per state, space-joined in dispatch order, from the fixed grammar.
        """
        return " ".join(f"{_SUMMARY_KEY[r.name]}={_token(r)}" for r in self.results)

    @property
    def exit_code(self) -> int:
        considered = [r for r in self.results if r.state != "disabled"]
        if not considered:                                      # nothing enabled at all
            return 0
        if any(r.state in ("sent", "partial", "expired", "dry-run") for r in considered):
            return 0                                            # the phone was reached (or would)
        if all(r.state == "skipped" for r in considered):
            return 0                                            # enabled but not set up yet
        return 1                                                # something failed, nothing worked


def _token(result: TransportResult) -> str:
    """The summary value for one result. Web push counts subscriptions; ntfy is a single target."""
    state = result.state
    if state == "disabled":
        return "off"
    if state == "skipped":
        return f"skipped({result.detail})"
    if result.name == "web_push":
        if state in ("sent", "partial", "failed"):
            return f"{result.sent}/{result.total}"
        if state == "expired":
            # Only reachable when nothing was sent and every failure was a 404/410, so the
            # expired count is the total by construction.
            return f"expired({result.total})"
        return f"dry-run({result.total})"
    return state                                                # "sent" | "failed" | "dry-run"


def annotate(message: str) -> None:
    """GitHub Actions warning annotation; a no-op anywhere else.

    The only print in this module. Always paired with a real log record, never used instead of
    one: the log line is what the user reads locally and what logs/daily_job.log keeps.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{message}", flush=True)


def _b64url_decode(value: str) -> Optional[bytes]:
    """Decode a base64url string, padding it first. None when it is not base64url at all."""
    text = "".join(value.split())                       # tolerate wrapped or trailing newlines
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
    except Exception:
        return None


def _header_safe(text: str) -> str:
    """HTTP header values must be latin-1-encodable; the ntfy Title is forced to ASCII."""
    return text.encode("ascii", "replace").decode("ascii")


# --------------------------------------------------------------------------- notification text

def build_notification(payload: dict) -> dict:
    """Build {title, body, url, tag} from a published payload and nothing else.

    Working from the payload (not from RunResult) is what makes --notify-only deterministic and
    the text testable from a fixture. Unlike the Windows toast the text is NOT ASCII-sanitised:
    it travels as JSON over HTTPS, not through a PowerShell command line, so
    notify.sanitize_toast_text is deliberately not reused.
    """
    stale = bool(payload["stale"])
    data_as_of = payload["data_as_of"]
    thresholds = payload["thresholds"]

    title = (f"{payload['app_name']}: prices unavailable today" if stale
             else payload["headline"])

    if stale:
        state = (f"Could not refresh prices today - showing {data_as_of}." if data_as_of
                 else "Could not refresh prices today - no previous close to show.")
    elif payload["overall_level"] == "strong":
        state = (f"Deep drawdown: more than {thresholds['strong_invest_pct']:g}% below the "
                 f"all-time high.")
    elif payload["overall_level"] == "invest":
        state = (f"Down more than {thresholds['invest_pct']:g}% from the all-time high. "
                 f"A good day to add.")
    elif payload["overall_level"] == "nodata":
        # Least reachable of the four: a fresh run with every source "none" goes to
        # _stale_fallback instead, so only a hand-built payload reaches this line.
        state = "No price data today."
    else:
        state = "Markets near their highs. Stick to your monthly contribution."

    lines = [state]
    if stale:
        lines.append(payload["headline"])               # yesterday's verdict, not lost, not the title
    for idx in payload["indices"]:
        label = short_name(idx["name"])
        # Keyed on the PUBLISHED fields. The second half of the guard is not redundant: the
        # payload may have been read off disk, and formatting None with :.1f raises TypeError.
        if idx["level"] == "nodata" or idx["below_ath_pct"] is None:
            lines.append(f"{label}: no data")
        else:
            lines.append(f"{label}: -{idx['below_ath_pct']:.1f}% ATH, "
                         f"{idx['tier'] or 'no signal'}")
    if payload["contribution_due"]:
        lines.append("Monthly contribution day.")

    return {
        "title": title,
        "body": "\n".join(lines)[:_BODY_LIMIT],
        # app_url is always present and is "" until the user fills it in, so a .get() default
        # would never fire. The service worker resolves "./" against its own scope.
        "url": payload.get("app_url") or "./",
        "tag": f"finan-{data_as_of}" if data_as_of else "finan-nodata",
    }


# --------------------------------------------------------------------------- secrets

def _parse_subscriptions(raw: str) -> list:
    """Parse PUSH_SUBSCRIPTIONS into a list without validating the entries.

    Raises ConfigError for the two structural failures, which no retry can fix. The variable's
    value is never part of the message.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        raise ConfigError("PUSH_SUBSCRIPTIONS is not a JSON list of subscriptions") from None
    if isinstance(data, dict):
        return [data]                                   # a single subscription object is accepted
    if not isinstance(data, list):
        raise ConfigError("PUSH_SUBSCRIPTIONS is not a JSON list of subscriptions")
    return data


def _subscription_error(sub) -> Optional[str]:
    """Why this entry is unusable, or None. The reason never quotes the endpoint or the keys."""
    if not isinstance(sub, dict):
        return "not an object"
    endpoint = sub.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        return "endpoint missing or not https"
    keys = sub.get("keys")
    if not isinstance(keys, dict):
        return "keys missing"
    for name, length, prefix in (("p256dh", _P256DH_BYTES, 0x04), ("auth", _AUTH_BYTES, None)):
        value = keys.get(name)
        if not isinstance(value, str) or not value:
            return f"keys.{name} missing"
        decoded = _b64url_decode(value)
        if decoded is None:
            return f"keys.{name} is not base64url"
        if len(decoded) != length:
            return f"keys.{name} is not {length} bytes"
        if prefix is not None and decoded[0] != prefix:
            return f"keys.{name} is not an uncompressed P-256 point"
    return None


def load_subscriptions(raw: Optional[str]) -> list:
    """Parse and validate PUSH_SUBSCRIPTIONS, skipping bad entries with an index-only warning.

    One stale entry must not stop the other devices, so a rejected entry is a WARNING and not a
    ConfigError. The warning carries the 1-based index and the failed check - never the endpoint.
    """
    if not raw or not raw.strip():
        return []
    subscriptions = []
    for i, sub in enumerate(_parse_subscriptions(raw), start=1):
        reason = _subscription_error(sub)
        if reason:
            log.warning("subscription #%d rejected: %s", i, reason)
            annotate(f"subscription #{i} rejected: {reason}")
            continue
        subscriptions.append(sub)
    return subscriptions


def _validate_secrets(config: dict) -> None:
    """Raise ConfigError for a secret that cannot possibly work, before any transport runs.

    A MISSING variable is not an error: that is the documented mid-setup state, which yields
    skipped(...) plus a warning. Every message names the variable and nothing else.
    """
    notifications = config["notifications"]
    if notifications["web_push"]["enabled"]:
        key = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
        if key:
            decoded = _b64url_decode(key)
            if decoded is None or len(decoded) != _VAPID_KEY_BYTES:
                raise ConfigError("VAPID_PRIVATE_KEY is not a 32-byte base64url key")
        raw = os.environ.get("PUSH_SUBSCRIPTIONS")
        if raw and raw.strip():
            _parse_subscriptions(raw)                   # structural check only; entries later
    # ntfy has no fatal secret shape: a missing or malformed NTFY_TOPIC is skipped(...), because
    # the topic is a user-chosen string and the transport is optional.


# --------------------------------------------------------------------------- transports

def _skipped(name: str, reason: str, message: str) -> TransportResult:
    log.warning("%s", message)
    annotate(message)
    return TransportResult(name, "skipped", detail=reason)


def _log_dry_run(name: str, note: dict) -> None:
    # A log record, not a print: setup_logging attaches both a StreamHandler (so it reaches the
    # console) and a FileHandler (so logs/daily_job.log keeps it). annotate stays the only print.
    log.info("%s dry-run notification:\n%s", name, f"{note['title']}\n{note['body']}")


def send_web_push(note: dict, cfg: dict, dry_run: bool = False) -> TransportResult:
    """Encrypt and POST the note to every configured subscription.

    skipped(...) precedence is fixed and first-match-wins: no-lib -> no-key -> no-subs. Library
    first because it is the only one of the three that no repository secret can fix.
    """
    try:
        import requests                                 # inside, same as pywebpush
        from py_vapid import VapidException
        from pywebpush import WebPushException, webpush
    except ImportError:
        return _skipped("web_push", "no-lib", "web push skipped: pywebpush is not installed")

    private_key = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
    if not private_key:
        return _skipped("web_push", "no-key",
                        "web push skipped: VAPID_PRIVATE_KEY is not set")
    subscriptions = load_subscriptions(os.environ.get("PUSH_SUBSCRIPTIONS"))
    if not subscriptions:
        return _skipped("web_push", "no-subs",
                        "web push skipped: PUSH_SUBSCRIPTIONS holds no usable subscription")

    total = len(subscriptions)
    if dry_run:                                         # only for a fully configured transport
        _log_dry_run("web_push", note)
        return TransportResult("web_push", "dry-run", sent=0, total=total)

    data = json.dumps(note, ensure_ascii=False)
    sent = 0
    expired = 0
    for i, sub in enumerate(subscriptions, start=1):
        for attempt in (1, 2):                          # one retry, per the response table
            try:
                webpush(
                    subscription_info=sub,
                    data=data,
                    vapid_private_key=private_key,      # base64url raw 32-byte scalar
                    vapid_claims={"sub": cfg["vapid_subject"]},   # pywebpush fills aud and exp
                    ttl=cfg.get("ttl_seconds", 86400),
                    timeout=10,
                    headers={"Urgency": cfg.get("urgency", "normal")},
                )
                sent += 1                               # webpush() returns for 200/201/202
                break
            except VapidException as exc:
                # Not a per-subscription outcome: the subject cannot work for any device.
                raise ConfigError(
                    f"notifications.web_push.vapid_subject rejected by py-vapid: {exc}"
                ) from exc
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in _EXPIRED_STATUSES:
                    message = (f"subscription #{i} is stale (HTTP {status}) - "
                               f"re-subscribe in the app")
                    log.warning("%s", message)
                    annotate(message)
                    expired += 1
                    break
                if status is None:
                    # Library- or socket-level, no HTTP response. str(exc) can embed the
                    # endpoint, so only the exception class name is logged.
                    log.warning("subscription #%d failed: %s with no HTTP response",
                                i, type(exc).__name__)
                    annotate(f"subscription #{i} failed: {type(exc).__name__}")
                    break
                if status in _RETRY_STATUSES and attempt == 1:
                    time.sleep(_RETRY_DELAY)
                    continue
                # Any other 4xx will not fix itself on retry: status and reason only, never the
                # response body (which may echo the request) and never the endpoint.
                log.warning("subscription #%d failed: HTTP %s %s", i, status,
                            getattr(exc.response, "reason", "") or "")
                annotate(f"subscription #{i} failed: HTTP {status}")
                break
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == 1:
                    time.sleep(_RETRY_DELAY)
                    continue
                log.warning("subscription #%d failed: %s", i, type(exc).__name__)
                annotate(f"subscription #{i} failed: {type(exc).__name__}")
                break

    if sent == total:
        state = "sent"
    elif sent:
        state = "partial"
    elif expired == total:
        state = "expired"                               # nothing sent, every failure a 404/410
    else:
        state = "failed"
    return TransportResult("web_push", state, sent=sent, total=total)


def send_ntfy(note: dict, cfg: dict, dry_run: bool = False) -> TransportResult:
    """POST the note to a ntfy topic.

    skipped(...) precedence is fixed and first-match-wins: no-lib -> no-topic -> bad-topic.
    "no-topic" means the variable is absent; "bad-topic" means it is set but fails the rule, which
    is a different mistake with a different fix.
    """
    try:
        import requests                                 # inside, so no-lib is reportable
    except ImportError:
        return _skipped("ntfy", "no-lib", "ntfy skipped: requests is not installed")

    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    if not topic:
        return _skipped("ntfy", "no-topic", "ntfy skipped: NTFY_TOPIC is not set")
    if not _TOPIC_RE.match(topic):
        return _skipped("ntfy", "bad-topic",
                        "ntfy skipped: NTFY_TOPIC must be 8-64 characters of letters, digits, "
                        "underscores and hyphens")
    if len(topic) < _TOPIC_SHORT:
        log.warning("NTFY_TOPIC is shorter than %d characters; the topic is the only secret "
                    "protecting the feed, so a short one is guessable", _TOPIC_SHORT)

    headers = {"Title": _header_safe(note["title"]), "Priority": cfg["priority"]}
    if cfg["tags"]:
        headers["Tags"] = cfg["tags"]
    if note["url"].startswith("https://"):
        # Only a non-empty absolute app_url. note["url"] falls back to the relative "./", which
        # the service worker resolves against its own scope and ntfy cannot resolve at all.
        headers["Click"] = note["url"]

    if dry_run:
        _log_dry_run("ntfy", note)
        return TransportResult("ntfy", "dry-run", sent=0, total=1)

    url = f"{cfg['server'].rstrip('/')}/{topic}"         # never logged: the topic is the credential
    body = note["body"].encode("utf-8")                  # the body stays UTF-8; only the Title is ASCII
    for attempt in (1, 2):
        try:
            response = requests.post(url, data=body, headers=headers, timeout=10)
            if 200 <= response.status_code < 300:
                return TransportResult("ntfy", "sent", sent=1, total=1)
            detail = f"ntfy POST failed: HTTP {response.status_code}"
        except requests.RequestException as exc:
            # str(exc) carries the request URL, and the topic in that path IS the credential.
            detail = f"ntfy POST failed: {type(exc).__name__}"
        if attempt == 1:
            time.sleep(_RETRY_DELAY)
            continue
        log.warning("%s", detail)
        annotate(detail)
        return TransportResult("ntfy", "failed", sent=0, total=1, detail=detail)


# --------------------------------------------------------------------------- dispatch

_TRANSPORTS = (("web_push", send_web_push), ("ntfy", send_ntfy))      # fixed dispatch order


def _transports(config: dict):
    """Yield (name, send, cfg) for EVERY known transport, enabled or not.

    Every transport, because summary() emits one token per result and exit_code filters on
    state != "disabled": a generator that skipped the disabled ones would produce an empty
    summary string instead of the required 'push=off ntfy=off'.
    """
    notifications = config["notifications"]
    for name, send in _TRANSPORTS:
        yield name, send, notifications[name]


def dispatch(payload: dict, config: dict, dry_run: bool = False) -> NotifyOutcome:
    """Run every enabled transport; one transport's failure never stops the other.

    dispatch decides WHETHER a transport runs (the enabled short-circuit lives here, once);
    send_* decides HOW it went. _validate_secrets is called outside the per-transport try, so a
    malformed secret can never be downgraded to a soft transport failure.
    """
    note = build_notification(payload)
    _validate_secrets(config)                           # raises ConfigError; NOT inside the try
    results = []
    for name, send, cfg in _transports(config):
        if not cfg["enabled"]:
            results.append(TransportResult(name, "disabled"))
            continue
        try:
            results.append(send(note, cfg, dry_run=dry_run))
        except ConfigError:
            raise                                       # fatal: daily_job maps it to exit 1
        except Exception as exc:                        # genuinely unexpected -> a soft failure
            log.warning("%s transport raised %s", name, type(exc).__name__)
            annotate(f"{name} transport raised {type(exc).__name__}")
            results.append(TransportResult(name, "failed", detail=type(exc).__name__))
    return NotifyOutcome(results)
