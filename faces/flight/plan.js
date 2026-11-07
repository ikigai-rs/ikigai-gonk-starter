// The flight plan's logic, apart from the page: which items are steps, in what order they
// land, and which lands next. No DOM here, so tests/faces/logic_test.js runs it against a
// recorded item. A step may be an item of ANY ledger (a run record's steps are often items of
// each repository's own ledger), so steps and their `blocks` edges are keyed by IRI, never by
// number: numbers repeat across ledgers.
import { childIris, iriOf, itemAt, linkedIris, pool, stateOf } from "./kit.js";

// A plan's steps, as IRIs: the record's `item.children` (closed ones too: a landed step stays
// a step) and the targets of its `related` links. One read of the record, never a scan of a
// ledger.
export function stepTargets(record) {
  const iris = new Set([...childIris(record), ...linkedIris(record, "related")]);
  iris.delete(iriOf(record));
  return [...iris];
}

// The steps themselves, each read once by its IRI, so each from its own ledger. A step that
// could not be read is left out.
export async function readSteps(record, read = itemAt) {
  const items = await pool(stepTargets(record), (iri) => read(iri));
  return items.filter((it) => it && !it.error);
}

// Lower priority number first; no priority last; then the item number, then its ledger.
function rank(a, b) {
  const pa = a.priority ?? Infinity;
  const pb = b.priority ?? Infinity;
  return pa - pb || a.number - b.number || String(a.ledger || "").localeCompare(String(b.ledger || "")) ||
    String(iriOf(a)).localeCompare(String(iriOf(b)));
}

// Kahn's algorithm over `blocks` within the plan; a cycle's members follow, ranked, apart.
export function landingOrder(items) {
  const byIri = new Map(items.map((it) => [iriOf(it), it]));
  const waits = new Map(items.map((it) => [iriOf(it), 0]));
  for (const it of items) for (const t of linkedIris(it, "blocks")) if (waits.has(t)) waits.set(t, waits.get(t) + 1);
  const order = [];
  let ready = items.filter((it) => waits.get(iriOf(it)) === 0).sort(rank);
  while (ready.length) {
    const it = ready.shift();
    order.push(it);
    for (const t of linkedIris(it, "blocks")) {
      if (!waits.has(t)) continue;
      waits.set(t, waits.get(t) - 1);
      if (waits.get(t) === 0) ready = [...ready, byIri.get(t)].sort(rank);
    }
  }
  const cycle = items.filter((it) => !order.includes(it)).sort(rank);
  return { order, cycle };
}

// Whether a step can land now: open, not handed back, and nothing open in the plan blocks it.
export function canLand(it, items, now) {
  const state = stateOf(it, now);
  if (state === "done" || state === "awaiting") return false;
  const iri = iriOf(it);
  return !items.some((s) => s !== it && stateOf(s, now) !== "done" && linkedIris(s, "blocks").includes(iri));
}

// `now` is left out in use: each step is then judged by the door's time for the answer that
// carried it (kit.js `nowFor`); a test passes one to judge every step at a set time.
// The plan for the steps read: the landing order, the cycle, and the next step to land.
export function plan(items, now) {
  const { order, cycle } = landingOrder(items);
  return { order, cycle, next: order.find((it) => canLand(it, items, now)) || null };
}
