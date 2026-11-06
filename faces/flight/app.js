// The flight plan: a record item's children and `related` items are a plan's steps; list them
// in the order they can land (topological by `blocks`, ties by priority then number) and mark
// the first step that can land now.
import { el, item, listAll, paths, pool, linked, stateOf, announce, query, setQuery } from "./kit.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");

async function steps(ledger, record) {
  const numbers = new Set([...linked(record, "child"), ...linked(record, "related")]);
  const all = await listAll(ledger, {}).catch(() => []);
  for (const it of all) if (linked(it, "parent").includes(record.number)) numbers.add(it.number);
  numbers.delete(record.number);
  const items = await pool([...numbers], (n) => item(ledger, n));
  return items.filter((it) => it && !it.error);
}

// Lower priority number first; no priority last; then the item number.
function rank(a, b) {
  const pa = a.priority ?? Infinity;
  const pb = b.priority ?? Infinity;
  return pa - pb || a.number - b.number;
}

// Kahn's algorithm over `blocks` within the plan; a cycle's members follow, ranked, marked.
export function landingOrder(items) {
  const byNumber = new Map(items.map((it) => [it.number, it]));
  const waits = new Map(items.map((it) => [it.number, 0]));
  for (const it of items) for (const n of linked(it, "blocks")) if (waits.has(n)) waits.set(n, waits.get(n) + 1);
  const order = [];
  let ready = items.filter((it) => waits.get(it.number) === 0).sort(rank);
  while (ready.length) {
    const it = ready.shift();
    order.push(it);
    for (const n of linked(it, "blocks")) {
      if (!waits.has(n)) continue;
      waits.set(n, waits.get(n) - 1);
      if (waits.get(n) === 0) ready = [...ready, byNumber.get(n)].sort(rank);
    }
  }
  const cycle = items.filter((it) => !order.includes(it)).sort(rank);
  return { order, cycle };
}

// The next step that can land: open, not handed back, and nothing open in the plan blocks it.
function canLand(it, items, now) {
  const state = stateOf(it, now);
  if (state === "done" || state === "awaiting") return false;
  return !items.some((s) => stateOf(s, now) !== "done" && linked(s, "blocks").includes(it.number));
}

const WORDS = {
  done: "done",
  awaiting: "handed back, awaiting the dispatcher",
  claimed: "claimed",
  expired: "claimed, lease expired",
  open: "open",
};

async function show(ledger, number) {
  announce(`Reading record ${number}…`);
  out.replaceChildren();
  try {
    const record = await item(ledger, number);
    const items = await steps(ledger, record);
    const now = Date.now();
    const { order, cycle } = landingOrder(items);
    const next = order.find((it) => canLand(it, items, now));
    const row = (it, extra) =>
      el(
        "li",
        { class: it === next ? "next" : null },
        it === next ? el("strong", {}, "Next to land: ") : null,
        el("a", { href: paths.itemPage(ledger, it.number) }, `${ledger}#${it.number}`),
        ` ${it.title || ""} `,
        el("span", { class: stateOf(it, now) === "expired" ? "warn" : "muted" }, `(${WORDS[stateOf(it, now)]}${it.priority != null ? `, priority ${it.priority}` : ""}${extra || ""})`),
      );
    out.append(
      el("h2", {}, el("a", { href: paths.itemPage(ledger, number) }, `${ledger}#${number}`), ` ${record.title || ""}`),
      el("h3", {}, `Landing order (${order.length} steps)`),
      order.length ? el("ol", { class: "items" }, order.map((it) => row(it))) : el("p", { class: "muted" }, "No steps."),
      cycle.length ? el("h3", {}, "In a blocking cycle (cannot land until it is broken)") : null,
      cycle.length ? el("ul", { class: "items" }, cycle.map((it) => row(it, ", in a cycle"))) : null,
    );
    announce(next ? `Next to land: ${ledger}#${next.number}.` : "No step can land now.");
  } catch (error) {
    out.append(el("p", { class: "warn" }, `Could not read the record: ${error.message}`));
    announce(`Could not read record ${number}.`);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const ledger = form.ledger.value.trim() || "default";
  setQuery({ ledger, record: form.record.value.trim() });
  show(ledger, form.record.value.trim());
});
const q = query();
if (q.get("record")) {
  form.ledger.value = q.get("ledger") || "default";
  form.record.value = q.get("record");
  show(form.ledger.value, form.record.value);
}
