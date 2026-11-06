// The board's and the flight plan's logic against a recorded item (tests/fixtures/faces/plan.json):
// an epic whose JSON face carries `item.children` (one of them closed) and links of each type.
// Run by tests/test_faces.py with Deno (the gates provision it): deno test tests/faces/
import fixture from "../fixtures/faces/plan.json" with { type: "json" };
import { board, childNumbers } from "../../faces/board/board.js";
import { plan, stepNumbers } from "../../faces/flight/plan.js";
import { children, listAll, stateOf } from "../../faces/template/kit.js";

const now = Date.parse(fixture.now);
const record = fixture.record.item;
const member = (n) => fixture.members[String(n)].item;
const numbers = (items) => items.map((it) => it.number);

function equal(actual, expected, what) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: got ${a}, expected ${e}`);
}

Deno.test("children come from item.children, the closed one included", () => {
  equal(children(record), [41, 42, 43, 44, 46], "children");
  equal(childNumbers(record), [41, 42, 43, 44, 46], "board's children");
});

Deno.test("the board puts the closed child in Done and counts it", () => {
  const b = board(childNumbers(record).map(member), now);
  equal(numbers(b.by.done), [41], "done");
  equal(numbers(b.by.blocked), [42], "blocked (43 is open and blocks it)");
  equal(numbers(b.by.flight), [43], "in flight");
  equal(numbers(b.by.awaiting), [44], "awaiting (awaiting: reviewer, not only dispatcher)");
  equal(numbers(b.by.ready), [46], "ready");
  equal([b.done, b.total], [1, 5], "progress");
  equal(b.tally[0], ["format", 3], "label tally");
});

Deno.test("flight keeps the closed child as a landed step, with the related item", () => {
  equal(stepNumbers(record).sort((a, b) => a - b), [41, 42, 43, 44, 45, 46], "steps");
  const p = plan(stepNumbers(record).map(member), now);
  equal(numbers(p.order), [41, 43, 42, 44, 45, 46], "landing order");
  equal(stateOf(p.order[0], now), "done", "the landed step");
  equal(p.cycle, [], "no cycle");
  equal(p.next && p.next.number, 43, "next to land (its only blocker, 41, has landed)");
});

Deno.test("an expired lease reads as expired; any awaiting as awaiting", () => {
  equal(stateOf(member(43), Date.parse("2026-11-06T14:00:00Z")), "expired", "expired");
  equal(stateOf({ status: "open", awaiting: "dispatcher" }), "awaiting", "dispatcher");
  equal(stateOf({ status: "open", awaiting: null, claim: null }), "open", "open");
});

Deno.test("listAll pages by total, not by a short page", async () => {
  const seen = [];
  const pages = { 0: [1, 2], 2: [3, 4], 4: [5] };
  globalThis.location = { origin: "http://gonk.test" };
  const realFetch = globalThis.fetch;
  globalThis.fetch = (url) => {
    const offset = Number(new URL(url).searchParams.get("offset"));
    seen.push(offset);
    const items = (pages[offset] || []).map((number) => ({ number }));
    return Promise.resolve(new Response(JSON.stringify({ count: items.length, total: 5, offset, items })));
  };
  try {
    // A page size larger than gonk's page: every page is "short", and only `total` stops it.
    const all = await listAll("example", {}, 200);
    equal(numbers(all), [1, 2, 3, 4, 5], "every item");
    equal(seen, [0, 2, 4], "offsets asked");
  } finally {
    globalThis.fetch = realFetch;
  }
});
