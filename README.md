# Finan Index Tracker

A small Windows utility that checks, once a day, whether the three index funds
held on MyInvestor are trading well below their all-time high and, if so,
sends a desktop toast saying it may be a good time to add to the monthly
contribution.

## What it does

Every run downloads the latest daily closes (via yfinance) for three EUR-listed
ETFs on Xetra, which stand in as proxies for the funds actually held:

| Fund held (MyInvestor)                                            | Proxy ETF |
|-------------------------------------------------------------------|-----------|
| Fidelity MSCI World Index Fund P-ACC-EUR (IE00BYX5NX33)           | EUNL.DE   |
| Vanguard Global Small-Cap Index Fund EUR Acc (IE00B42W3S00)       | IUSN.DE   |
| Vanguard Emerging Markets Stock Index Fund EUR Acc (IE0031786696) | IS3N.DE   |

The ETFs track the same MSCI indices as the funds, but they are stand-ins:
the prices used are ETF closes, not the funds' NAV. Small differences in
fees, replication and pricing time are expected.

Each run:

- updates the per-ticker CSV cache in `data\`, re-downloading only the missing tail
  (if the download fails, the cached data is used and the headline says `(cached data)`);
- computes all-time high, 52-week high, 200-day SMA and the drawdowns from them,
  plus 1-day / 1-month / YTD changes;
- evaluates the signals below and writes `report.html`;
- shows one Windows toast whose title is the headline and whose body has one line
  per index; clicking it opens `report.html`;
- appends one `RUN` line to `logs\tracker.log`.

## Signals

| Verdict         | Condition                              |
|-----------------|----------------------------------------|
| `STRONG INVEST` | close is 20% or more below its all-time high |
| `INVEST`        | close is 10% or more below its all-time high |
| `No signal`     | otherwise                              |
| `No data`       | no download and no cache for the ticker |

Supporting factors (shown next to the verdict, they do not change it):

- 10% or more below the 52-week high;
- close below the 200-day simple moving average.

Headline wording: `Finan: STRONG INVEST signal on N of 3 indices`,
`Finan: INVEST signal on N of 3 indices`, `Finan: no signal today` or
`Finan: no data today`, with ` (cached data)` / ` (partial data)` appended when
a ticker could not be refreshed. On the configured contribution day the toast
and report also show the line `Monthly contribution day`.

## Two ways to run it

The same rules can run in two places, and they can both be on at once.

| | Local Windows mode | Cloud mode |
|---|---|---|
| What runs it | `tracker.py`, by hand or via Task Scheduler | GitHub Actions, on a weekday cron |
| Where it notifies | a Windows toast on this PC | a push notification on your iPhone |
| What it produces | `report.html` with charts | `site\data.json`, shown by the installable web app |
| Needs the PC on | yes, at the scheduled time | no |
| Needs the internet | only to refresh prices (falls back to the cache) | yes |
| Needs any setup beyond Install | `register_task.ps1` | a public GitHub repository, Pages and two secrets |

Cloud mode is the iPhone route: GitHub runs the check after the European close, commits the
day's numbers, publishes them as a small GitHub Pages site you add to your home screen, and
sends the notification from the same run. The step-by-step setup is in
[README_IPHONE.md](README_IPHONE.md).

### Privacy

Cloud mode needs a public repository (GitHub Pages is free only there), so it is worth being
precise about what that publishes.

The site publishes the names and ISINs of the three funds you track, because every card
names the fund it stands for. Nothing else about your portfolio is published - no amounts,
no units, no account, no broker login, nothing identifying you. If the fund names are more
than you want public, replace each `fund_held` in `config.json` with a generic label such as
`"World fund"`; everything keeps working. `config.json` itself is committed to the same
public repository, so its thresholds, contribution reminder day, notification time and
`vapid_subject` are public too.

What reaches `site\data.json` is built from a fixed allow-list of fields and checked on every
write, so the published payload stays minimal and easy to review rather than secret. The
things that must stay private - the push private key, your phone's subscription and the
optional ntfy topic - are never committed: they are GitHub Actions secrets, and `secrets\`,
`data\`, `logs\` and `report.html` are gitignored.

The repository is public, so the committed files use relative paths - nothing published names
your PC or your user account.

## Editing config.json

| Key                                        | Meaning                                                  | Default |
|--------------------------------------------|----------------------------------------------------------|---------|
| `thresholds.ath_drawdown_invest_pct`       | % below all-time high for `INVEST`                       | `10.0`  |
| `thresholds.ath_drawdown_strong_invest_pct`| % below all-time high for `STRONG INVEST` (must be larger)| `20.0`  |
| `thresholds.high_52w_drawdown_pct`         | % below 52-week high flagged as a supporting factor      | `10.0`  |
| `thresholds.flag_below_sma200`             | flag close below the 200-day SMA                         | `true`  |
| `indices[].thresholds`                     | optional per-fund overrides of any of the four keys above, for that fund only | none (the global values) |
| `contribution_reminder_day`                | day of month (1-31) for the `Monthly contribution day` line | `1`  |
| `notification_time`                        | daily run time, `HH:MM` local                            | `09:00` |
| `app_name`                                 | name shown on the toast and in the headline              | `Finan` |

Cloud-mode keys (ignored by local Windows mode; all optional - the shipped defaults are
injected when a section is absent):

| Key                                     | Meaning                                                              | Default |
|-----------------------------------------|----------------------------------------------------------------------|---------|
| `site.schedule_cron_utc`                | the cron the app's footer turns into "next check", in UTC            | `30 18 * * 1-5` |
| `site.schedule_note`                    | the free-text sentence under it                                      | `Weekdays after the European close: 20:30 Madrid in summer, 19:30 in winter` |
| `site.app_url`                          | `https://...` address a notification tap opens; empty = the site root | `""` |
| `notifications.web_push.enabled`        | send the iPhone push notification                                    | `true` |
| `notifications.web_push.vapid_subject`  | contact identifying the sender, sent with every push. A `mailto:` address or a **path-less** `https://` origin such as `https://dashpulseco.github.io` - a repository URL with a path on it is rejected | `https://example.github.io` |
| `notifications.web_push.ttl_seconds`    | how long Apple may hold an undelivered push (`1`-`2419200`)          | `86400` |
| `notifications.web_push.urgency`        | `very-low` / `low` / `normal` / `high`                               | `normal` |
| `notifications.ntfy.enabled`            | also send through ntfy (see README_IPHONE.md)                         | `false` |
| `notifications.ntfy.server`             | ntfy server, `https://...`                                           | `https://ntfy.sh` |
| `notifications.ntfy.priority`           | `min` / `low` / `default` / `high` / `max` (or `1`-`5`)               | `default` |
| `notifications.ntfy.tags`               | comma-separated ntfy tags; empty for none                            | `chart_with_downwards_trend` |

