// Drives extension/background.js with a stand-in for Chrome and checks how it hands downloads to Accipio.
// Run by tests/test_browser.py (node tests/js/takeover.mjs); exits non-zero with the reason on a failure.
//
// The point (2026-11-05): the takeover happens in onDeterminingFilename, before Chrome's own "Save As" box, and
// a download Accipio did not take is handed back with suggest() so Chrome carries on as usual.

const state = { reply: { ok: true }, takeover: true, sent: [], cancelled: [], erased: [], listener: null };
const noop = { addListener() {} };

globalThis.chrome = {
  storage: { local: { get: async () => ({ takeover: state.takeover }) } },
  cookies: { getAll: async () => [{ name: "session", value: "abc" }] },
  runtime: {
    lastError: null,
    sendNativeMessage: (host, message, callback) => { state.sent.push(message); callback(state.reply); },
    onInstalled: noop, onStartup: noop, onMessage: noop,
  },
  contextMenus: { onClicked: noop, removeAll() {}, create() {} },
  downloads: {
    onDeterminingFilename: { addListener: (f) => { state.listener = f; } },
    onCreated: noop,
    cancel: async (id) => { state.cancelled.push(id); },
    erase: async (query) => { state.erased.push(query.id); },
    download() {},
  },
};
if (!globalThis.navigator) globalThis.navigator = { userAgent: "test" };

await import(new URL("../../extension/background.js", import.meta.url));

function fail(why) {
  console.error("FAIL: " + why);
  process.exit(1);
}

async function determine(item) {
  let suggested = false;
  const waits = state.listener(item, () => { suggested = true; });
  if (waits !== true) fail("the listener must return true so Chrome waits for the answer");
  await new Promise((resolve) => setTimeout(resolve, 20));
  return suggested;
}

const link = { id: 1, url: "https://a.org/book.epub", referrer: "https://a.org/page", incognito: false };

if (!state.listener) fail("no onDeterminingFilename listener: Chrome's Save As box would open first");

// Accipio takes it: cancelled and erased, Chrome is never told to go on (so no Save As box).
let suggested = await determine(link);
if (suggested || state.cancelled[0] !== 1 || state.erased[0] !== 1) fail("a taken download must be cancelled, not suggested");
const sent = state.sent.at(-1);
if (sent.type !== "add" || sent.url !== link.url || sent.page_url !== link.referrer || sent.cookies !== "session=abc")
  fail("the offer to Accipio is wrong: " + JSON.stringify(sent));
if (sent.takeover !== true) fail("a hand-over must say so, or Accipio says yes before its own check worked");

// Accipio cannot be reached: Chrome goes on with its own name, nothing cancelled.
state.reply = { ok: false, error: "Accipio is not running" };
state.cancelled = [];
suggested = await determine({ ...link, id: 2 });
if (!suggested || state.cancelled.length) fail("a download Accipio did not take must go back to Chrome");

// Incognito, a blob: link, and the takeover switched off: straight back to Chrome, nothing offered.
state.reply = { ok: true };
for (const [what, item, takeover] of [["incognito", { ...link, id: 3, incognito: true }, true],
                                      ["blob link", { ...link, id: 4, url: "blob:https://a.org/x" }, true],
                                      ["takeover off", { ...link, id: 5 }, false]]) {
  state.takeover = takeover;
  const before = state.sent.length;
  if (!(await determine(item)) || state.sent.length !== before) fail(what + " must go to Chrome without asking Accipio");
}

console.log("ok");
