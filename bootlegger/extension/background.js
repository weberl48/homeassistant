/* Bootlegger overlay — background fetcher. The draft room is HTTPS and the Pi
   is plain HTTP on the LAN; a page fetch would be blocked as mixed content,
   but an extension service worker with host_permissions is not. */
const BASE = "http://192.168.1.160:8484";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "board") {
    fetch(`${BASE}/api/draft/board`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((board) => sendResponse({ ok: true, board }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true; // keep the channel open for the async response
  }
});