`site.schedule_cron_utc` and `site.schedule_note` do not schedule anything: the workflow cron
in `.github/workflows/daily.yml` is what fires; these two values are what the app's footer
says, so change all three together and re-run `deploy.ps1`. The cloud job logs a warning when
the cron and the config copy disagree.

After changing `notification_time`, re-run `register_task.ps1` (see Scheduling)
so the scheduled task picks up the new time. Threshold changes take effect on
the next run.

To swap a ticker, edit `ticker` in the `indices` list and run once with
`--force-refresh`. Use a EUR listing (Xetra `.DE` or Amsterdam `.AS`). London
`.L` listings are quoted in GBX (pence), so their levels and drawdowns are not
comparable with a EUR fund. `ticker_note` is free text for your own notes.

### Thresholds for one fund only

A fund can carry its own `thresholds` object inside its `indices` entry, overriding the
global block key by key for that fund alone. It is there for a fund whose normal behaviour
is not a broad index's: a semiconductor ETF routinely falls 15-20% and recovers, so a 10%
trigger on it would fire often enough to stop meaning anything. Give that fund 18% / 30% of
its own and leave the three broad funds on the shared 10% / 20%.

```json
"indices": [
  {
    "name": "Semiconductors",
    "ticker": "VVSM.DE",
    "fund_held": "Watchlist (not held)",
    "thresholds": { "ath_drawdown_invest_pct": 18.0, "ath_drawdown_strong_invest_pct": 30.0 }
  }
]
```

Any subset of the four keys is allowed; whatever is absent falls back to the global value,
and the resolved pair must still have `strong` deeper than `invest`. The report's charts and
the app's card sparklines draw each fund's own guide lines, and a card whose values differ
says so underneath.

One thing stays global on purpose: the notification's first sentence quotes the shared
thresholds ("20% below the all-time high" and so on), because it is one line summarising all
the funds at once. The per-fund lines below it carry each fund's real drawdown and its own
verdict, which is where an overridden threshold shows up.

## Install

Requires Python 3 reachable as `py -3`. Run the commands in this README from the Finan
folder (open PowerShell there, or `cd` to it first).

```powershell
py -3 -m pip install -r requirements-windows.txt
```

That is the file to use on this PC: it pulls in everything the cloud job needs plus the
Windows-only extras - `winotify` for the toast, `matplotlib` for `report.html`'s charts and
`Pillow` for the one-off icon generator.

