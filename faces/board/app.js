// The epic board: which of an epic's children can be worked now, and how far along it is.
import { el, item, paths, pool, stateOf, announce, query, setQuery } from "./kit.js";
import { COLUMNS, board, childNumbers } from "./board.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");

// The epic's children, from the epic's own `item.children` (closed ones too), each read once.
async function children(ledger, epic) {
  const items = await pool(childNumbers(epic), (n) => item(ledger, n));
  return items.filter((it) => it && !it.error);
}

function card(ledger, it, now) {
  const state = stateOf(it, now);
  const holder = it.claim && it.claim.holder;
  const note = holder ? ` (${holder}${state === "expired" ? ", lease expired" : ""})` : "";
  return el(
    "li",
    {},
    el("a", { href: paths.itemPage(ledger, it.number) }, `${ledger}#${it.number}`),
    ` ${it.title || ""}`,
    note ? el("span", { class: state === "expired" ? "warn" : "muted" }, note) : null,
  );
}

async function show(ledger, number) {
  announce(`Reading epic ${number}…`);
  out.replaceChildren();
  try {
    const epic = await item(ledger, number);
    const kids = await children(ledger, epic);
    const now = Date.now();
    const { by, done, tally } = board(kids, now);
    out.append(
      el("h2", {}, el("a", { href: paths.itemPage(ledger, number) }, `${ledger}#${number}`), ` ${epic.title || ""}`),
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
              ? el("ul", { class: "items" }, by[key].map((it) => card(ledger, it, now)))
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
    announce(`Epic ${number}: ${kids.length} children, ${done} done.`);
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
