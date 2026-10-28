// Shared by the service worker and the popup.

export const HOST = "no.erlbon.accipio";

// Only these go to Accipio; blob:, data: and file: links exist only inside the browser, so Chrome keeps them.
export function isTakeable(url) {
  return typeof url === "string" && /^(https?|ftp):\/\//i.test(url);
}

// One message to the native host; resolves to its reply, or {ok: false, error} when it cannot be reached.
export function send(message) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendNativeMessage(HOST, message, (reply) => {
        const failure = chrome.runtime.lastError;
        resolve(failure ? { ok: false, error: failure.message } : reply || { ok: false, error: "no answer" });
      });
    } catch (e) {
      resolve({ ok: false, error: String(e) });
    }
  });
}

// "name=value; name2=value2" for the link's site, as the browser would send it.
export async function cookieHeader(url) {
  try {
    const cookies = await chrome.cookies.getAll({ url });
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch (e) {
    return "";
  }
}
