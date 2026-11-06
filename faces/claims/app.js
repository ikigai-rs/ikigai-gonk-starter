// Claims and hand-backs: who holds what (expired leases first, since those are stuck), and
// every item handed back, grouped by whose turn it is.
import { el, listAll, paths, stateOf, announce, query, setQuery } from "./kit.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");

// Whose turn a handed-back item is: the dispatcher's while `awaiting` says so; otherwise the
// newest comment's author, if the ledger names one.
function turnOf(it) {
  if (it.awaiting === "dispatcher") return "the dispatcher";
  return it.awaiting || "unknown";
}

function line(ledger, it, note, warn) {
  return el(
    "li",
    {},
    el("a", { href: paths.itemPage(ledger, it.number) }, `${ledger}#${it.number}`),
    ` ${it.title || ""} `,
    el("span", { class: warn ? "warn" : "muted" }, `(${note})`),
  );
}

async function show(ledger) {
  announce("Reading open items…");
  out.replaceChildren();
  try {
    // No viewer clock: each item's lease is judged by the door's time for the page that carried it.
    const items = (await listAll(ledger, { status: "open" })).filter((it) => (it.status || "open") === "open");
    const holders = new Map();
    const turns = new Map();
    for (const it of items) {
      const state = stateOf(it);
      if (state === "claimed" || state === "expired") {
        const h = it.claim.holder;
        if (!holders.has(h)) holders.set(h, []);
        holders.get(h).push(it);
      } else if (state === "awaiting" || it.awaiting) {
        const t = turnOf(it);
        if (!turns.has(t)) turns.set(t, []);
        turns.get(t).push(it);
      }
    }
    const expiredCount = (list) => list.filter((it) => stateOf(it) === "expired").length;
    const holderOrder = [...holders].sort((a, b) => expiredCount(b[1]) - expiredCount(a[1]) || a[0].localeCompare(b[0]));
    out.append(el("h2", {}, `Claimed (${[...holders.values()].flat().length})`));
    if (!holderOrder.length) out.append(el("p", { class: "muted" }, "Nothing is claimed."));
    for (const [holder, list] of holderOrder) {
      list.sort((a, b) => (stateOf(b) === "expired") - (stateOf(a) === "expired") || a.number - b.number);
      const expired = expiredCount(list);
      out.append(
        el("h3", {}, `${holder}: ${list.length} held${expired ? `, ${expired} with an expired lease` : ""}`),
        el(
          "ul",
          { class: "items" },
          list.map((it) =>
            stateOf(it) === "expired"
              ? line(ledger, it, `lease expired ${it.claim.expires}`, true)
              : line(ledger, it, it.claim.expires ? `lease until ${it.claim.expires}` : "claimed", false),
          ),
        ),
      );
    }
    out.append(el("h2", {}, `Handed back (${[...turns.values()].flat().length})`));
    if (!turns.size) out.append(el("p", { class: "muted" }, "Nothing is waiting."));
    for (const [turn, list] of [...turns].sort((a, b) => a[0].localeCompare(b[0]))) {
      out.append(
        el("h3", {}, `${turn}'s turn: ${list.length}`),
        el("ul", { class: "items" }, list.sort((a, b) => a.number - b.number).map((it) => line(ledger, it, "handed back", false))),
      );
    }
    announce(`${holders.size} holders, ${turns.size} queues of handed-back items.`);
  } catch (error) {
    out.append(el("p", { class: "warn" }, `Could not read the ledger: ${error.message}`));
    announce("Could not read the ledger.");
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const ledger = form.ledger.value.trim() || "default";
  setQuery({ ledger });
  show(ledger);
});
const q = query();
if (q.get("ledger")) {
  form.ledger.value = q.get("ledger");
  show(form.ledger.value);
}
