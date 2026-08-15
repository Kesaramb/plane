/* PMO naming layer.
 *
 * nginx rewrites the brand strings in the served HTML, but the SPA sets its own
 * document.title on hydration and on every route change, so the tab reverts to
 * "Plane" a beat after load. This puts it back, and renames Plane on the signed
 * out screens where the product name is most visible.
 *
 * Deliberately narrow: text is only rewritten on auth routes, which contain no
 * user content. Renaming "Plane" everywhere would corrupt real work — a brand
 * agency has work items about plane tickets and Planet Earth.
 *
 * Logos and favicons are swapped by nginx at the asset path, not here.
 */
(function () {
  "use strict";
  var TITLE = "PMO — BrandFactory";
  var DEFAULT_TITLE = /^Plane \| Simple, extensible/;
  var SUFFIX = /\s[|–-]\s*Plane\s*$/;

  // Signed-out and setup routes: no user-authored content on the page.
  // "/" belongs here — the app sends signed-in users straight to a workspace, so a
  // rendered root is the sign-in screen. It also rewrites /sign-in to / on load.
  var AUTH = /^\/(sign-in|sign-up|god-mode|onboarding|create-workspace|accounts|forgot-password|reset-password|workspace-invitations|invitations)|^\/$/;

  // Plane's own marketing, which would become a false claim if simply renamed.
  var DROP = /\d[\d,]*\+?\s*teams building with/i;

  // Fixed strings shipped in Plane's bundle. Renaming only inside these is safe on
  // any page, including ones full of user content: a work item called "Plane ticket
  // to Colombo" matches none of them and is left exactly as written.
  var CHROME = [
    /Powered by Plane Pages/g,
    /New at Plane/g,
    /Welcome back to Plane/g,
    /Welcome to Plane/g,
    /Create your Plane account/g,
    /New to Plane\?/g,
    /Make Plane yours/g,
    /set up Plane for how you work/g,
    /Plane APIs? can be used/g,
    /data in Plane with any external system/g,
    /access to Plane data/g,
    /Plane will auto archive/g,
    /use Plane to its potential/g,
    /starts with a project in Plane/g,
    /Looks like Plane didn't start up/g
  ];

  function renameChrome(v) {
    for (var i = 0; i < CHROME.length; i++) {
      v = v.replace(CHROME[i], function (m) { return m.replace(/\bPlane\b/g, "PMO"); });
    }
    return v;
  }

  function fixTitle() {
    var t = document.title || "";
    var want = DEFAULT_TITLE.test(t) || !t ? TITLE
             : SUFFIX.test(t) ? t.replace(SUFFIX, " — PMO")
             : null;
    if (want && want !== t) document.title = want;
  }

  function onAuthRoute() {
    return AUTH.test(location.pathname);
  }

  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEXTAREA: 1, CODE: 1, PRE: 1 };

  function renameText(root) {
    var auth = onAuthRoute();
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (!n.nodeValue || n.nodeValue.indexOf("Plane") === -1) return NodeFilter.FILTER_REJECT;
        var p = n.parentNode;
        if (p && SKIP[p.nodeName]) return NodeFilter.FILTER_REJECT;
        if (p && p.closest && p.closest("[contenteditable],input,textarea")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var n, hits = [];
    while ((n = walker.nextNode())) hits.push(n);
    for (var i = 0; i < hits.length; i++) {
      var node = hits[i];
      if (DROP.test(node.nodeValue)) {
        // hide rather than remove: React still owns the node and will re-render it
        var el = node.parentElement;
        if (el && el.style.display !== "none") el.style.display = "none";
        continue;
      }
      // On auth screens nothing is user-authored, so rename freely; \bPlane\b keeps
      // "Planet" and "planes" intact. Elsewhere, only Plane's own fixed strings.
      var next = auth ? node.nodeValue.replace(/\bPlane\b/g, "PMO")
                      : renameChrome(node.nodeValue);
      if (next !== node.nodeValue) node.nodeValue = next;
    }
  }

  function isDark() {
    var t = document.documentElement.getAttribute("data-theme") || "";
    if (t.indexOf("dark") !== -1) return true;
    if (t.indexOf("light") !== -1) return false;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  // Plane's wordmark is an inline SVG, so nginx cannot swap it at the asset path
  // the way it does the favicons. Its viewBox is a stable fingerprint of that one
  // drawing. The SVG is hidden rather than removed, because React still owns it.
  function swapWordmark() {
    var svg = document.querySelector('svg[viewBox="0 0 253 53"]');
    if (!svg) return;
    if (svg.style.display !== "none") svg.style.display = "none";
    var img = document.getElementById("pmo-wordmark");
    if (!img) {
      img = document.createElement("img");
      img.id = "pmo-wordmark";
      img.alt = "PMO";
      img.style.cssText = "height:26px;max-height:100%;width:auto;display:block";
      svg.parentNode.insertBefore(img, svg.nextSibling);
    }
    // the stock SVG inherits currentColor, so follow the theme the same way
    var want = isDark() ? "/pmo-brand/pmo-mark-white.png" : "/pmo-brand/pmo-mark.png";
    if (img.getAttribute("src") !== want) img.setAttribute("src", want);
  }

  // Plane's customer logos (Sony, Dolby, Accenture...). Left in place they read as
  // BrandFactory's clients, which is a claim we are not entitled to make.
  function hidePlaneCustomers() {
    if (!onAuthRoute()) return;
    var cell = document.querySelector("div.flex.h-7.flex-1.items-center.justify-center");
    var strip = cell && cell.parentElement;
    if (strip && strip.style.display !== "none") strip.style.display = "none";
  }

  function sweep() {
    fixTitle();
    swapWordmark();
    hidePlaneCustomers();
    if (document.body) renameText(document.body);
  }

  function start() {
    sweep();
    // React owns the DOM and rewrites both the title and these screens as it renders
    new MutationObserver(sweep).observe(document.documentElement, {
      childList: true, subtree: true, characterData: true
    });
    window.__pmoBrand = { title: TITLE, authRoute: onAuthRoute() };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
