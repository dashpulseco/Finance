/* Finan PWA. One IIFE, three concerns: render, sparkline, notifications.
 *
 * Every number on the page is pre-computed by the daily job and published in data.json. This file
 * performs NO financial arithmetic - it formats, it negates the two drawdown magnitudes for
 * display, and it scales the sparkline to the viewBox. Nothing else.
 *
 * Every URL here is relative. The site is served from https://<user>.github.io/<repo>/, where a
 * leading "/" would 404.
 */
(function () {
  "use strict";

  var SCHEMA_VERSION = 2;          // 2: every index carries its own effective thresholds block
  var STALE_DAYS = 4;              // long weekend plus a holiday; beyond that something is wrong
  var VAPID_KEY_RE = /^B[A-Za-z0-9_-]{86}$/;

  function el(id) { return document.getElementById(id); }

  var dom = {
    asof: el("asof"),
    banner: el("banner"),
    overall: el("overall"),
    contribution: el("contribution"),
    cards: el("cards"),
    nextCheck: el("next-check"),
    contributionNote: el("contribution-note"),
    legendInvest: el("legend-invest"),
    legendStrong: el("legend-strong"),
    legend52w: el("legend-52w"),
    legendSma200: el("legend-sma200"),
    panel: el("notify-panel"),
    notifyState: el("notify-state"),
    notifyBtn: el("notify-btn"),
    subBox: el("sub-box"),
    subJson: el("sub-json"),
    copyBtn: el("copy-btn")
  };

  /* ------------------------------------------------------------------ helpers */

  var ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};

  // Card markup is built as a string, so every published string is escaped on the way in.
  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/[&<>"']/g, function (ch) { return ESCAPES[ch]; });
  }

  var eurFormat = new Intl.NumberFormat(undefined, {
    style: "currency", currency: "EUR", maximumFractionDigits: 2
  });

  function fmtEur(v) { return v === null || v === undefined ? "n/a" : eurFormat.format(v); }

  function fmtPct(v, signed) {
    if (v === null || v === undefined) { return "n/a"; }
    return (signed && v >= 0 ? "+" : "") + v.toFixed(1) + "%";
  }

  // Date-only strings get an explicit local midnight: new Date("2026-10-09") is parsed as UTC,
  // which renders as the previous day for anyone west of Greenwich.
  function fmtDate(iso) {
    if (!iso) { return "n/a"; }
    var d = new Date(String(iso).length === 10 ? iso + "T00:00:00" : iso);
    return isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString(undefined, {dateStyle: "medium"});
  }

  // schedule.next_check is an INSTANT, not a date: rendering it in the device's zone is what
  // keeps the summer/winter shift of the UTC cron invisible to the reader.
  function fmtDateTime(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime())
      ? String(iso)
      : d.toLocaleString(undefined, {dateStyle: "medium", timeStyle: "short"});
  }

  function daysBehind(isoDate) {
    var then = new Date(isoDate + "T00:00:00");
    if (isNaN(then.getTime())) { return 0; }
    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.floor((today.getTime() - then.getTime()) / 86400000);
  }

  /* ------------------------------------------------------------------ sparkline */

  function drawSparkline(points, thresholds) {
    if (!points || points.length < 2) { return ""; }
    var n = points.length;
    var values = points.map(function (pt) { return pt.drawdown_pct; });
    var deepest = Math.min.apply(null, values);
    // Keep the strong-signal guide line on screen even when the series never got near it.
    var floor = Math.min(deepest, -thresholds.strong_invest_pct) * 1.1;
    function y(dd) { return (dd / floor) * 32; }

    var coords = points.map(function (pt, i) {
      return (i / (n - 1) * 100).toFixed(2) + "," + y(pt.drawdown_pct).toFixed(2);
    }).join(" ");

    // preserveAspectRatio="none" stretches the box, so every stroke needs non-scaling-stroke or
    // it renders as a wedge.
    function guide(pct, cls) {
      var gy = y(-pct).toFixed(2);
      return '<line class="guide ' + cls + '" x1="0" y1="' + gy + '" x2="100" y2="' + gy +
             '" stroke-dasharray="3 3" vector-effect="non-scaling-stroke" />';
    }

    var label = "Drawdown from all-time high, weekly closes since " + points[0].date +
                ": now " + fmtPct(values[n - 1]) + ", deepest " + fmtPct(deepest);

    return '<svg class="spark" viewBox="0 0 100 32" preserveAspectRatio="none" role="img"' +
           ' aria-label="' + esc(label) + '">' +
           guide(thresholds.invest_pct, "guide--invest") +
           guide(thresholds.strong_invest_pct, "guide--strong") +
           '<polyline class="spark-line" points="' + coords +
           '" vector-effect="non-scaling-stroke" />' +
           '</svg>';
  }

  /* ------------------------------------------------------------------ render */

  var LEVEL_SENTENCE = {
    none: "No signal - stick to your monthly contribution.",
    invest: "At least one index has reached the INVEST threshold.",
    strong: "At least one index has reached the STRONG INVEST threshold.",
    nodata: "No prices could be read today."
  };

  function levelClass(level) {
    return LEVEL_SENTENCE[level] ? "level-" + level : "level-nodata";
  }

  function renderBanner(p) {
    var lines = [];
    if (p.stale && p.stale_reason) { lines.push(p.stale_reason); }
    var degraded = (p.indices || []).some(function (idx) { return idx.source !== "live"; });
    if (degraded) {
      lines.push("Some indices could not be refreshed; showing the last close that was available.");
    }
    if (p.data_as_of) {
      var age = daysBehind(p.data_as_of);
      if (age > STALE_DAYS) { lines.push("This data is " + age + " days old."); }
    }
    if (!lines.length) {
      dom.banner.hidden = true;
      dom.banner.innerHTML = "";
      return;
    }
    dom.banner.innerHTML = lines.map(function (line) {
      return "<p>" + esc(line) + "</p>";
    }).join("");
    dom.banner.hidden = false;
  }

  function renderOverall(p) {
    dom.overall.className = "card card--overall " + levelClass(p.overall_level);
    dom.overall.innerHTML =
      '<p class="headline">' + esc(p.headline) + "</p>" +
      '<p class="muted">' + esc(LEVEL_SENTENCE[p.overall_level] || LEVEL_SENTENCE.nodata) + "</p>";
  }

  // An index may be evaluated against its own thresholds (a semiconductor fund routinely falls
  // further than a world index), so the comparison is against the two values the card draws.
  function ownThresholds(thresholds, globalThresholds) {
    if (!thresholds || !globalThresholds) { return false; }
    return thresholds.invest_pct !== globalThresholds.invest_pct ||
           thresholds.strong_invest_pct !== globalThresholds.strong_invest_pct;
  }

  function renderIndexCard(idx, thresholds, globalThresholds) {
    var head = '<p class="fund">' + esc(idx.fund_held) + "</p>" +
               '<h2 class="label">' + esc(idx.name) + " \u00b7 " + esc(idx.ticker) + "</h2>";

    // Same test the notification uses: the card variant keys on published fields, never on the
    // verdict text. below_ath_pct and below_52w_pct are non-null whenever metrics exist, which is
    // what lets the numbers below be negated unguarded.
    if (idx.level === "nodata" || idx.below_ath_pct === null || idx.below_ath_pct === undefined) {
      return '<section class="card ' + levelClass(idx.level) + '">' + head +
             '<p class="pill">No data</p>' +
             '<p class="note">' + esc(idx.note || "No prices were available for this index.") +
             "</p></section>";
    }

    var spark = drawSparkline(idx.drawdown_history, thresholds);
    var ownNote = ownThresholds(thresholds, globalThresholds)
      ? "This card uses its own thresholds: " + fmtPct(-thresholds.invest_pct) + " / " +
        fmtPct(-thresholds.strong_invest_pct) + ". "
      : "";
    var rows =
      '<div class="rows">' +
      cell("Close", fmtEur(idx.last_close) + " (" + fmtDate(idx.last_date) + ")", true) +
      cell("52w high", fmtPct(-idx.below_52w_pct)) +
      cell("200-day SMA", fmtPct(idx.vs_sma200_pct, true)) +
      cell("1 month", fmtPct(idx.change_1m_pct, true)) +
      cell("YTD", fmtPct(idx.ytd_pct, true)) +
      "</div>";

    return '<section class="card ' + levelClass(idx.level) + '">' + head +
           // Published as a positive magnitude, displayed as the drawdown it is.
           '<p class="metric">' + fmtPct(-idx.below_ath_pct) + "</p>" +
           '<p class="metric-note">below all-time high (' + esc(fmtEur(idx.ath)) + " on " +
           esc(fmtDate(idx.ath_date)) + ")</p>" +
           '<p class="pill">' + esc(idx.verdict) + "</p>" +
           spark +
           rows +
           (idx.note ? '<p class="note">' + esc(idx.note) + "</p>" : "") +
           '<p class="footnote">' +
           (spark ? "" : "No price history available for a sparkline. ") +
           esc(ownNote) +
           "History since " + esc(fmtDate(idx.history_start)) + ".</p>" +
           "</section>";
  }

  function cell(key, value, wide) {
    return '<div class="cell' + (wide ? " wide" : "") + '"><span class="k">' + esc(key) +
           '</span><span class="v">' + esc(value) + "</span></div>";
  }

  function renderLegend(t) {
    dom.legendInvest.textContent = fmtPct(t.invest_pct) + " or more";
    dom.legendStrong.textContent = fmtPct(t.strong_invest_pct) + " or more";
    dom.legend52w.textContent = fmtPct(t.high_52w_pct) + " or more";
    dom.legendSma200.textContent = t.flag_below_sma200
      ? "A close below that average is listed as a supporting reason."
      : "It is shown for context only and never adds a supporting reason.";
  }

  function render(p) {
    dom.asof.textContent = p.data_as_of
      ? "Data as of " + fmtDate(p.data_as_of)
      : "No price data yet";
    renderBanner(p);
    renderOverall(p);

    dom.contribution.hidden = !p.contribution_due;
    dom.contribution.textContent = "Monthly contribution day";

    dom.cards.innerHTML = (p.indices || []).map(function (idx) {
      // Each card renders from its OWN published block; the global one is passed only so the
      // card can say when the two differ. No resolution happens here - see plan D3.
      return renderIndexCard(idx, idx.thresholds, p.thresholds);
    }).join("");

    dom.nextCheck.textContent = p.schedule.next_check
      ? "Next check: " + fmtDateTime(p.schedule.next_check)
      : p.schedule.note;
    dom.contributionNote.textContent = "Contribution reminder: day " + p.contribution_day +
      " of each month (moved to the nearest weekday).";
    renderLegend(p.thresholds);
  }

  function renderError(detail) {
    dom.asof.textContent = "";
    dom.banner.hidden = true;
    dom.contribution.hidden = true;
    dom.cards.innerHTML = "";
    dom.nextCheck.textContent = "";
    dom.contributionNote.textContent = "";
    dom.overall.className = "card card--overall level-nodata";
    dom.overall.innerHTML =
      '<p class="headline">Could not read today\'s data</p>' +
      '<p class="muted">Pull to refresh, or check back after the next scheduled check.</p>' +
      '<p class="note">' + esc(detail) + "</p>";
  }

  function loadData() {
    return fetch("./data.json", {cache: "no-store"}).then(function (res) {
      if (!res.ok) { throw new Error("data.json returned HTTP " + res.status); }
      return res.json();
    }).then(function (payload) {
      if (!payload || payload.schema_version !== SCHEMA_VERSION) {
        throw new Error("data.json has schema_version " +
                        (payload ? payload.schema_version : "none") +
                        ", this app reads " + SCHEMA_VERSION);
      }
      render(payload);
    }).catch(function (err) {
      renderError(err && err.message ? err.message : String(err));
    });
  }

  /* ------------------------------------------------------------------ service worker */

  var swReady = null;

  function registerWorker() {
    if (!("serviceWorker" in navigator)) { return; }
    swReady = navigator.serviceWorker.register("./sw.js").then(function () {
      return navigator.serviceWorker.ready;
    });
    swReady.catch(function (err) {
      // Not fatal for rendering: the page works online without a worker. The notification panel
      // reports the failure itself, through its own error state.
      console.warn("Finan: service worker registration failed:", err && err.message);
    });
  }

  /* ------------------------------------------------------------------ notifications */

  var state = "unsupported";
  var vapidKey = "";
  var subscriptionJson = "";

  function setState(name, text, enabled, buttonLabel) {
    state = name;
    dom.panel.dataset.state = name;
    dom.notifyState.textContent = text;          // the aria-live region announces every change
    dom.notifyBtn.disabled = !enabled;
    if (buttonLabel) { dom.notifyBtn.textContent = buttonLabel; }
  }

  function isStandalone() {
    return (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
           navigator.standalone === true;
  }

  // Used only to decide what to EXPLAIN; it never blocks a browser that actually works.
  function isIOSLike() {
    var ua = navigator.userAgent || "";
    return /iPad|iPhone|iPod/.test(ua) ||
           (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  }

  function supportsPush() {
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  }

  // The committed placeholder holds "", so anything that is not an 87-character base64url key
  // starting with B means "not set up yet" - including a missing or unparseable file.
  function loadVapidKey() {
    return fetch("./push-config.json", {cache: "no-store"}).then(function (res) {
      if (!res.ok) { return ""; }
      return res.json().then(function (cfg) {
        var key = cfg && cfg.vapid_public_key;
        return typeof key === "string" && VAPID_KEY_RE.test(key) ? key : "";
      });
    }).catch(function () { return ""; });
  }

  function urlB64ToUint8Array(base64) {
    var padding = "=".repeat((4 - base64.length % 4) % 4);
    var normalised = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
    var raw = atob(normalised);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) { out[i] = raw.charCodeAt(i); }
    return out;
  }

  function showSubscription(sub, reveal) {
    // Already the one-element array the PUSH_SUBSCRIPTIONS secret expects, so the shape cannot be
    // pasted wrong. This JSON is a capability: it is rendered here and nowhere else - never
    // logged, never sent anywhere.
    subscriptionJson = JSON.stringify([sub.toJSON()], null, 2);
    dom.subJson.value = subscriptionJson;
    dom.subBox.hidden = !reveal;
    setState("subscribed",
             reveal
               ? "Notifications are on. Paste the JSON below into the PUSH_SUBSCRIPTIONS " +
                 "repository secret."
               : "Notifications are on.",
             true, "Show subscription again");
  }

  function showError(err) {
    var name = err && err.name ? err.name : "Error";
    var message = err && err.message ? err.message : String(err);
    var hint = "";
    if (name === "NotAllowedError") {
      hint = " The permission prompt was dismissed. Tap the button again and choose Allow.";
    } else if (name === "AbortError") {
      hint = " iOS could not reach the push service. Try again on Wi-Fi.";
    }
    setState("error", name + ": " + message + hint, true, "Try again");
  }

  function subscribe() {
    setState("ready", "Asking for permission\u2026", false);
    return Notification.requestPermission().then(function (permission) {
      if (permission === "denied") {
        setState("denied",
                 "iOS has blocked notifications for Finan. Open Settings -> Notifications -> " +
                 "Finan to allow them.", false);
        return null;
      }
      if (permission !== "granted") {
        setState("ready", "Permission was not granted. Tap the button again to retry.", true,
                 "Enable iPhone notifications");
        return null;
      }
      return navigator.serviceWorker.ready.then(function (reg) {
        return reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlB64ToUint8Array(vapidKey)
        });
      }).then(function (sub) {
        showSubscription(sub, true);
        return sub;
      });
    }).catch(showError);
  }

  function onNotifyClick() {
    if (state === "ready" || state === "error") {
      subscribe();
    } else if (state === "subscribed") {
      dom.subBox.hidden = false;
      dom.notifyState.textContent =
        "Notifications are on. Paste the JSON below into the PUSH_SUBSCRIPTIONS repository secret.";
    }
  }

  function copySubscription() {
    var text = dom.subJson.value;
    var done = function (ok) {
      dom.notifyState.textContent = ok
        ? "Subscription JSON copied to the clipboard."
        : "Could not copy automatically - select the text above and copy it by hand.";
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, fallbackCopy);
      return;
    }
    fallbackCopy();

    function fallbackCopy() {
      dom.subJson.focus();
      dom.subJson.select();
      var ok = false;
      try { ok = document.execCommand && document.execCommand("copy"); } catch (err) { ok = false; }
      done(!!ok);
    }
  }

  function initNotify() {
    dom.notifyBtn.addEventListener("click", onNotifyClick);
    dom.copyBtn.addEventListener("click", copySubscription);

    if (!supportsPush()) {
      setState("unsupported",
               "This browser cannot do web push. Use Safari on iOS 16.4+ or a desktop " +
               "Chrome/Edge.", false);
      return;
    }
    if (isIOSLike() && !isStandalone()) {
      setState("not-installed",
               "On iPhone, notifications only work after you add Finan to your home screen: tap " +
               "the Share button (the square with an arrow), then Add to Home Screen, then open " +
               "Finan from the new icon.", false);
      return;
    }

    setState("ready", "Checking notification setup\u2026", false);
    loadVapidKey().then(function (key) {
      vapidKey = key;
      if (!vapidKey) {
        setState("keys-missing",
                 "Notification keys are not set up yet. Run scripts/gen_vapid_keys.py, then " +
                 "deploy.ps1 and a workflow run to publish them.", false);
        return null;
      }
      if (Notification.permission === "denied") {
        setState("denied",
                 "iOS has blocked notifications for Finan. Open Settings -> Notifications -> " +
                 "Finan to allow them.", false);
        return null;
      }
      return swReady.then(function (reg) {
        return reg.pushManager.getSubscription();
      }).then(function (existing) {
        if (existing) {
          showSubscription(existing, false);
        } else {
          setState("ready", "Ready. Tap the button to turn on notifications for this iPhone.",
                   true, "Enable iPhone notifications");
        }
        return existing;
      });
    }).catch(showError);
  }

  /* ------------------------------------------------------------------ start */

  registerWorker();
  initNotify();          // runs even when data.json fails, so the panel is always honest
  loadData();
})();
