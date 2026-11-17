// Drives extension/background.js with a stand-in for Chrome and checks how it hands downloads to Accipio.
// Run by tests/test_browser.py (node tests/js/takeover.mjs); exits non-zero with the reason on a failure.
//
// The point (2026-11-05): the takeover happens in onDeterminingFilename, before Chrome's own "Save As" box, and
// a download Accipio did not take is handed back with suggest() so Chrome carries on as usual.

const state = { reply: { ok: true }, takeover: true, sent: [], cancelled: [], erased: [], listener: null,
                session: {}, changed: null, saved: {} };
const noop = { addListener() {} };

globalThis.chrome = {
  storage: {
    local: { get: async () => ({ takeover: state.takeover }) },
    session: {
      get: async (key) => (key in state.session ? { [key]: state.session[key] } : {}),
      set: async (values) => { Object.assign(state.session, values); },
      remove: async (key) => { delete state.session[key]; },
    },
  },
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
    onChanged: { addListener: (f) => { state.changed = f; } },
    search: async (query) => (state.saved[query.id] ? [state.saved[query.id]] : []),
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

// Incognito, a blob: link, and the takeover switched off: straight back to Chrome, nothing offered. Accipio's debug
// log gets a note ("seen", 2026-11-07) for the last two, never for incognito.
state.reply = { ok: true };
for (const [what, item, takeover, noted] of [["incognito", { ...link, id: 3, incognito: true }, true, false],
                                             ["blob link", { ...link, id: 4, url: "blob:https://a.org/x" }, true, true],
                                             ["takeover off", { ...link, id: 5 }, false, true]]) {
  state.takeover = takeover;
  const before = state.sent.length;
  const goesToChrome = await determine(item);
  const after = state.sent.slice(before);
  if (!goesToChrome || after.some((m) => m.type !== "seen")) fail(what + " must go to Chrome without asking Accipio");
  if (after.length !== (noted ? 1 : 0)) fail(what + (noted ? " must be noted for the debug log" : " must never be noted"));
  if (noted && (after[0].url !== item.url || !after[0].reason || "cookies" in after[0]))
    fail("the note is wrong: " + JSON.stringify(after[0]));
}

// Files Chrome downloads itself are reported once Chrome has saved them, so Accipio can file them by the rules
// (2026-11-17): a blob: or data: file, and one Accipio said no to. Never incognito, takeover off, or one Accipio took.
async function saved(id, item) {
  state.saved[id] = { id, ...item };
  const before = state.sent.length;
  state.changed({ id, state: { current: "complete" } });
  await new Promise((resolve) => setTimeout(resolve, 20));
  return state.sent.slice(before);
}

state.takeover = true;
state.reply = { ok: true };
const blob = { id: 10, url: "blob:https://a.org/1234", referrer: "https://a.org/page", filename: "notes.txt", incognito: false };
await determine(blob);
let told = await saved(10, { url: blob.url, referrer: blob.referrer, filename: "C:\\Users\\me\\Downloads\\notes.txt", mime: "text/plain" });
if (told.length !== 1 || told[0].type !== "file" || told[0].path !== "C:\\Users\\me\\Downloads\\notes.txt"
    || told[0].suggested !== "notes.txt" || told[0].url !== blob.url || told[0].page_url !== blob.referrer
    || told[0].user_agent !== navigator.userAgent)
  fail("a finished blob: file must be reported with its path and the name Chrome proposed: " + JSON.stringify(told));
if ((await saved(10, { url: blob.url, filename: "C:\\x\\notes.txt" })).length) fail("a file is reported once only");

const data = { id: 11, url: "data:text/plain;base64," + "QUJD".repeat(5000), referrer: "", filename: "big.txt", incognito: false };
const noted = state.sent.length;
await determine(data);
told = await saved(11, { url: data.url, filename: "C:\\x\\big.txt" });
if (told.length !== 1 || told[0].url !== "data:text/plain;base64") fail("a data: link's content must never be sent on");
if (state.sent[noted].url !== "data:text/plain;base64") fail("the debug note must not carry a data: link's content either");

state.reply = { ok: false, error: "the server answered 403" };
await determine({ ...link, id: 12, filename: "book.epub" });
state.reply = { ok: true };
told = await saved(12, { url: link.url, referrer: link.referrer, filename: "C:\\x\\book.epub" });
if (told.length !== 1 || told[0].type !== "file") fail("a download Accipio said no to must be reported when Chrome has saved it");

await determine({ ...link, id: 13 });                                  // Accipio took it
if ((await saved(13, { url: link.url, filename: "C:\\x\\book.epub" })).length) fail("a download Accipio took is not Chrome's to report");
await determine({ ...blob, id: 14, incognito: true });
if ((await saved(14, { url: blob.url, filename: "C:\\x\\notes.txt" })).length) fail("incognito must never be reported");
state.takeover = false;
await determine({ ...blob, id: 15 });
if ((await saved(15, { url: blob.url, filename: "C:\\x\\notes.txt" })).length) fail("with the takeover off Chrome's files are left alone");
if ((await saved(99, { url: link.url, filename: "C:\\x\\other.bin" })).length) fail("a download nobody asked about is not reported");

console.log("ok");
