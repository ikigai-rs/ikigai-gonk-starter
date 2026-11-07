// The board's and the flight plan's logic against recorded items. tests/fixtures/faces/plan.json
// is an epic whose JSON face carries `item.children` (one of them closed) and links of each
// type; tests/fixtures/faces/cross-ledger.json is a record whose steps are items of two OTHER
// ledgers, their link targets carrying `number: null` and IRIs ending in an opaque id, two of
// them sharing a number.
// Run by tests/test_faces.py with Deno (the gates provision it): deno test tests/faces/
import fixture from "../fixtures/faces/plan.json" with { type: "json" };
import cross from "../fixtures/faces/cross-ledger.json" with { type: "json" };
import { board, childTargets, readChildren } from "../../faces/board/board.js";
import { plan, readSteps, stepTargets } from "../../faces/flight/plan.js";
import { answeredAt, children, item, labelOf, listAll, paths, refusal, stateOf, unreadableWords } from "../../faces/template/kit.js";

const now = Date.parse(fixture.now);
const record = fixture.record.item;
const byIri = new Map(Object.values(fixture.members).map((d) => [d.item.iri, d.item]));
const member = (iri) => byIri.get(iri);
const numbers = (items) => items.map((it) => it.number);
const labels = (items) => items.map(labelOf);

function equal(actual, expected, what) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`${what}: got ${a}, expected ${e}`);
}

Deno.test("children come from item.children, the closed one included", () => {
  equal(children(record), [41, 42, 43, 44, 46], "children");
  equal(childTargets(record).map((iri) => member(iri).number), [41, 42, 43, 44, 46], "board's children");
});

Deno.test("the board puts the closed child in Done and counts it", () => {
  const b = board(childTargets(record).map(member), now);
  equal(numbers(b.by.done), [41], "done");
  equal(numbers(b.by.blocked), [42], "blocked (43 is open and blocks it)");
  equal(numbers(b.by.flight), [43], "in flight");
  equal(numbers(b.by.awaiting), [44], "awaiting (awaiting: reviewer, not only dispatcher)");
  equal(numbers(b.by.ready), [46], "ready");
  equal([b.done, b.total], [1, 5], "progress");
  equal(b.tally[0], ["format", 3], "label tally");
});

Deno.test("flight keeps the closed child as a landed step, with the related item", () => {
  equal(stepTargets(record).map((iri) => member(iri).number).sort((a, b) => a - b), [41, 42, 43, 44, 45, 46], "steps");
  const p = plan(stepTargets(record).map(member), now);
  equal(numbers(p.order), [41, 43, 42, 44, 45, 46], "landing order");
  equal(stateOf(p.order[0], now), "done", "the landed step");
  equal(p.cycle, [], "no cycle");
  equal(p.next && p.next.number, 43, "next to land (its only blocker, 41, has landed)");
});

