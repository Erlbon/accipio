// Drives extension/background.js with a stand-in for FIREFOX and checks the hand-over there (2026-11-17#04).
// Run by tests/test_browser.py (node tests/js/firefox.mjs); exits non-zero with the reason on a failure.
//
// Firefox has no onDeterminingFilename: the download is already running when onCreated comes. A yes from Accipio
// stops the browser's copy (or deletes it when a small file was already done); a no leaves it alone, and the file
// is reported for filing when Firefox has saved it, even if it finished before Accipio answered.

const state = { reply: { ok: true }, sent: [], cancelled: [], erased: [], removed: [], created: null, changed: null,
                session: {}, items: {}, menus: [] };
const noop = { addListener() {} };

globalThis.chrome = {
  storage: {
    local: { get: async () => ({ takeover: true }) },
    session: {
      get: async (key) => (key in state.session ? { [key]: state.session[key] } : {}),
      set: async (values) => { Object.assign(state.session, values); },
      remove: async (key) => { delete state.session[key]; },
    },
  },
  cookies: { getAll: async () => [{ name: "session", value: "abc" }] },
  runtime: {
    lastError: null,
    getURL: (path) => "moz-extension://0b9a5a3e-6f0c-4d51-9d0e-1c2d3e4f5a6b/" + path,
    sendNativeMessage: (host, message, callback) => { state.sent.push(message); callback(state.reply); },
    onInstalled: { addListener: (f) => { state.installed = f; } }, onStartup: noop, onMessage: noop,
  },
  contextMenus: { onClicked: noop, removeAll(done) { done(); }, create(menu) { state.menus.push(menu); } },
  downloads: {
    // no onDeterminingFilename: that is what makes it Firefox
    onCreated: { addListener: (f) => { state.created = f; } },
    onChanged: { addListener: (f) => { state.changed = f; } },
    search: async (query) => (state.items[query.id] ? [state.items[query.id]] : []),
    cancel: async (id) => { state.cancelled.push(id); },
    erase: async (query) => { state.erased.push(query.id); },
    removeFile: async (id) => { state.removed.push(id); },
    download() {},
  },
};
if (!globalThis.navigator) globalThis.navigator = { userAgent: "Mozilla/5.0 Firefox/160.0" };

await import(new URL("../../extension/background.js", import.meta.url));

function fail(why) {
  console.error("FAIL: " + why);
  process.exit(1);
}

async function create(item, stateNow = "in_progress") {
  state.items[item.id] = { ...item, state: stateNow };
  const before = state.sent.length;
  state.created(item);
  await new Promise((resolve) => setTimeout(resolve, 30));
  return state.sent.slice(before);
}

if (!state.created) fail("no onCreated listener: nothing would be handed over in Firefox");

// Accipio takes it: Firefox's running copy is stopped and forgotten. Nothing about the browser itself is sent.
const link = { id: 1, url: "https://a.org/book.epub", referrer: "https://a.org/page", incognito: false,
               filename: "C:\\Users\\me\\Downloads\\book.epub" };
let sent = await create(link);
if (sent.length !== 1 || sent[0].type !== "add" || sent[0].takeover !== true || sent[0].url !== link.url
    || sent[0].cookies !== "session=abc") fail("the offer to Accipio is wrong: " + JSON.stringify(sent));
if (sent[0].user_agent !== "") fail("Firefox must not pass the browser's details on (not declared to Mozilla)");
if (state.cancelled[0] !== 1 || state.erased[0] !== 1 || state.removed.length) fail("a taken download must be stopped");

// Taken, but the small file was already done: the browser's finished copy is deleted, so it is not there twice.
await create({ ...link, id: 2 }, "complete");
if (state.removed[0] !== 2 || state.erased[1] !== 2 || state.cancelled.length !== 1)
  fail("a taken download that had already finished must have its file removed");

// Accipio says no: Firefox keeps it, nothing stopped; when Firefox has saved it, it is reported for filing.
state.reply = { ok: false, error: "the server answered 403" };
await create({ ...link, id: 3 });
if (state.cancelled.length !== 1 || state.removed.length !== 1) fail("a download Accipio did not take must be left alone");
state.reply = { ok: true };
let before = state.sent.length;
state.items[3].state = "complete";
state.changed({ id: 3, state: { current: "complete" } });
await new Promise((resolve) => setTimeout(resolve, 30));
let told = state.sent.slice(before);
if (told.length !== 1 || told[0].type !== "file" || told[0].path !== link.filename || told[0].user_agent !== "")
  fail("Firefox's own finished download must be reported for filing: " + JSON.stringify(told));

// A blob: file that finished before it could be remembered is still reported, once.
const blob = { id: 4, url: "blob:https://a.org/1", referrer: "", incognito: false, filename: "C:\\x\\notes.txt" };
sent = await create(blob, "complete");
told = sent.filter((m) => m.type === "file");
if (told.length !== 1 || told[0].path !== blob.filename || told[0].suggested !== blob.filename)
  fail("a blob: file already finished must be reported at once: " + JSON.stringify(sent));
before = state.sent.length;
state.changed({ id: 4, state: { current: "complete" } });
await new Promise((resolve) => setTimeout(resolve, 30));
if (state.sent.length !== before) fail("a file is reported once only");

// A private window is never touched or reported.
sent = await create({ ...link, id: 5, incognito: true });
if (sent.length) fail("a private window's download must never reach Accipio");

// The menu names the browser the user is in.
state.installed();
if (!state.menus.some((m) => m.title === "Download with Firefox instead")) fail("the menu must say Firefox here");

console.log("ok");
