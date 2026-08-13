/* PMO announcement banner.
 *
 * Injected into Plane's HTML by nginx sub_filter, so it ships without touching
 * apps/web and without any JavaScript build. See ./README.md for the coupling
 * this depends on.
 *
 * Notes that matter:
 *  - React owns <body>, so the host element is appended at runtime and re-appended
 *    by a MutationObserver if React ever removes it.
 *  - A shadow root keeps Plane's Tailwind preflight from restyling the banner and
 *    vice versa.
 *  - 401 (signed out, sign-in page, public space pages) renders nothing.
 */
(function () {
  "use strict";
  var VERSION = "1.2";
  var HEIGHT = 40;
  var POLL_MS = 60000;
  var host, shadow, current = [], reported = {};

  var COLORS = {
    info:     { light: ["#eaf2fd", "#1c5cab", "#cde2fb"], dark: ["#12233c", "#9ec5f4", "#1c3f6b"] },
    warning:  { light: ["#fdf3e2", "#8a5a00", "#f5deb0"], dark: ["#2e2410", "#fab219", "#5a4715"] },
    critical: { light: ["#fdeceb", "#b02a2a", "#f7c9c7"], dark: ["#331717", "#e88b8b", "#6b2727"] }
  };

  function isDark() {
    var t = document.documentElement.getAttribute("data-theme") || "";
    if (t.indexOf("dark") !== -1) return true;
    if (t.indexOf("light") !== -1) return false;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function api(path, opts) {
    opts = opts || {};
    opts.credentials = "include";
    opts.headers = opts.headers || { "Content-Type": "application/json" };
    return fetch(path, opts);
  }

  function ensureHost() {
    if (host && document.body.contains(host)) return;
    host = document.createElement("div");
    host.id = "pmo-announcement-host";
    host.style.cssText =
      "position:fixed;top:0;left:0;right:0;z-index:2147483000;pointer-events:none;";
    shadow = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
    document.body.appendChild(host);
  }

  function reclaimLayout(count) {
    var px = count * HEIGHT;
    var style = document.getElementById("pmo-announcement-layout");
    if (!style) {
      style = document.createElement("style");
      style.id = "pmo-announcement-layout";
      document.head.appendChild(style);
    }
    // push Plane's root container down instead of covering its top bar
    style.textContent = px
      ? ".desktop-app-container{height:calc(100vh - " + px + "px)!important;margin-top:" + px + "px!important}"
      : "";
  }

  function render(items) {
    ensureHost();
    var dark = isDark();
    var html =
      "<style>" +
      ":host{all:initial}" +
      ".wrap{font:500 13px/1.4 system-ui,-apple-system,'Segoe UI',sans-serif;pointer-events:auto}" +
      ".bar{display:flex;align-items:center;gap:12px;height:" + HEIGHT + "px;padding:0 16px;box-sizing:border-box;border-bottom:1px solid}" +
      ".msg{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
      ".who{opacity:.75;font-weight:400}" +
      "a.cta{color:inherit;text-decoration:underline;white-space:nowrap}" +
      "button{all:unset;cursor:pointer;padding:2px 6px;border-radius:4px;opacity:.7;font-size:15px;line-height:1}" +
      "button:hover{opacity:1}" +
      "</style><div class='wrap'>" +
      items.map(function (a) {
        var c = (COLORS[a.level] || COLORS.info)[dark ? "dark" : "light"];
        return (
          "<div class='bar' style=\"background:" + c[0] + ";color:" + c[1] + ";border-color:" + c[2] + "\">" +
          "<span class='msg'>" + esc(a.message) +
          (a.author_name ? " <span class='who'>— " + esc(a.author_name) + "</span>" : "") +
          "</span>" +
          (a.link_url ? "<a class='cta' data-id='" + esc(a.id) + "' href='" + esc(a.link_url) + "' target='_blank' rel='noopener'>" +
            esc(a.link_label || "Details") + "</a>" : "") +
          (a.is_dismissible ? "<button data-id='" + esc(a.id) + "' title='Dismiss'>&times;</button>" : "") +
          "</div>"
        );
      }).join("") +
      "</div>";
    shadow.innerHTML = html;
    reclaimLayout(items.length);

    items.forEach(function (a) {
      if (reported[a.id]) return;          // one receipt per page load, not per re-render
      reported[a.id] = true;
      api("/api/announcements/" + a.id + "/seen/receipt/", { method: "POST", body: "{}" })
        .catch(function () {});
    });

    var links = shadow.querySelectorAll("a.cta[data-id]");
    for (var k = 0; k < links.length; k++) {
      links[k].addEventListener("click", function (e) {
        api("/api/announcements/" + e.currentTarget.getAttribute("data-id") + "/clicked/receipt/",
            { method: "POST", body: "{}" }).catch(function () {});
      });
    }

    var buttons = shadow.querySelectorAll("button[data-id]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function (e) {
        var id = e.currentTarget.getAttribute("data-id");
        api("/api/announcements/" + id + "/dismiss/", { method: "POST", body: "{}" })
          .then(function () {
            current = current.filter(function (x) { return x.id !== id; });
            render(current);
          });
      });
    }
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function refresh() {
    api("/api/announcements/")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (items) {
        if (!items) {
          window.__pmoBanner = { version: VERSION, state: "not-signed-in-or-api-error" };
          current = []; if (shadow) { shadow.innerHTML = ""; } reclaimLayout(0); return;
        }
        current = items;
        window.__pmoBanner = { version: VERSION, state: items.length ? "rendered" : "none-for-this-user", count: items.length };
        render(items);
      })
      .catch(function (e) {
        window.__pmoBanner = { version: VERSION, state: "fetch-failed", error: String(e) };
      });
  }

  function start() {
    window.__pmoBanner = { version: VERSION, state: "starting" };
    refresh();
    setInterval(refresh, POLL_MS);
    // React re-renders <body>; put the banner back if it gets torn out
    new MutationObserver(function () {
      if (current.length && host && !document.body.contains(host)) render(current);
    }).observe(document.body, { childList: true });
    // restyle on theme switch
    new MutationObserver(function () {
      if (current.length) render(current);
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