Deno.test("an expired lease reads as expired; any awaiting as awaiting", () => {
  equal(stateOf(fixture.members["43"].item, Date.parse("2026-11-06T14:00:00Z")), "expired", "expired");
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

// The steps in other ledgers, served as gonk answers each IRI's mechanical path, every path
// asked recorded. A path for the record's ledger (`runs`) answers 404: no step lives there.
// `refuse` maps an IRI to the status its path answers instead (403 for a ledger the viewer
// holds no read grant for, 404 for an item that is not there).
async function serveCross(run, refuse = {}) {
  globalThis.location = { origin: "http://gonk.test" };
  const realFetch = globalThis.fetch;
  const asked = [];
  const byPath = new Map(Object.entries(cross.members).map(([iri, doc]) => [paths.itemByIri(iri), doc]));
  globalThis.fetch = (url) => {
    const path = new URL(url).pathname;
    asked.push(path);
    const refused = Object.entries(refuse).find(([iri]) => paths.itemByIri(iri) === path);
    if (refused) return Promise.resolve(new Response(JSON.stringify({ error: "refused", title: "SECRET" }), { status: refused[1] }));
    const doc = byPath.get(path);
    return Promise.resolve(doc ? new Response(JSON.stringify(doc)) : new Response("{}", { status: 404 }));
  };
  try {
    await run(asked);
  } finally {
    globalThis.fetch = realFetch;
  }
}

Deno.test("an item IRI reads by the mechanical rule, in its own ledger; anything else is refused", () => {
  equal(paths.itemByIri("urn:iki:ledger:alpha:item:01M6SJ5GCY0000000000000A03"), "/iki/ledger/alpha/item/01M6SJ5GCY0000000000000A03", "named");
  equal(paths.itemByIri("urn:iki:ledger:item:12"), "/iki/ledger/item/12", "default ledger");
  for (const bad of ["https://evil.test/x", "urn:iki:ledger:alpha:comment:1", "urn:other:item:1", "", null, "urn:iki:ledger::item:1"]) {
    let threw = false;
    try {
      paths.itemByIri(bad);
    } catch {
      threw = true;
    }
    equal(threw, true, `refuses ${bad}`);
  }
});

Deno.test("flight: a record whose steps are in two other ledgers reads each from its own", async () => {
  const rec = cross.record.item;
  equal(stepTargets(rec).length, 4, "no step dropped for `number: null`");
  await serveCross(async (asked) => {
    const { items: steps, unreadable } = await readSteps(rec);
    equal(unreadable, [], "every step read");
    equal(labels(steps).sort(), ["alpha#3", "alpha#5", "beta#1", "beta#3"], "every step, by its own ledger");
    equal(asked.filter((p) => !p.startsWith("/iki/ledger/alpha/") && !p.startsWith("/iki/ledger/beta/")), [], "nothing read in the record's ledger");
    const p = plan(steps, Date.parse(cross.now));
    // beta#1 blocks alpha#3, which blocks alpha#5; beta#3 shares alpha#3's number and is blocked by nothing.
    equal(labels(p.order), ["beta#3", "beta#1", "alpha#3", "alpha#5"], "landing order by `blocks`, keyed by IRI");
    equal(p.cycle, [], "no cycle");
    equal(p.next && labelOf(p.next), "beta#3", "next to land: not held back by the edge into alpha#3");
  });
});

Deno.test("board: siblings that share a number across ledgers are told apart", async () => {
  await serveCross(async () => {
    const { items: kids } = await readChildren(cross.record.item);
    equal(labels(kids), ["alpha#3", "beta#3", "alpha#5"], "the epic's children, each from its own ledger");
    const all = Object.values(cross.members).map((d) => ({ ...d.item, ledger: d.ledger }));
    const b = board(all, Date.parse(cross.now));
    equal(labels(b.by.blocked), ["alpha#3", "alpha#5"], "blocked: only alpha#3 (by beta#1) and alpha#5 (by alpha#3)");
    equal(labels(b.by.ready), ["beta#1", "beta#3"], "ready: beta#3, though beta#1 blocks a #3 elsewhere");
  });
});

const B03 = "urn:iki:ledger:beta:item:01M6SJ5GCY0000000000000B03";
const A05 = "urn:iki:ledger:alpha:item:01M6SJ5GCY0000000000000A05";

Deno.test("flight: steps that answer 403 and 404 are counted by reason, and the rest still order", async () => {
  await serveCross(async () => {
    const { items: steps, unreadable } = await readSteps(cross.record.item);
    equal(labels(steps).sort(), ["alpha#3", "beta#1"], "the steps that could be read");
    equal(unreadable, [{ iri: B03, reason: "denied" }, { iri: A05, reason: "not found" }], "the unreadable, by IRI and reason");
    const p = plan(steps, Date.parse(cross.now));
    equal(labels(p.order), ["beta#1", "alpha#3"], "landing order of the rest, by `blocks`");
    equal(p.next && labelOf(p.next), "beta#1", "next to land");
    const words = unreadableWords(unreadable, "step", "steps");
    equal(words, "2 steps could not be read (1 denied, 1 not found): you may not have access to their ledger.", "said in words");
    equal(JSON.stringify(unreadable).includes("SECRET"), false, "nothing from the refusing ledger's answer");
  }, { [B03]: 403, [A05]: 404 });
});

Deno.test("board: children that answer 403 and 404 are counted by reason", async () => {
  await serveCross(async () => {
    const { items: kids, unreadable } = await readChildren(cross.record.item);
    equal(labels(kids), ["alpha#3"], "the child that could be read");
    equal(unreadable.map((u) => u.reason), ["denied", "not found"], "reasons, in the epic's order");
    equal(unreadableWords(unreadable, "child item", "child items"),
      "2 child items could not be read (1 denied, 1 not found): you may not have access to their ledger.", "said in words");
  }, { [B03]: 403, [A05]: 404 });
});

Deno.test("unreadable words: one, grouped, other failures, and none", () => {
  equal(unreadableWords([], "step", "steps"), "", "nothing to say");
  equal(unreadableWords([{ iri: "x", reason: "denied" }], "step", "steps"),
    "1 step could not be read (denied): you may not have access to its ledger.", "one");
  equal(unreadableWords([{ iri: "x", reason: "denied" }, { iri: "y", reason: "denied" }, { iri: "z", reason: "failed" }], "step", "steps"),
    "3 steps could not be read (2 denied, 1 failed): you may not have access to their ledger.", "grouped");
  equal(refusal(Object.assign(new Error("x"), { status: 401 })), "denied", "401 is denied");
  equal(refusal(Object.assign(new Error("x"), { status: 500 })), "failed", "500 failed");
  equal(refusal(undefined), "failed", "no item in the answer");
});
