// The epic board: which of an epic's children can be worked now, and how far along it is.
// The epic is read in the ledger the address names (`?ledger=`); each child is read by its IRI
// from its own ledger, and shown as `{ledger}#{number}`.
import { el, item, labelOf, paths, stateOf, announce, query, setQuery, unreadableNote, unreadableWords } from "./kit.js";
import { COLUMNS, board, readChildren } from "./board.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");

function card(it) {
  const state = stateOf(it);
  const holder = it.claim && it.claim.holder;
  const note = holder ? ` (${holder}${state === "expired" ? ", lease expired" : ""})` : "";
  return el(
    "li",
    {},
    el("a", { href: paths.itemPage(it.ledger, it.number) }, labelOf(it)),
    ` ${it.title || ""}`,
    note ? el("span", { class: state === "expired" ? "warn" : "muted" }, note) : null,
  );
}

async function show(ledger, number) {
  announce(`Reading epic ${number}…`);
  out.replaceChildren();
  try {
    const epic = await item(ledger, number);
    const { items: kids, unreadable } = await readChildren(epic);
    // No viewer clock: each child's lease is judged by the door's time for its own answer.
    const { by, done, tally } = board(kids);
    out.append(
      el("h2", {}, el("a", { href: paths.itemPage(ledger, number) }, `${ledger}#${number}`), ` ${epic.title || ""}`),
      unreadableNote(unreadable, "child item", "child items"),
      el("p", {}, el("label", {}, `${done} of ${kids.length} done `, el("progress", { max: kids.length || 1, value: done }))),
      el(
        "div",
        { class: "columns" },
        COLUMNS.map(([key, name]) =>
          el(
            "section",
            { "aria-labelledby": `col-${key}` },
            el("h3", { id: `col-${key}` }, `${name} (${by[key].length})`),
            by[key].length
              ? el("ul", { class: "items" }, by[key].map((it) => card(it)))
              : el("p", { class: "muted" }, "None."),
          ),
        ),
      ),
      el("h2", {}, "Labels"),
      el(
        "ul",
        { class: "tags" },
        tally.map(([l, n]) => el("li", {}, el("a", { href: paths.labelPage(ledger, l) }, l), ` ${n}`)),
      ),
    );
    const missing = unreadableWords(unreadable, "child item", "child items");
    announce(`Epic ${number}: ${kids.length} children, ${done} done.${missing ? ` ${missing}` : ""}`);
  } catch (error) {
    out.append(el("p", { class: "warn" }, `Could not read the epic: ${error.message}`));
    announce(`Could not read epic ${number}.`);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const ledger = form.ledger.value.trim() || "default";
  setQuery({ ledger, epic: form.epic.value.trim() });
  show(ledger, form.epic.value.trim());
});
const q = query();
if (q.get("epic")) {
  form.ledger.value = q.get("ledger") || "default";
  form.epic.value = q.get("epic");
  show(form.ledger.value, form.epic.value);
}
