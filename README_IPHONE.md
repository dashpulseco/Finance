# Finan on your iPhone

This turns Finan into an app icon on your iPhone home screen that shows the same three
fund cards you see in `report.html`, plus a notification every weekday evening - with your
PC switched off. The daily check runs on GitHub's computers (GitHub Actions), writes the
day's numbers into the repository, publishes them as a small website (GitHub Pages) and
sends your phone a push.

Run every PowerShell command below from the Finan folder (open PowerShell there, or `cd`
to it first). That is why every command starts with `.\`.

Do the ten steps in order. Nothing is published until step 6.

---

## 1. Create an empty public repository on github.com

Open <https://github.com/new>, choose **Public**, and create it without a README, without a
`.gitignore` and without a licence. There is no `gh` command-line tool here, so this one
step is done by hand in the browser.

This guide is written for the repository actually in use, **`dashpulseco/Finance`**. If you
ever point it at a different one, every `dashpulseco` and `Finance` below changes with it -
and note that the published address is case-sensitive, so a repository named `Finance`
publishes at `/Finance/` and never at `/finance/`.

**What becomes public, in plain words.** The site publishes the names and ISINs of the
three funds you track, because every card names the fund it stands for. Nothing else about
your portfolio is published: no amounts, no units, no account, no broker login, nothing
that identifies you. If even the fund names feel like too much, open `config.json`
and replace each `fund_held` with a generic label such as `"World fund"` - the app and the
notifications keep working.

Note too that `config.json` itself is committed to the same public repository, so its
thresholds, the contribution reminder day, the notification time and `vapid_subject` are
public as well. That is deliberate: the published payload is built from a small, fixed
allow-list of fields, so what reaches the web stays minimal and easy to review rather than
secret. The private things - the push key, your phone's subscription and the optional ntfy
topic - are never committed; they live only in GitHub's encrypted secrets.

A **private** repository works too, but GitHub Pages on a private repository needs a paid
plan, so the free route is a public repository.

## 2. Generate your notification keys

```powershell
py -3 .\scripts\gen_vapid_keys.py
```

This prints two keys and writes the public one into `site\push-config.json`. The private
key is written to `secrets\vapid_private_key.txt`, which is never committed. Leave the
window open - you need the private key in the next step.

## 3. Add the `VAPID_PRIVATE_KEY` secret

On github.com open your repository, then **Settings > Secrets and variables > Actions >
New repository secret**:

- name `VAPID_PRIVATE_KEY`, value: the private key the previous step printed;
- add a second secret named `PUSH_SUBSCRIPTIONS` with the value `[]` (two brackets) for
  now. Your phone fills it in at step 10.

## 4. Push the files with `deploy.ps1`

First open `config.json` and set `notifications.web_push.vapid_subject` to your GitHub
Pages **origin** - the bare domain, `https://dashpulseco.github.io`, with nothing after it.
This is a contact address identifying whoever sends the notifications; nothing is ever
delivered to it. A full repository URL such as `https://github.com/dashpulseco/Finance` is
**rejected**, because it carries a path: the setting takes a `mailto:` address or a
path-less `https://` origin and nothing else.

Optionally also set `site.app_url` to the full published address,
`https://dashpulseco.github.io/Finance/`. That one does take a path, and it is what makes a
tapped notification open straight into the app; it is also required if you switch on the
ntfy fallback below. Leaving it empty is supported.

