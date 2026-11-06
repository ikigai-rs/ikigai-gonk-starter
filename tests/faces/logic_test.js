// The board's and the flight plan's logic against a recorded item (tests/fixtures/faces/plan.json):
// an epic whose JSON face carries `item.children` (one of them closed) and links of each type.
// Run by tests/test_faces.py with Deno (the gates provision it): deno test tests/faces/
import fixture from "../fixtures/faces/plan.json" with { type: "json" };
import { board, childNumbers } from "../../faces/board/board.js";
import { plan, stepNumbers } from "../../faces/flight/plan.js";
import { answeredAt, children, item, listAll, stateOf } from "../../faces/template/kit.js";

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

// A recorded answer for one item, its Date header the door's clock, under a viewer clock set
// apart from it: stateOf must judge the lease by the door, not by the viewer.
async function underClocks(doorDate, viewerTime, body, run) {
  globalThis.location = { origin: "http://gonk.test" };
  const realFetch = globalThis.fetch;
  const realNow = Date.now;
  globalThis.fetch = (url) => {
    const headers = { "Content-Type": "application/json" };
    if (doorDate) headers.Date = doorDate;
    return Promise.resolve(new Response(JSON.stringify(body(new URL(url))), { headers }));
  };
  Date.now = () => viewerTime;
  try {
    await run();
  } finally {
    globalThis.fetch = realFetch;
    Date.now = realNow;
  }
}

const leased = (number) => ({
  number,
  status: "open",
  awaiting: null,
  claim: { holder: "worker-a", kind: "lease", expires: "2026-11-06T12:00:00Z" },
});

Deno.test("a lease is live while the door's answer is before it expires, whatever the viewer's clock", async () => {
  // The door answered at 11:00; the viewer's clock says 13:00, after the lease ran out.
  await underClocks("Fri, 06 Nov 2026 11:00:00 GMT", Date.parse("2026-11-06T13:00:00Z"), () => ({ item: leased(7) }), async () => {
    const it = await item("default", 7);
    equal(answeredAt(it), Date.parse("2026-11-06T11:00:00Z"), "the door's time for the answer");
    equal(stateOf(it), "claimed", "live by the door's clock");
    equal(board([it]).by.flight.length, 1, "the board passes the door's time through");
    equal(plan([it]).next && plan([it]).next.number, 7, "flight passes it through");
  });
});

Deno.test("a lease is expired once the door's answer is after it, whatever the viewer's clock", async () => {
  // The door answered at 13:00; the viewer's clock says 11:00, before the lease ran out.
  await underClocks("Fri, 06 Nov 2026 13:00:00 GMT", Date.parse("2026-11-06T11:00:00Z"), () => ({ item: leased(7) }), async () => {
    equal(stateOf(await item("default", 7)), "expired", "expired by the door's clock");
  });
});

Deno.test("listAll stamps every item with its page's answer (the claims face)", async () => {
  await underClocks(
    "Fri, 06 Nov 2026 13:00:00 GMT",
    Date.parse("2026-11-06T11:00:00Z"),
    () => ({ count: 2, total: 2, offset: 0, items: [leased(1), leased(2)] }),
    async () => {
      const all = await listAll("default", { status: "open" });
      equal(all.map((it) => stateOf(it)), ["expired", "expired"], "each judged by the door");
    },
  );
});

Deno.test("with no Date header the viewer's clock is the fallback", async () => {
  await underClocks(null, Date.parse("2026-11-06T13:00:00Z"), () => ({ item: leased(7) }), async () => {
    const it = await item("default", 7);
    equal(answeredAt(it), null, "no door time recorded");
    equal(stateOf(it), "expired", "judged by the viewer's clock");
  });
});
