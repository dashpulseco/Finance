"""Paths and configuration loading for the Finan Index Tracker.

All paths are resolved from BASE_DIR (the folder containing tracker.py), never
from the current working directory, because Task Scheduler starts processes in
C:\\Windows\\System32.
"""

import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
REPORT_PATH = BASE_DIR / "report.html"
CONFIG_PATH = BASE_DIR / "config.json"
SITE_DIR = BASE_DIR / "site"
SITE_DATA_PATH = SITE_DIR / "data.json"
PUSH_CONFIG_PATH = SITE_DIR / "push-config.json"
SECRETS_DIR = BASE_DIR / "secrets"

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

# The complete threshold vocabulary, at both scopes: config["thresholds"] (required, all four) and
# the optional per-index config["indices"][i]["thresholds"] (any non-empty subset of these names).
THRESHOLD_KEYS = ("ath_drawdown_invest_pct", "ath_drawdown_strong_invest_pct",
                  "high_52w_drawdown_pct", "flag_below_sma200")
_THRESHOLD_PCT_KEYS = ("ath_drawdown_invest_pct", "ath_drawdown_strong_invest_pct",
                       "high_52w_drawdown_pct")

# Shipped defaults for the two sections added by the PWA/cloud work. Both sections are optional:
# an older config.json (or one that never touches notifications) loads with these injected.
SITE_DEFAULTS = {
    "schedule_cron_utc": "30 18 * * 1-5",
    "schedule_note": "Weekdays after the European close: 20:30 Madrid in summer, "
                     "19:30 in winter",
    "app_url": "",
}
NOTIFICATION_DEFAULTS = {
    "web_push": {
        "enabled": True,
        "vapid_subject": "https://example.github.io",
        "ttl_seconds": 86400,
        "urgency": "normal",
    },
    "ntfy": {
        "enabled": False,
        "server": "https://ntfy.sh",
        "priority": "default",
        "tags": "chart_with_downwards_trend",
    },
}

# py-vapid's rule: a mailto: subject carries an address, an https: subject carries no path.
_VAPID_SUBJECT_RE = re.compile(r"^(mailto:.+@.+|https://[^/\s]+)$")
_HTTPS_URL_RE = re.compile(r"^https://[^\s]+$")
# Header values, so they are validated here rather than trusted: comma-separated safe tokens.
_NTFY_TAGS_RE = re.compile(r"^[A-Za-z0-9_-]+(?:,[A-Za-z0-9_-]+)*$")
_URGENCIES = ("very-low", "low", "normal", "high")
_PRIORITIES = ("min", "low", "default", "high", "max", "1", "2", "3", "4", "5")
_TTL_MAX = 2419200  # 28 days, the Web Push maximum


def _require_positive_number(thresholds: dict, key: str, prefix: str = "thresholds") -> float:
    value = thresholds.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{prefix}.{key} must be a positive number")
    return float(value)


def _validate_index_thresholds(idx: dict, i: int) -> None:
    """Validate the optional per-index override block. Only the keys PRESENT are checked here;
    the resolved four-key dict is re-checked for the strong > invest invariant in load_config."""
    overrides = idx.get("thresholds")
    if overrides is None:
        return
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError(f"indices[{i}].thresholds must be a non-empty object")
    prefix = f"indices[{i}].thresholds"
    for key in overrides:
        if key not in THRESHOLD_KEYS:
            raise ValueError(f"{prefix}.{key} is not a known threshold")
    for key in _THRESHOLD_PCT_KEYS:
        if key in overrides:
            _require_positive_number(overrides, key, prefix)
    if "flag_below_sma200" in overrides and not isinstance(overrides["flag_below_sma200"], bool):
        raise ValueError(f"{prefix}.flag_below_sma200 must be true or false")


def resolve_thresholds(global_thresholds: dict, index_entry: dict) -> dict:
    """The EFFECTIVE thresholds for one index: its own value for a key, else the global one.

    Always returns a complete four-key dict, and mutates neither input - load_config deliberately
    never writes the resolved values back, so config.json stays exactly what the user wrote and
    scripts/manage_funds.py can round-trip it.
    """
    overrides = index_entry.get("thresholds") or {}
    return {key: overrides[key] if key in overrides else global_thresholds[key]
            for key in THRESHOLD_KEYS}


def _require_section(config: dict, key: str, defaults: dict) -> dict:
    """Return config[key], injecting the shipped defaults when it is absent."""
    if key not in config:
        config[key] = json.loads(json.dumps(defaults))
    section = config[key]
    if not isinstance(section, dict):
        raise ValueError(f"{key} must be an object")
    return section


def _require_string(section: dict, key: str, dotted: str) -> str:
    value = section[key]
    if not isinstance(value, str):
        raise ValueError(f"{dotted} must be a string")
    return value


def _require_bool(section: dict, key: str, dotted: str) -> bool:
    value = section[key]
    if not isinstance(value, bool):
        raise ValueError(f"{dotted} must be true or false")
    return value


def _require_choice(section: dict, key: str, dotted: str, allowed: tuple) -> str:
    value = _require_string(section, key, dotted)
    if value not in allowed:
        raise ValueError(f"{dotted} must be one of {'|'.join(allowed)}")
    return value


