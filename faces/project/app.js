// The project view: projects are declared in projects.json (a name to a set of ledgers); show
// the open items across a project's ledgers, filtered by `labels` (all of them) and `without`
// (none of them), both carried in the address so a bookmark is a saved view.
//
// One listing per ledger, at most a few at once. ikigai-ledger's `ledgers=` union (ledger PR 88)
// answers all of them in one read once it is released, and replaces this loop.
import { append, chips, el, read, listAll, orderByCount, orderByPriority, paths, pool, stateOf, announce, query, setQuery } from "./kit.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");
const split = (s) => (s || "").split(",").map((w) => w.trim()).filter(Boolean);

function counts(title, entries, link) {
  return [el("h3", {}, title), chips(entries, link)];
}

const itemLink = (it) => el("a", { href: paths.itemPage(it.ledger, it.number) }, `${it.ledger}#${it.number}`);

// The item, priority and state columns stay on one line; a title or labels wrap between words.
function table(items) {
  const heads = [["Item", true], ["Title"], ["Priority", true], ["State", true], ["Labels"]];
  return el(
    "div",
    { class: "table-wrap wide-only" },
    el(
      "table",
      {},
      el("thead", {}, el("tr", {}, heads.map(([h, tight]) => el("th", { scope: "col", class: tight ? "nowrap" : null }, h)))),
      el(
        "tbody",
        {},
        items.map((it) =>
          el(
            "tr",
            {},
            el("td", { class: "nowrap" }, itemLink(it)),
            el("td", { class: "long-text" }, it.title || ""),
            el("td", { class: "nowrap" }, it.priority ?? "none"),
            el("td", { class: "nowrap" }, stateOf(it)),
            el("td", {}, (it.labels || []).join(", ")),
          ),
        ),
      ),
    ),
  );
}

// The same rows where a table does not fit: one card per item, each value under its name.
function cards(items) {
  const field = (name, value) => el("div", {}, el("dt", {}, name), el("dd", {}, value));
  return el(
    "ul",
    { class: "item-cards narrow-only", role: "list" },
    items.map((it) =>
      el(
        "li",
        { class: "item-card" },
        el(
          "dl",
          {},
          field("Item", itemLink(it)),
          field("Title", it.title || ""),
          field("Priority", it.priority ?? "none"),
          field("State", stateOf(it)),
          it.labels && it.labels.length ? field("Labels", it.labels.join(", ")) : null,
        ),
      ),
    ),
  );
}

async function show(projects, name, labels, without) {
  out.replaceChildren();
  const ledgers = projects[name] || [];
  if (!ledgers.length) {
    append(out, el("p", { class: "warn" }, `No project named ${name} in projects.json.`));
    return;
  }
  announce(`Reading ${ledgers.length} ledgers…`);
  // Each item is kept (not copied), so stateOf still judges it by the door's time for its answer.
  const lists = await pool(ledgers, (ledger) =>
    listAll(ledger, { status: "open", labels: labels.join(",") }).then((items) => items.map((it) => Object.assign(it, { ledger }))),
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
  append(out,
    el("h2", {}, `${name}: ${items.length} open items`),
    failed.length ? el("p", { class: "warn" }, `Could not read: ${failed.join(", ")}.`) : null,
    ...counts("By ledger", orderByCount(byLedger), (l) => el("a", { href: paths.labelPage(l, labels) }, l)),
    ...counts("By priority", orderByPriority(byPriority)),
    ...counts("By label", orderByCount(byLabel)),
    el("h3", {}, "Items"),
    table(items),
    cards(items),
  );
  announce(`${items.length} open items in ${name}.`);
}

async function start() {
  let projects;
  try {
    projects = await read(new URL("projects.json", location.href).pathname);
  } catch (error) {
    append(out, el("p", { class: "warn" }, `Could not read projects.json: ${error.message}`));
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
