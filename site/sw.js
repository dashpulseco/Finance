/* Finan service worker.
 *
 * Every URL here is relative: the scope is whatever directory the worker is served from, which is
 * correct both at a domain root and under /<repo>/ on GitHub Pages.
 */

const SHELL_VERSION = "v2";                   // bump when any shell file changes
const SHELL_CACHE = `finan-shell-${SHELL_VERSION}`;
/* Deliberately UNVERSIONED. Keying the data cache on SHELL_VERSION would make a version bump
 * rename it, and the activate-time purge would then delete the last data.json the phone managed
 * to fetch - worst for the user who updates the app while offline. There is no migration problem
 * to solve: this cache holds two network-first JSON files whose schema is guarded by
 * schema_version, and a stale entry is replaced on the next successful fetch. */
const DATA_CACHE = "finan-data";
const SHELL = ["./", "./index.html", "./app.css", "./app.js", "./manifest.webmanifest",
               "./icons/icon-192.png", "./icons/icon-512.png",
               "./icons/icon-512-maskable.png", "./icons/apple-touch-icon.png"];
/* push-config.json is network-first and absent from SHELL on purpose: it ships as the empty
 * placeholder, so a user who opened the site during setup would otherwise be pinned to the
 * keyless copy and the panel would keep reporting keys-missing after the keys were deployed.
 * .nojekyll is in neither list - it is a hosting-side marker the app never fetches. */
const NETWORK_FIRST = ["data.json", "push-config.json"];
const NETWORK_TIMEOUT_MS = 6000;

const SHELL_URLS = new Set(SHELL.map(path => new URL(path, self.location.href).href));

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then(cache => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    // finan-data never matches the prefix, so it survives every version bump.
    await Promise.all(names
      .filter(name => /^finan-shell-/.test(name) && name !== SHELL_CACHE)
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

function isNetworkFirst(url) {
  return NETWORK_FIRST.some(name => url.pathname.endsWith("/" + name));
}

function isShell(url) {
  return SHELL_URLS.has(url.origin + url.pathname);
}

async function networkFirst(request) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), NETWORK_TIMEOUT_MS);
  try {
    const response = await fetch(request, {signal: controller.signal});
    clearTimeout(timer);
    if (response && response.ok) {
      const cache = await caches.open(DATA_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    clearTimeout(timer);
    const cached = await caches.match(request, {cacheName: DATA_CACHE, ignoreSearch: true});
    if (cached) { return cached; }
    // A synthesised 503 rather than a rejected promise, so app.js renders its error card instead
    // of hanging on a never-settling fetch.
    return new Response(
      JSON.stringify({error: "offline", detail: "No network and no cached copy of this file."}),
      {status: 503, statusText: "Offline", headers: {"Content-Type": "application/json"}}
    );
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request, {cacheName: SHELL_CACHE, ignoreSearch: true});
  if (cached) { return cached; }
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(SHELL_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    return new Response("", {status: 504, statusText: "Offline"});
  }
}

async function navigationResponse(request) {
  const cached = await caches.match(request, {cacheName: SHELL_CACHE, ignoreSearch: true});
  if (cached) { return cached; }
  try {
    const response = await fetch(request);
    if (response && response.ok) { return response; }
  } catch (err) {
    // fall through to the cached shell
  }
  const fallback = await caches.match(new URL("./index.html", self.location.href).href,
                                      {cacheName: SHELL_CACHE});
  if (fallback) { return fallback; }
  return new Response("<!doctype html><title>Finan</title><p>Offline, and the app shell is not "
                      + "cached yet.</p>",
                      {status: 503, statusText: "Offline",
                       headers: {"Content-Type": "text/html; charset=utf-8"}});
}

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") { return; }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) { return; }

  if (isNetworkFirst(url)) {
    event.respondWith(networkFirst(request));
  } else if (request.mode === "navigate") {
    event.respondWith(navigationResponse(request));
  } else if (isShell(url)) {
    event.respondWith(cacheFirst(request));
  }
  // Anything else: no respondWith, so the browser performs its own plain fetch.
});

const PUSH_FALLBACK = {title: "Finan", body: "Open Finan for today's check."};

function readPush(data) {
  if (!data) { return PUSH_FALLBACK; }
  try {
    return data.json();
  } catch (err) {
    let text = "";
    try { text = data.text(); } catch (err2) { text = ""; }
    return text ? {title: PUSH_FALLBACK.title, body: text} : PUSH_FALLBACK;
  }
}

function str(value, fallback) {
  return typeof value === "string" && value ? value : fallback;
}

/* ALWAYS shows a notification: iOS revokes the permission of an app that receives a push and
 * shows nothing. */
self.addEventListener("push", event => {
  const payload = readPush(event.data) || PUSH_FALLBACK;
  event.waitUntil(self.registration.showNotification(
    str(payload.title, PUSH_FALLBACK.title),
    {
      body: str(payload.body, PUSH_FALLBACK.body),
      icon: "./icons/icon-192.png",
      badge: "./icons/icon-192.png",
      tag: str(payload.tag, "finan"),
      renotify: true,
      data: {url: str(payload.url, "./")}
    }
  ));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "./";
  event.waitUntil((async () => {
    const scope = self.registration.scope;
    const windows = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    for (const client of windows) {
      if (client.url.startsWith(scope)) { return client.focus(); }
    }
    return self.clients.openWindow(url);
  })());
});
