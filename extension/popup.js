import { isTakeable } from "./common.js";

const takeover = document.getElementById("takeover");
const status = document.getElementById("status");
const grab = document.getElementById("grab");

chrome.storage.local.get("takeover").then(({ takeover: on = true }) => { takeover.checked = on; });
takeover.addEventListener("change", () => chrome.storage.local.set({ takeover: takeover.checked }));

chrome.runtime.sendMessage({ type: "ping" }).then((reply) => {
  status.textContent = reply && reply.ok
    ? `Accipio ${reply.version || ""} is ready.`
    : `Accipio cannot be reached: ${(reply && reply.error) || "no answer"}. Start Accipio once so it can register itself.`;
});

grab.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !isTakeable(tab.url)) {
    status.textContent = "This page has no address Accipio can download.";
    return;
  }
  grab.disabled = true;
  const reply = await chrome.runtime.sendMessage({ type: "grab-page", url: tab.url });
  status.textContent = reply && reply.ok ? "Sent to Accipio." : `Not sent: ${(reply && reply.error) || "no answer"}`;
  grab.disabled = false;
});