def _normalise_site(config: dict) -> dict:
    """Validate config['site'], injecting the shipped defaults for anything absent."""
    site = _require_section(config, "site", SITE_DEFAULTS)
    for key, default in SITE_DEFAULTS.items():
        site.setdefault(key, default)

    _require_string(site, "schedule_cron_utc", "site.schedule_cron_utc")
    _require_string(site, "schedule_note", "site.schedule_note")

    app_url = _require_string(site, "app_url", "site.app_url")
    if app_url and not _HTTPS_URL_RE.match(app_url):
        raise ValueError("site.app_url must be empty or an https:// URL with no whitespace")
    return site


def _normalise_notifications(config: dict) -> dict:
    """Validate config['notifications'], injecting the shipped defaults for anything absent."""
    notifications = _require_section(config, "notifications", NOTIFICATION_DEFAULTS)
    for name, defaults in NOTIFICATION_DEFAULTS.items():
        if name not in notifications:
            notifications[name] = json.loads(json.dumps(defaults))
        if not isinstance(notifications[name], dict):
            raise ValueError(f"notifications.{name} must be an object")
        for key, default in defaults.items():
            notifications[name].setdefault(key, default)

    web_push = notifications["web_push"]
    enabled = _require_bool(web_push, "enabled", "notifications.web_push.enabled")
    subject = _require_string(web_push, "vapid_subject", "notifications.web_push.vapid_subject")
    if enabled and not _VAPID_SUBJECT_RE.match(subject):
        raise ValueError("notifications.web_push.vapid_subject must be a non-empty "
                         "mailto:<address> or a path-less https:// origin")
    ttl = web_push["ttl_seconds"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= _TTL_MAX:
        raise ValueError(f"notifications.web_push.ttl_seconds must be an integer between 1 "
                         f"and {_TTL_MAX}")
    _require_choice(web_push, "urgency", "notifications.web_push.urgency", _URGENCIES)

    ntfy = notifications["ntfy"]
    _require_bool(ntfy, "enabled", "notifications.ntfy.enabled")
    server = _require_string(ntfy, "server", "notifications.ntfy.server")
    if not _HTTPS_URL_RE.match(server):
        raise ValueError("notifications.ntfy.server must be an https:// URL with no whitespace")
    _require_choice(ntfy, "priority", "notifications.ntfy.priority", _PRIORITIES)
    tags = _require_string(ntfy, "tags", "notifications.ntfy.tags")
    if tags and not _NTFY_TAGS_RE.match(tags):
        raise ValueError("notifications.ntfy.tags must be comma-separated tokens of "
                         "letters, digits, underscores and hyphens")
    return notifications


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load and validate config.json. Raises ValueError naming the bad key."""
    with open(path, encoding="utf-8") as fh:
        config = json.load(fh)

    indices = config.get("indices")
    if not isinstance(indices, list) or not indices:
        raise ValueError("indices must be a non-empty list")
    for i, idx in enumerate(indices):
        if not isinstance(idx, dict):
            raise ValueError(f"indices[{i}] must be an object")
        for key in ("name", "ticker", "fund_held"):
            if not isinstance(idx.get(key), str) or not idx[key].strip():
                raise ValueError(f"indices[{i}].{key} must be a non-empty string")
        _validate_index_thresholds(idx, i)

    thresholds = config.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("thresholds must be an object")
    invest = _require_positive_number(thresholds, "ath_drawdown_invest_pct")
    strong = _require_positive_number(thresholds, "ath_drawdown_strong_invest_pct")
    _require_positive_number(thresholds, "high_52w_drawdown_pct")
    if strong <= invest:
        raise ValueError(
            "thresholds.ath_drawdown_strong_invest_pct must be greater than "
            "thresholds.ath_drawdown_invest_pct"
        )
    if not isinstance(thresholds.get("flag_below_sma200"), bool):
        raise ValueError("thresholds.flag_below_sma200 must be true or false")

    # The invariant has to hold on what each index will ACTUALLY be evaluated against: an override
    # of one of the two percentages can invert the pair even though both blocks are valid alone.
    for i, idx in enumerate(indices):
        effective = resolve_thresholds(thresholds, idx)
        if (float(effective["ath_drawdown_strong_invest_pct"])
                <= float(effective["ath_drawdown_invest_pct"])):
            raise ValueError(
                f"indices[{i}]: effective ath_drawdown_strong_invest_pct must be greater than "
                "ath_drawdown_invest_pct"
            )

    day = config.get("contribution_reminder_day")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
        raise ValueError("contribution_reminder_day must be an integer between 1 and 31")

    time_str = config.get("notification_time")
    if not isinstance(time_str, str) or not _TIME_RE.match(time_str):
        raise ValueError("notification_time must be a string in HH:MM format")

    app_name = config.get("app_name", "Finan")
    if not isinstance(app_name, str) or not app_name.strip():
        raise ValueError("app_name must be a non-empty string")
    config["app_name"] = app_name

    _normalise_site(config)
    _normalise_notifications(config)

    return config
