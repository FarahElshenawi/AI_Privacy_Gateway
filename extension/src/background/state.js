// Per-tab bookkeeping that must survive the MV3 service worker being stopped and restarted
// (Chrome does this after ~30 s idle). It is kept in chrome.storage.session: in memory only,
// cleared when the browser closes, never written to disk. Only small metadata goes in
// (filenames, hashes, ids, expiry times). Never file bytes or the user's text.
const KEY = "doppel_state";

export function createState(area = () => chrome.storage.session) {
  const maps = new Map();      // name -> { map, strip }
  let scheduled = false;

  const flush = () => {
    scheduled = false;
    const snap = {};
    for (const [name, { map, strip }] of maps) {
      snap[name] = [...Map.prototype.entries.call(map)].map(([k, v]) => [k, strip ? strip(v) : v]);
    }
    return Promise.resolve(area().set({ [KEY]: snap })).catch(() => {});
  };
  const dirty = () => {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(flush);
  };

  class PersistedMap extends Map {
    // Callers mutate queues in place after get(), so any access schedules a (coalesced) snapshot.
    get(k) { dirty(); return super.get(k); }
    set(k, v) { dirty(); return super.set(k, v); }
    delete(k) { dirty(); return super.delete(k); }
  }

  return {
    map(name, { strip } = {}) {
      const map = new PersistedMap();
      maps.set(name, { map, strip });
      return map;
    },
    /** Load what an earlier worker instance saved. Entries created since start-up win. */
    async hydrate() {
      let saved;
      try { saved = (await area().get(KEY))[KEY]; } catch { return; }
      if (!saved || typeof saved !== "object") return;
      for (const [name, rows] of Object.entries(saved)) {
        const entry = maps.get(name);
        if (!entry || !Array.isArray(rows)) continue;
        for (const [k, v] of rows) {
          if (!Map.prototype.has.call(entry.map, k)) Map.prototype.set.call(entry.map, k, v);
        }
      }
    },
    flush,
  };
}
