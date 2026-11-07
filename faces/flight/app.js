// The flight plan: a record item's children and `related` items are a plan's steps; list them
// in the order they can land (topological by `blocks`, ties by priority then number) and mark
// the first step that can land now. The record is read in the ledger the address names
// (`?ledger=`); each step is read by its IRI from its own ledger, and shown as `{ledger}#{number}`.
import { el, item, labelOf, paths, stateOf, announce, query, setQuery } from "./kit.js";
import { plan, readSteps } from "./plan.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");

const WORDS = {
  done: "done",
  awaiting: "handed back",
  claimed: "claimed",
  expired: "claimed, lease expired",
  open: "open",
};

async function show(ledger, number) {
  announce(`Reading record ${number}…`);
  out.replaceChildren();
  try {
    const record = await item(ledger, number);
    const items = await readSteps(record);
    // No viewer clock: each step's lease is judged by the door's time for its own answer.
    const { order, cycle, next } = plan(items);
    const row = (it, extra) =>
      el(
        "li",
        { class: it === next ? "next" : null },
        it === next ? el("strong", {}, "Next to land: ") : null,
        el("a", { href: paths.itemPage(it.ledger, it.number) }, labelOf(it)),
        ` ${it.title || ""} `,
        el("span", { class: stateOf(it) === "expired" ? "warn" : "muted" }, `(${WORDS[stateOf(it)]}${it.priority != null ? `, priority ${it.priority}` : ""}${extra || ""})`),
      );
    out.append(
      el("h2", {}, el("a", { href: paths.itemPage(ledger, number) }, `${ledger}#${number}`), ` ${record.title || ""}`),
      el("h3", {}, `Landing order (${order.length} steps)`),
      order.length ? el("ol", { class: "items" }, order.map((it) => row(it))) : el("p", { class: "muted" }, "No steps."),
      cycle.length ? el("h3", {}, "In a blocking cycle (cannot land until it is broken)") : null,
      cycle.length ? el("ul", { class: "items" }, cycle.map((it) => row(it, ", in a cycle"))) : null,
    );
    announce(next ? `Next to land: ${labelOf(next)}.` : "No step can land now.");
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
