// kit.js: the few things every face needs, and nothing else. Copied unchanged into every face
// (a face is served from its own directory and cannot reach a sibling's files);
// tests/test_faces.py fails while any copy differs from faces/template/kit.js.
//
// Every read is a same-origin GET of one of gonk's JSON routes, under the viewer's own
// session: a face can see exactly what the person looking at it can, and nothing more.

// An element: el("a", {href: "/x", class: "tag"}, "text", child, ...). Text is always a text
// node, never markup, so a title holding "<script>" stays words. `style` is refused: the
// door's CSP blocks style attributes, so a face styles through classes in its own CSS.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (name === "style") throw new Error("kit.el: no style attributes under the face CSP; use a class");
    if (name.startsWith("on")) throw new Error("kit.el: no inline handlers; use addEventListener");
    node.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Read one of gonk's JSON routes: same origin, the session's cookie, JSON asked for by name.
// Throws an Error carrying the status, so a face can say what failed in words.
export async function read(path, params = {}) {
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error(`kit.read: ${path} is not a same-origin path`);
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  const answer = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!answer.ok) {
    const error = new Error(`${url.pathname}: ${answer.status} ${answer.statusText}`);
    error.status = answer.status;
    throw error;
  }
  const doc = await answer.json();
  stamp(doc, doorTime(answer));
  return doc;
}

// The door's clock. Whether a lease has run out is judged against the time of the answer that
// carried the item, from that answer's Date header (same origin, so a face can read it), never
// against the viewer's own clock, which may be skewed on another machine. `answeredAt(x)` is
// that time in milliseconds for a document `read` answered, an item `item` or `listAll`
// answered, or null when the answer carried no readable Date header.
const answered = new WeakMap();
function doorTime(answer) {
  const header = answer.headers && answer.headers.get("Date");
  const time = header ? Date.parse(header) : NaN;
  return Number.isFinite(time) ? time : null;
}
function stamp(value, time) {
  if (time !== null && value && typeof value === "object") answered.set(value, time);
  return value;
}
export function answeredAt(value) {
  return value && typeof value === "object" && answered.has(value) ? answered.get(value) : null;
}
// "Now" for an item: the door's time for the answer that carried it. Only when that answer had
// no Date header (or the item was not read through `read`) does this fall back to the viewer's
// clock, which is the one a skewed machine gets wrong.
export function nowFor(it) {
  const time = answeredAt(it);
  return time !== null ? time : Date.now();
}

// gonk's mechanical paths (docs/ui-conventions.md D10: path /a/b/c is IRI urn:a:b:c). The
// default ledger is `urn:iki:ledger:…` with no name; a named one is `urn:iki:ledger:{name}:…`.
// `ledger` "" or "default" means the default ledger.
function ledgerBase(ledger) {
  return !ledger || ledger === "default" ? "/iki/ledger" : `/iki/ledger/${encodeURIComponent(ledger)}`;
}
export const paths = {
  item: (ledger, number) => `${ledgerBase(ledger)}/item/${Number(number)}`,
  items: (ledger) => `${ledgerBase(ledger)}/items`,
  // gonk's own pages, for links out of a face: an item, and a ledger filtered by labels.
  itemPage: (ledger, number) => `/l/${encodeURIComponent(ledger || "default")}/item/${Number(number)}`,
  labelPage: (ledger, labels) =>
    `/l/${encodeURIComponent(ledger || "default")}?labels=${encodeURIComponent([].concat(labels).join(","))}`,
  // An item's JSON face by its IRI, by the mechanical rule: `urn:iki:ledger:acme:item:01J…` is
  // `/iki/ledger/acme/item/01J…`. Only a ledger item's IRI is read; anything else is refused.
  itemByIri: (iri) => {
    const parts = String(iri || "").split(":");
    if (parts[0] !== "urn" || parts[1] !== "iki" || parts[2] !== "ledger" || parts.length < 5 ||
        parts[parts.length - 2] !== "item" || parts.some((p) => p === "")) {
      throw new Error(`kit.paths.itemByIri: ${iri} is not a ledger item's IRI`);
    }
    return "/" + parts.slice(1).map(encodeURIComponent).join("/");
  },
};

