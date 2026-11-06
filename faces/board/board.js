// The board's logic, apart from the page: which column each of an epic's children goes in.
// No DOM here, so tests/faces/logic_test.js runs it against a recorded item.
import { children, linked, stateOf } from "./kit.js";

export const COLUMNS = [
  ["blocked", "Blocked by an open sibling"],
  ["ready", "Ready"],
  ["flight", "In flight"],
  ["awaiting", "Awaiting (handed back)"],
  ["done", "Done"],
];

// The numbers of an epic's children: `item.children` of its JSON face, closed ones included.
export function childNumbers(epic) {
  return children(epic).filter((n) => n !== epic.number);
}

// Which column: done and awaiting are the ledger's own state; a claim is in flight; an open
// unclaimed item is blocked while an open sibling `blocks` it, and ready otherwise.
export function column(it, siblings, now = Date.now()) {
  const state = stateOf(it, now);
  if (state === "done" || state === "awaiting") return state;
  if (state === "claimed" || state === "expired") return "flight";
  const blocked = siblings.some((s) => s !== it && stateOf(s, now) !== "done" && linked(s, "blocks").includes(it.number));
  return blocked ? "blocked" : "ready";
}

// The whole board for the children read: each column's items (by number), how many are done,
// and the label tally ([label, count], most first).
export function board(kids, now = Date.now()) {
  const sorted = [...kids].sort((a, b) => a.number - b.number);
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
