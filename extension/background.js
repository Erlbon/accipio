// Accipio for Chrome: hands downloads to the Accipio app through Chrome's native messaging.
//
// A download is caught in onDeterminingFilename, which Chrome runs BEFORE it picks a file name and before its
// "Save As" box (the "Ask where to save each file" setting). Chrome waits while the extension offers the link to
// Accipio. Only when Accipio says it took it is Chrome's copy cancelled, so no Save As box opens; if Accipio
// cannot be reached (not installed, refused), Chrome is told to go on as if nothing happened, Save As and all.
// So a click never gets lost. (Until 2026-11-05 this happened in onCreated, after Chrome had already opened Save
// As for people with that setting on.)
//
// Cookies for the link's site go along, so downloads behind a login work in Accipio too. They are sent
// only to the local Accipio app, never anywhere else. Incognito downloads are left alone.

import { HOST, cookieHeader, isTakeable, send } from "./common.js";

// links Chrome should download itself: "Download with Chrome instead", or a fallback
const passThrough = new Set();

async function takeoverOn() {
  const { takeover = true } = await chrome.storage.local.get("takeover");
  return takeover;
}

// takeover: Chrome has already started this download and drops it on a yes, so Accipio answers only after its
// own request worked (a no leaves the download with Chrome).
export async function offer(url, pageUrl, referrer = "", takeover = false) {
  return send({
    type: "add",
    takeover,
    url,
    page_url: pageUrl || "",
    referrer: referrer || pageUrl || "",
    cookies: await cookieHeader(url),
    user_agent: navigator.userAgent,
  });
}

// Whether Accipio takes this download (false: leave it to Chrome).
async function decide(item) {
  const url = item.finalUrl || item.url;
  if (item.incognito || !isTakeable(url)) return false;
  if (passThrough.delete(url) || passThrough.delete(item.url)) return false;
  if (!(await takeoverOn())) return false;
  const reply = await offer(url, item.referrer, item.referrer, true);
  if (!reply || !reply.ok) {
    console.warn("Accipio did not take the download:", reply && reply.error);
    return false;
  }
  return true;
}

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  decide(item).then(async (taken) => {
    if (taken) {
      await chrome.downloads.cancel(item.id).catch(() => {});
      await chrome.downloads.erase({ id: item.id }).catch(() => {});
    } else {
      suggest();                     // Chrome carries on with its own name (and Save As, if the user wants it)
    }
  }, () => suggest());
  return true;                       // the answer comes later: Chrome waits for it
});

const MENUS = [
  { id: "accipio-link", title: "Download with Accipio", contexts: ["link"] },
  { id: "accipio-media", title: "Download with Accipio", contexts: ["image", "video", "audio"] },
  { id: "accipio-page", title: "Download this page's video with Accipio", contexts: ["page"] },
  { id: "accipio-chrome", title: "Download with Chrome instead", contexts: ["link"] },
];

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    for (const menu of MENUS) chrome.contextMenus.create(menu);
  });
  send({ type: "ping" });          // tells a running Accipio the extension is in, so it stops showing how to add it
});

chrome.runtime.onStartup.addListener(() => {
  send({ type: "ping" });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const page = info.pageUrl || (tab && tab.url) || "";
  if (info.menuItemId === "accipio-chrome") {
    passThrough.add(info.linkUrl);
    chrome.downloads.download({ url: info.linkUrl });
    return;
  }
  const url = info.menuItemId === "accipio-link" ? info.linkUrl
    : info.menuItemId === "accipio-media" ? info.srcUrl
    : page;
  if (!isTakeable(url)) return;
  const reply = await offer(url, page);
  if (!reply || !reply.ok) console.warn("Accipio could not be reached:", reply && reply.error);
});

// the popup asks through messages, so cookies and native messaging stay in one place
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type === "grab-page") {
    offer(message.url, message.url).then(respond);
    return true;
  }
  if (message.type === "ping") {
    send({ type: "ping" }).then(respond);
    return true;
  }
  return false;
});

export { HOST };