// One item's JSON face: the `item` object of {"item": {...}}.
export async function item(ledger, number) {
  const doc = await read(paths.item(ledger, number));
  return stamp(doc.item, answeredAt(doc));
}

// One item's JSON face read by its IRI (a link target's `iri`), so an item in ANOTHER ledger is
// read from its own ledger. The item carries `ledger` from the answer, for its label and link.
export async function itemAt(iri) {
  const doc = await read(paths.itemByIri(iri));
  const it = doc.item;
  if (it && typeof it === "object") {
    if (!it.iri) it.iri = iri;
    if (!it.ledger) it.ledger = doc.ledger || "default";
  }
  return stamp(it, answeredAt(doc));
}

// How a person reads an item: `{ledger}#{number}`, in its own ledger.
export function labelOf(it) {
  return `${it.ledger || "default"}#${it.number}`;
}

// Every item of a listing, a page at a time. A listing answers
// {schema, ledger, count, total, offset, items, unreadable}; paging stops at `total`, not at a
// short page. `params` are the listing's named arguments (labels=, status=, ...). gonk's listing
// answers OPEN items unless `status` says otherwise: never list a ledger to find an item's
// children (they include closed ones); read `item.children` instead (`children` below).
export async function listAll(ledger, params = {}, pageSize = 200, maxPages = 50) {
  const all = [];
  for (let page = 0; page < maxPages; page++) {
    const doc = await read(paths.items(ledger), { ...params, limit: pageSize, offset: all.length });
    const items = doc.items || [];
    for (const it of items) stamp(it, answeredAt(doc));
    all.push(...items);
    const total = Number.isInteger(doc.total) ? doc.total : null;
    if (items.length === 0 || (total !== null ? all.length >= total : items.length < pageSize)) return all;
  }
  return all;
}

// Run `task` over `inputs` with at most `limit` in flight; answers results in input order.
// A failed task answers {error} in its place rather than failing the whole pool.
export async function pool(inputs, task, limit = 6) {
  const results = new Array(inputs.length);
  let next = 0;
  async function worker() {
    while (next < inputs.length) {
      const i = next++;
      try {
        results[i] = await task(inputs[i], i);
      } catch (error) {
        results[i] = { error };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, inputs.length) }, worker));
  return results;
}

// Read each of `iris` once (by `itemAt`, or `read` in a test), so each from its own ledger.
// Answers {items, unreadable}: the items read, in input order, and for each read that failed
// {iri, reason}, so a face can say how many it could not show and why. Nothing else of a
// failed read is kept: not its answer's body, nor anything else from that ledger.
export async function readEach(iris, read = itemAt) {
  const results = await pool(iris, (iri) => read(iri));
  const items = [];
  const unreadable = [];
  results.forEach((it, i) => {
    if (it && typeof it === "object" && !it.error) items.push(it);
    else unreadable.push({ iri: iris[i], reason: refusal(it && it.error) });
  });
  return { items, unreadable };
}

// Why a read failed, in words: "denied" (401, 403: no read grant for that ledger), "not found"
// (404), or "failed" (anything else, or an answer that carried no item).
export const REASONS = ["denied", "not found", "failed"];
export function refusal(error) {
  const status = error && error.status;
  if (status === 401 || status === 403) return "denied";
  if (status === 404) return "not found";
  return "failed";
}

