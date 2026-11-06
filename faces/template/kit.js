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
  return answer.json();
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
};

// One item's JSON face: the `item` object of {"item": {...}}.
export async function item(ledger, number) {
  return (await read(paths.item(ledger, number))).item;
}

// Every item of a listing, a page at a time, until a page comes back short.
// `params` are the listing's named arguments (labels=, status=, ...).
export async function listAll(ledger, params = {}, pageSize = 200, maxPages = 50) {
  const all = [];
  for (let page = 0; page < maxPages; page++) {
    const doc = await read(paths.items(ledger), { ...params, limit: pageSize, offset: page * pageSize });
    const items = doc.items || [];
    all.push(...items);
    if (items.length < pageSize) return all;
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

// The number at the end of an item IRI or link target (`urn:iki:ledger:x:item:12` -> 12).
export function numberOf(target) {
  const iri = typeof target === "string" ? target : (target && (target.iri || target.id)) || "";
  const n = (target && typeof target === "object" && target.number) || iri.split(":").pop();
  return Number.isInteger(Number(n)) ? Number(n) : null;
}

// The numbers an item links to with a link of `type` ("blocks", "related", "parent", ...).
export function linked(it, type) {
  return (it.links || [])
    .filter((l) => l.type === type)
    .map((l) => numberOf(l.target))
    .filter((n) => n !== null);
}

// What state an open item is in, as words a person reads (color is never the only signal).
export function stateOf(it, now = Date.now()) {
  if ((it.status || "open") !== "open") return "done";
  if (it.awaiting === "dispatcher") return "awaiting";
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
