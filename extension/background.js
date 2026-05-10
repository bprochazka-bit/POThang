// Maintains a toolbar-button badge showing the number of captured items.

async function refreshBadge() {
  const out = await browser.storage.local.get({ items: [] });
  const n = (out.items || []).length;
  const text = n > 0 ? String(n) : "";
  try {
    await browser.action.setBadgeText({ text });
    if (n > 0) {
      await browser.action.setBadgeBackgroundColor({ color: "#2563eb" });
    }
  } catch (_) {
    // setBadge* are not available on all Firefox versions; ignore.
  }
}

browser.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.items) refreshBadge();
});

browser.runtime.onStartup.addListener(refreshBadge);
browser.runtime.onInstalled.addListener(refreshBadge);

// Also refresh once on script load so reloads of the extension pick up
// any pre-existing captured list.
refreshBadge();