// The sentence for what `readEach` could not read, grouped by reason, or "" for none:
// "2 steps could not be read (1 denied, 1 not found): you may not have access to their ledger."
export function unreadableWords(unreadable, one, many) {
  const n = unreadable.length;
  if (!n) return "";
  const count = (reason) => unreadable.filter((u) => u.reason === reason).length;
  const groups = REASONS.filter((r) => count(r)).map((r) => (n === 1 ? r : `${count(r)} ${r}`));
  return `${n} ${n === 1 ? one : many} could not be read (${groups.join(", ")}): ` +
    `you may not have access to ${n === 1 ? "its" : "their"} ledger.`;
}

// That sentence on the page, under the heading, with the IRIs (and nothing else of them) in a
// <details>; null when everything was read. Say the same sentence in the `announce`.
export function unreadableNote(unreadable, one, many) {
  const words = unreadableWords(unreadable, one, many);
  if (!words) return null;
  return el(
    "div",
    {},
    el("p", { class: "warn" }, words),
    el(
      "details",
      {},
      el("summary", {}, unreadable.length === 1 ? "Its address" : "Their addresses"),
      el("ul", { class: "items" }, unreadable.map((u) => el("li", {}, el("code", {}, u.iri), ` (${u.reason})`))),
    ),
  );
}

// An item's identity is its IRI: numbers repeat across ledgers, and a link target in another
// ledger carries `number: null` and an IRI ending in an opaque id. `iriOf` is the IRI of a link
// target, a child entry or an item; `linkedIris` and `childIris` are what links and children
// point at, by IRI. A face that may hold items of more than one ledger keys them by these.
export function iriOf(target) {
  if (typeof target === "string") return target || null;
  return (target && (target.iri || target.id)) || null;
}
export function linkedIris(it, type) {
  return (it.links || [])
    .filter((l) => l.type === type)
    .map((l) => iriOf(l.target))
    .filter((iri) => iri !== null);
}
export function childIris(it) {
  return (it.children || []).map(iriOf).filter((iri) => iri !== null);
}

// The number at the end of an item IRI or link target (`urn:iki:ledger:x:item:12` -> 12).
// Only for items of one ledger: a target in another ledger has no number here (null).
export function numberOf(target) {
  const iri = typeof target === "string" ? target : (target && (target.iri || target.id)) || "";
  const n = (target && typeof target === "object" && target.number) || iri.split(":").pop();
  return Number.isInteger(Number(n)) ? Number(n) : null;
}

// The numbers an item links to with a link of `type`: "blocks", "related" or "parent".
// `item.links` is [{type, target: {number, display, iri}}]; there is no "child" link type.
export function linked(it, type) {
  return (it.links || [])
    .filter((l) => l.type === type)
    .map((l) => numberOf(l.target))
    .filter((n) => n !== null);
}

// An item's children, open and closed alike: its JSON face carries them as `item.children`,
// [{number, display, iri}]. One read of the parent, never a scan of the ledger.
export function children(it) {
  return (it.children || []).map(numberOf).filter((n) => n !== null);
}

// What state an item is in, as words a person reads (color is never the only signal).
// `status` is "open" or "closed"; any non-null `awaiting` ("dispatcher", ...) is a hand-back;
// `claim` is null or {holder, kind, expires, lease, principal, purpose}. `now` is the door's
// time for the answer that carried the item (`nowFor`); pass one only to judge at a set time.
export function stateOf(it, now = nowFor(it)) {
  if ((it.status || "open") !== "open") return "done";
  if (it.awaiting !== null && it.awaiting !== undefined && it.awaiting !== "") return "awaiting";
  const claim = it.claim;
  if (claim && claim.holder) {
    return claim.expires && Date.parse(claim.expires) < now ? "expired" : "claimed";
  }
  return "open";
}

// Say something to a screen reader without moving focus: the page's role=status region.
export function announce(text) {
  const region = document.querySelector("[role=status]");
  if (region) region.textContent = text;
}

// The query string as the face's state: a bookmark is a saved view.
export function query() {
  return new URLSearchParams(location.search);
}
export function setQuery(params) {
  const url = new URL(location.href);
  url.search = new URLSearchParams(params).toString();
  history.replaceState(null, "", url);
}