`requirements.txt` underneath it is what the cloud job installs on Linux. It deliberately
carries no `winotify` and no `matplotlib`, so installing only that file on Windows would
leave you without the toast backend and without the report charts.

```powershell
py -3 -m pip install -r requirements.txt    # what GitHub Actions installs; not enough on its own here
```

## Run manually

```powershell
py -3 .\tracker.py
py -3 .\tracker.py --no-toast
py -3 .\tracker.py --force-refresh
```

- `--no-toast`: write the report and log but skip the Windows toast.
- `--force-refresh`: re-download the full price history instead of the incremental tail.

The console prints the headline, one `<index>: <verdict>` line per index and the
report path. Before 18:00 local the current day's intraday price is ignored so
only completed sessions are evaluated.

## Adding or removing a tracked fund

Two small tools do it safely, so you never hand-edit the `indices` list.

```powershell
py -3 .\scripts\fund_search.py search "VanEck Semiconductor"
py -3 .\scripts\fund_search.py search "MSCI World" --limit 15
py -3 .\scripts\fund_search.py validate VVSM.DE
py -3 .\scripts\manage_funds.py add --ticker VVSM.DE --display-name "Semiconductors" --invest-pct 18 --strong-invest-pct 30 --dry-run
py -3 .\scripts\manage_funds.py add --ticker VVSM.DE --display-name "Semiconductors" --fund-held "Watchlist (not held)"
py -3 .\scripts\manage_funds.py rename --ticker VVSM.DE --display-name "Semis"
py -3 .\scripts\manage_funds.py remove --ticker VVSM.DE
```

`search` lists every listing of that fund with its exchange, currency and how much price
history exists, EUR lines first, and marks one row as recommended. `validate` is the gate
`add` runs for you: it refuses a ticker that is already tracked, one with less than a year
of history, and anything not quoted in EUR. A London `.L` listing is refused by name,
because it is quoted in pence - its closes, all-time high and every drawdown from them
would be wrong - and the refusal points you at the Xetra `.DE` or Amsterdam `.AS` line
instead.

`add` fills in `ticker_note` from the listing it verified, defaults `fund_held` to
`Watchlist (not held)` and turns `--invest-pct` / `--strong-invest-pct` into that fund's own
`thresholds` block. `remove` refuses to delete the last remaining fund and leaves the cached
`data\<TICKER>.csv` alone. `rename` touches only the field you pass. All three accept
`--dry-run`, which prints the diff and writes nothing, and `--config PATH` to work on a copy.

Both tools write `config.json` atomically and re-validate the file before it replaces the
original, so a rejected change leaves the original untouched. The first write reformats the
three hand-written compact `indices` entries into standard 2-space JSON blocks - same
values, same order, just the indentation Python's JSON writer produces.

After adding a fund, run `py -3 .\tracker.py` once to download its history and see it in the
report. The same thing can be done from the phone instead, without the PC: the
**Add or remove a fund** workflow in the Actions tab wraps these two tools, and it needs the
one-time **Settings > Actions > General > Workflow permissions > Read and write permissions**
setting to commit. See [README_IPHONE.md](README_IPHONE.md).

## Outputs

- `report.html` - self-contained report (metrics table, verdicts, one 3-year
  drawdown chart per index). Open it in any browser; regenerated on every run.
- `data\EUNL.DE.csv`, `data\IUSN.DE.csv`, `data\IS3N.DE.csv` - price cache (`date,close`).
- `logs\tracker.log` - one `RUN` line per run with closes, drawdowns, verdicts,
  headline and the toast backend used.

## Scheduling

`register_task.ps1` creates the Task Scheduler task `Finan Index Tracker`,
which runs `py -3 ...\tracker.py` daily at `notification_time` as the current
user (no admin needed; the toast needs your interactive session). If the PC was
off or asleep at that time, the task runs as soon as it is available again.

```powershell
powershell -ExecutionPolicy Bypass -File .\register_task.ps1
schtasks /Query /TN "Finan Index Tracker"
```

The task log is written to `logs\tracker.log`; the task's own last result can
be seen with `schtasks /Query /TN "Finan Index Tracker" /V /FO LIST`.

## Removing

```powershell
powershell -ExecutionPolicy Bypass -File .\unregister_task.ps1
```

Then delete the folder if you no longer want the data cache and logs.

## Disclaimer

This is a rule-based reminder built on simple drawdown thresholds. It is not
investment advice, it does not predict markets, and it uses ETF prices as a
proxy for the funds held. Decide for yourself before investing.
