// The project view: projects are declared in projects.json (a name to a set of ledgers); show
// the open items across a project's ledgers, filtered by `labels` (all of them) and `without`
// (none of them), both carried in the address so a bookmark is a saved view.
//
// One listing per ledger, at most a few at once. ikigai-ledger's `ledgers=` union (ledger PR 88)
// answers all of them in one read once it is released, and replaces this loop.
import { el, read, listAll, paths, pool, stateOf, announce, query, setQuery } from "./kit.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");
const split = (s) => (s || "").split(",").map((w) => w.trim()).filter(Boolean);

function counts(title, entries, link) {
  return [
    el("h3", {}, title),
    el(
      "ul",
      { class: "tags" },
      [...entries]
        .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
        .map(([k, n]) => el("li", {}, link ? link(k) : String(k), ` ${n}`)),
    ),
  ];
}

async function show(projects, name, labels, without) {
  out.replaceChildren();
  const ledgers = projects[name] || [];
  if (!ledgers.length) {
    out.append(el("p", { class: "warn" }, `No project named ${name} in projects.json.`));
    return;
  }
  announce(`Reading ${ledgers.length} ledgers…`);
  const lists = await pool(ledgers, (ledger) =>
    listAll(ledger, { status: "open", labels: labels.join(",") }).then((items) => items.map((it) => ({ ...it, ledger }))),
  );
  const failed = ledgers.filter((_, i) => lists[i].error);
  const items = lists
    .filter((l) => !l.error)
    .flat()
    .filter((it) => (it.status || "open") === "open")
    .filter((it) => labels.every((l) => (it.labels || []).includes(l)))
    .filter((it) => !without.some((l) => (it.labels || []).includes(l)))
    .sort((a, b) => (a.priority ?? Infinity) - (b.priority ?? Infinity) || a.ledger.localeCompare(b.ledger) || a.number - b.number);
  const byLabel = new Map();
  const byPriority = new Map();
  const byLedger = new Map(ledgers.map((l) => [l, 0]));
  for (const it of items) {
    for (const l of it.labels || []) byLabel.set(l, (byLabel.get(l) || 0) + 1);
    const p = it.priority ?? "none";
    byPriority.set(p, (byPriority.get(p) || 0) + 1);
    byLedger.set(it.ledger, byLedger.get(it.ledger) + 1);
  }
  out.append(
    el("h2", {}, `${name}: ${items.length} open items`),
    failed.length ? el("p", { class: "warn" }, `Could not read: ${failed.join(", ")}.`) : null,
    ...counts("By ledger", byLedger, (l) => el("a", { href: paths.labelPage(l, labels) }, l)),
    ...counts("By priority", byPriority),
    ...counts("By label", byLabel),
    el("h3", {}, "Items"),
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, ["Item", "Title", "Priority", "State", "Labels"].map((h) => el("th", { scope: "col" }, h)))),
        el(
          "tbody",
          {},
          items.map((it) =>
            el(
              "tr",
              {},
              el("td", {}, el("a", { href: paths.itemPage(it.ledger, it.number) }, `${it.ledger}#${it.number}`)),
              el("td", {}, it.title || ""),
              el("td", {}, it.priority ?? "none"),
              el("td", {}, stateOf(it)),
              el("td", {}, (it.labels || []).join(", ")),
            ),
          ),
        ),
      ),
    ),
  );
  announce(`${items.length} open items in ${name}.`);
}

async function start() {
  let projects;
  try {
    projects = await read(new URL("projects.json", location.href).pathname);
  } catch (error) {
    out.append(el("p", { class: "warn" }, `Could not read projects.json: ${error.message}`));
    return;
  }
  for (const name of Object.keys(projects)) form.project.append(el("option", { value: name }, name));
  const q = query();
  if (q.get("project")) form.project.value = q.get("project");
  form.labels.value = q.get("labels") || "";
  form.without.value = q.get("without") || "";
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    setQuery({ project: form.project.value, labels: form.labels.value.trim(), without: form.without.value.trim() });
    show(projects, form.project.value, split(form.labels.value), split(form.without.value));
  });
  if (q.get("project")) show(projects, form.project.value, split(form.labels.value), split(form.without.value));
}
start();
