// The board's logic, apart from the page: which column each of an epic's children goes in.
// No DOM here, so tests/faces/logic_test.js runs it against a recorded item. A child may be an
// item of any ledger, so siblings and their `blocks` edges are keyed by IRI, never by number.
import { childIris, iriOf, itemAt, linkedIris, pool, stateOf } from "./kit.js";

export const COLUMNS = [
  ["blocked", "Blocked by an open sibling"],
  ["ready", "Ready"],
  ["flight", "In flight"],
  ["awaiting", "Awaiting (handed back)"],
  ["done", "Done"],
];

// The IRIs of an epic's children: `item.children` of its JSON face, closed ones included.
export function childTargets(epic) {
  const self = iriOf(epic);
  return childIris(epic).filter((iri) => iri !== self);
}

// The children themselves, each read once by its IRI, so each from its own ledger.
export async function readChildren(epic, read = itemAt) {
  const items = await pool(childTargets(epic), (iri) => read(iri));
  return items.filter((it) => it && !it.error);
}

// Which column: done and awaiting are the ledger's own state; a claim is in flight; an open
// unclaimed item is blocked while an open sibling `blocks` it, and ready otherwise.
export function column(it, siblings, now) {
  const state = stateOf(it, now);
  if (state === "done" || state === "awaiting") return state;
  if (state === "claimed" || state === "expired") return "flight";
  const iri = iriOf(it);
  const blocked = siblings.some((s) => s !== it && stateOf(s, now) !== "done" && linkedIris(s, "blocks").includes(iri));
  return blocked ? "blocked" : "ready";
}

// `now` is left out in use: each child is then judged by the door's time for the answer that
// carried it (kit.js `nowFor`); a test passes one to judge every child at a set time.
// The whole board for the children read: each column's items (by number, then ledger), how
// many are done, and the label tally ([label, count], most first).
export function board(kids, now) {
  const sorted = [...kids].sort((a, b) => a.number - b.number ||
    String(a.ledger || "").localeCompare(String(b.ledger || "")) || String(iriOf(a)).localeCompare(String(iriOf(b))));
  const by = Object.fromEntries(COLUMNS.map(([key]) => [key, []]));
  for (const it of sorted) by[column(it, sorted, now)].push(it);
  const tally = new Map();
  for (const it of sorted) for (const l of it.labels || []) tally.set(l, (tally.get(l) || 0) + 1);
  return {
    by,
    done: by.done.length,
    total: sorted.length,
    tally: [...tally].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
  };
}