Then:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -RemoteUrl https://github.com/dashpulseco/Finance
```

The script checks a few things, shows you exactly which files it will send, asks for
confirmation and then pushes. **This pushes code only and publishes nothing on the web** -
the next two steps do that.

## 5. Turn on Pages, with Source: GitHub Actions

In your repository: **Settings > Pages**, and under "Build and deployment" set
**Source: GitHub Actions**. Do not pick "Deploy from a branch" - that serves the wrong
folder and would also expose the Python files at the site root.

## 6. Actions > Run workflow

Open the **Actions** tab, click **Finan daily check** on the left, then the
**Run workflow** button. **This run is what publishes the site** - the public key from
step 2 and the first day of data only reach the public URL through a workflow run. Wait
for the green tick (about a minute).

From now on it repeats itself every weekday at 18:30 UTC (20:30 in Madrid in summer,
19:30 in winter).

## 7. Open the site in Safari on the iPhone

The address is:

```
https://dashpulseco.github.io/Finance/
```

Open it **in Safari on the iPhone**, not on your PC. You should see the three cards. Mind
the capital `F`: the path is case-sensitive and `/finance/` returns a 404.

## 8. Share > Add to Home Screen

In Safari tap the **Share** button, scroll to **Add to Home Screen**, then **Add**. A
Finan icon appears on your home screen.

This step is not decoration: on iOS, **notifications do not work from a Safari tab**. Only
the app you opened from the home-screen icon is allowed to ask for them.

## 9. Open the installed app and enable notifications

Open Finan from the **home-screen icon**. Scroll to the notifications panel and tap
**Enable iPhone notifications**. iOS shows its own permission prompt - tap **Allow**.

The app then shows a block of text called your subscription. Tap **Copy**. (If you lose it,
tap "Show subscription again".)

## 10. Paste the subscription and confirm

Back on github.com: **Settings > Secrets and variables > Actions**, open
`PUSH_SUBSCRIPTIONS` and replace the `[]` with what you copied. Save.

Then go to **Actions > Run workflow** once more. Within a minute your phone should buzz
with the day's headline. That is the whole setup.

Easiest way to get the text from the phone to the PC: AirDrop it, email it to yourself, or
paste it into the secret directly from the phone's browser.

---

## Adding a fund later

You can add or remove a tracked fund entirely from the phone, with no PC involved.

**One-time setting first.** On github.com open your repository, then **Settings > Actions >
General**, scroll to **Workflow permissions**, choose **Read and write permissions** and
save. Without it the workflow cannot commit your new fund and the run fails on the commit
step.

Then, every time you want to add something:

1. Open the **Actions** tab and tap **Add or remove a fund** on the left.
2. Tap **Run workflow**. Leave `action` on **search**, type the fund name in `query` (for
   example `VanEck Semiconductor`) and tap the green **Run workflow** button.
3. Open the finished run and read its **summary**: a numbered table of the listings found,
   one per row, with the ticker, the exchange, the currency, how much price history exists
   and the name. One row is marked **recommended**.
4. Tap **Run workflow** again, this time with `action` = **add** and `ticker` = the ticker
   from the row you chose (for example `VVSM.DE`). Leave the rest blank unless you want to
   set them.

**Only EUR listings are accepted.** A London `.L` line of the same ETF is quoted in pence,
so its closes, its all-time high and every drawdown computed from them would be wrong by a
factor of about a hundred. The check refuses such a ticker and tells you to search for the
Xetra `.DE` or Amsterdam `.AS` line instead. Non-EUR rows still appear in the search table,
clearly marked, so you can see why the recommended row is the recommended one.

The optional boxes on **add**:

- `display_name` - the title on the card. Blank uses the official name, tidied up.
- `fund_held` - the fund you actually buy at MyInvestor. Blank records
  `Watchlist (not held)`.
- `invest_pct` and `strong_invest_pct` - this one fund's own signal thresholds, in percent
  below its all-time high. Blank keeps the shared 10% / 20%. Use them for a fund that swings
  more than a broad index (see the per-index thresholds note in [README.md](README.md)).

**What happens after it commits.** The run writes your new fund into `config.json` and then
asks **Finan daily check** to run, so the app picks the fund up within a minute or two. If
the summary instead says the refresh could not be started from there, tap **Finan daily
check > Run workflow** yourself - that is the only difference.

**To remove one**, run it with `action` = **remove** and the ticker. The last remaining fund
cannot be removed, and the cached price file is left alone in case you add it back.

Nothing is committed when a check refuses: the run fails with the plain-language reason in
its summary and `config.json` is untouched.

---

## Optional shortcut: ntfy instead of (or alongside) push

If the iPhone push route gives you trouble, [ntfy](https://ntfy.sh) is a simpler path: a
free app that subscribes to a topic name.

1. Install **ntfy** from the App Store.
2. Invent a long random topic name - at least 16 characters of letters, digits, `-` and
   `_`, for example `finan-7fQ2m8xK4pLw`.
3. In the app, subscribe to that topic.
4. In `config.json` set `notifications.ntfy.enabled` to `true` (and make sure
   `site.app_url` is filled in, or the notification cannot be tapped through to the app),
   then push again:
   `powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -RemoteUrl https://github.com/dashpulseco/Finance -Force`
5. Add a repository secret `NTFY_TOPIC` with the topic name.

**The topic name is the only secret.** Anyone who learns it can read your notifications (and
send you some), so make it long and random, and never put it in the README, in config.json
or in an issue. Both transports can be on at once, in which case you get two notifications.

---

## When something does not work

- **The Enable button is greyed out and the panel says notification keys are not set up.**
  You generated keys but have not deployed since: re-run `deploy.ps1` if you have not
  pushed, then **Actions > Run workflow**. That run is what puts the key on the site.
- **The Enable button is missing entirely and the panel talks about the home screen.** You
  are in a Safari tab. Go back to step 8 and open the app from the home-screen icon.
- **No notification arrives.** Open the latest run in the Actions tab and read the last
  step. `push=0/1` means the send failed; `push=1/1` means it was delivered and the problem
  is on the phone (check Settings > Notifications > Finan).
- **The log says `410` or `push=expired(1)`.** Your subscription was replaced by iOS. Open
  Finan, tap "Show subscription again", copy it and paste it into `PUSH_SUBSCRIPTIONS`
  again.
- **The log says `push=skipped(no-key)`, `skipped(no-subs)` or `ntfy=skipped(no-topic)`.**
  The named secret is missing or empty. Re-check step 3 (or step 5 of the ntfy section).
- **It stopped running after a few weeks.** GitHub pauses scheduled workflows on
  repositories with no activity for 60 days. Open the Actions tab and click
  **Enable workflow**.
- **The app shows old numbers.** Pull down to refresh. If it is still old, check the latest
  Actions run went green and that Pages still says Source: GitHub Actions.
- **The app shows "Could not read today's data".** The site is up but `data.json` could not
  be read - usually a workflow run that failed before publishing. Read that run's log.
- **Adding a fund failed on the "Commit config.json" step.** The repository is not allowed
  to write to itself: **Settings > Actions > General > Workflow permissions > Read and
  write permissions**, then run it again.
- **The first run after an update fails with "no data" instead of showing yesterday's
  numbers.** The published payload changed shape (its `schema_version` went from 1 to 2),
  so there is no old file left that the job could honestly republish. If every price
  download happened to fail on that one run, it stops rather than showing stale numbers -
  open **Actions > Finan daily check > Run workflow** and it recovers.
- **You changed the run time.** The cron line in `.github\workflows\daily.yml` is what
  actually fires. `site.schedule_cron_utc` and `site.schedule_note` in `config.json` are
  what the app's footer says. Change all three together and re-run `deploy.ps1`.
