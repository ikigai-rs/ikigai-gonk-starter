// The epic board: which of an epic's children can be worked now, and how far along it is.
import { el, item, listAll, paths, pool, linked, numberOf, stateOf, announce, query, setQuery } from "./kit.js";

const COLUMNS = [
  ["blocked", "Blocked by an open sibling"],
  ["ready", "Ready"],
  ["flight", "In flight"],
  ["awaiting", "Awaiting the dispatcher"],
  ["done", "Done"],
];
const form = document.getElementById("pick");
const out = document.getElementById("out");

// The epic's children: what it links to as a child, and what links to it as its parent.
async function children(ledger, epic) {
  const numbers = new Set(linked(epic, "child"));
  const all = await listAll(ledger, {}).catch(() => []);
  for (const it of all) {
    if (linked(it, "parent").includes(epic.number)) numbers.add(it.number);
  }
  const items = await pool([...numbers], (n) => item(ledger, n));
  return items.filter((it) => it && !it.error);
}

// Which column: done and awaiting are the ledger's own state; a claim is in flight; an open
// unclaimed item is blocked while an open sibling `blocks` it, and ready otherwise.
function column(it, siblings, now) {
  const state = stateOf(it, now);
  if (state === "done" || state === "awaiting") return state;
  if (state === "claimed" || state === "expired") return "flight";
  const blocked = siblings.some((s) => stateOf(s, now) !== "done" && linked(s, "blocks").includes(it.number));
  return blocked ? "blocked" : "ready";
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
    const kids = (await children(ledger, epic)).sort((a, b) => a.number - b.number);
    const now = Date.now();
    const by = Object.fromEntries(COLUMNS.map(([key]) => [key, []]));
    for (const it of kids) by[column(it, kids, now)].push(it);
    const done = by.done.length;
    const tally = new Map();
    for (const it of kids) for (const l of it.labels || []) tally.set(l, (tally.get(l) || 0) + 1);
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
        [...tally]
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
          .map(([l, n]) => el("li", {}, el("a", { href: paths.labelPage(ledger, l) }, l), ` ${n}`)),
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
