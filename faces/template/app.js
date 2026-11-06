// The template face: read one item and show its title, state and labels. Copy this directory,
// rename it, and replace render() with the question your face answers.
import { el, item, paths, stateOf, announce, query, setQuery } from "./kit.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");

async function show(ledger, number) {
  announce(`Reading item ${number}…`);
  out.replaceChildren(el("h2", { id: "out-h" }, "Item"));
  try {
    const it = await item(ledger, number);
    out.append(
      el("p", {}, el("a", { href: paths.itemPage(ledger, number) }, `${ledger}#${number}`), " ", it.title || ""),
      el("p", {}, "State: ", stateOf(it)),
      el("ul", { class: "tags", "aria-label": "Labels" }, (it.labels || []).map((l) => el("li", {}, l))),
    );
    announce(`Item ${number} shown.`);
  } catch (error) {
    out.append(el("p", { class: "warn" }, `Could not read it: ${error.message}`));
    announce(`Could not read item ${number}.`);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const ledger = form.ledger.value.trim() || "default";
  const number = form.item.value.trim();
  setQuery({ ledger, item: number });
  show(ledger, number);
});

const q = query();
if (q.get("item")) {
  form.ledger.value = q.get("ledger") || "default";
  form.item.value = q.get("item");
  show(form.ledger.value, form.item.value);
}
