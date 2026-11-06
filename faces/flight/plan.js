// The flight plan's logic, apart from the page: which items are steps, in what order they
// land, and which lands next. No DOM here, so tests/faces/logic_test.js runs it against a
// recorded item.
import { children, linked, stateOf } from "./kit.js";

// A plan's steps: the record's `item.children` (closed ones too: a landed step stays a step)
// and the targets of its `related` links. One read of the record, never a scan of the ledger.
export function stepNumbers(record) {
  const numbers = new Set([...children(record), ...linked(record, "related")]);
  numbers.delete(record.number);
  return [...numbers];
}

// Lower priority number first; no priority last; then the item number.
function rank(a, b) {
  const pa = a.priority ?? Infinity;
  const pb = b.priority ?? Infinity;
  return pa - pb || a.number - b.number;
}

// Kahn's algorithm over `blocks` within the plan; a cycle's members follow, ranked, apart.
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

// Whether a step can land now: open, not handed back, and nothing open in the plan blocks it.
export function canLand(it, items, now = Date.now()) {
  const state = stateOf(it, now);
  if (state === "done" || state === "awaiting") return false;
  return !items.some((s) => s !== it && stateOf(s, now) !== "done" && linked(s, "blocks").includes(it.number));
}

// The plan for the steps read: the landing order, the cycle, and the next step to land.
export function plan(items, now = Date.now()) {
  const { order, cycle } = landingOrder(items);
  return { order, cycle, next: order.find((it) => canLand(it, items, now)) || null };
}
