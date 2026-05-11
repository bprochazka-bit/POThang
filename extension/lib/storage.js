// Thin wrapper around browser.storage.local for the captured-items list.

const KEY = "items";

export async function getList() {
  const out = await browser.storage.local.get({ [KEY]: [] });
  return out[KEY];
}

async function setList(items) {
  await browser.storage.local.set({ [KEY]: items });
}

export async function addItem(item) {
  const list = await getList();
  list.push({
    id:
      (crypto.randomUUID && crypto.randomUUID()) ||
      `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    captured_at: new Date().toISOString(),
    ...item,
  });
  await setList(list);
}

export async function removeItem(id) {
  const list = await getList();
  await setList(list.filter((i) => i.id !== id));
}

export async function clearList() {
  await setList([]);
}
