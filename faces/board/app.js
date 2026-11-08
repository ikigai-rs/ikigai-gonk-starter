// The epic board: which of an epic's children can be worked now, and how far along it is.
// The epic is read in the ledger the address names (`?ledger=`); each child is read by its IRI
// from its own ledger, and shown as `{ledger}#{number}`. The picker offers the ledger's open items
// carrying one of the address's `?kinds=` (default epic); the number input still reaches any item.
import { append, el, item, labelOf, paths, stateOf, announce, query, setQuery, unreadableNote, unreadableWords } from "./kit.js";
import { COLUMNS, board, optionText, parseKinds, pickerWords, readChildren, readPicker } from "./board.js";

const form = document.getElementById("pick");
const out = document.getElementById("out");
const picker = form.picked;
let kinds = ["epic"];
let pickerNote = "";
// Each newer read makes an older one's answer stale: a keyboard walks a <select> faster than
// the reads answer, and two answers would otherwise both write the page.
let showing = 0;
let filling = 0;

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
  const mine = ++showing;
  const say = (text) => announce(pickerNote ? `${text} ${pickerNote}` : text);
  say(`Reading item ${number}…`);
  out.replaceChildren();
  try {
    const epic = await item(ledger, number);
    const { items: kids, unreadable } = await readChildren(epic);
    if (mine !== showing) return;
    // No viewer clock: each child's lease is judged by the door's time for its own answer.
    const { by, done, tally } = board(kids, undefined, kinds);
    append(out,
      el("h2", {}, el("a", { href: paths.itemPage(ledger, number) }, `${ledger}#${number}`), ` ${epic.title || ""}`),
      unreadableNote(unreadable, "child item", "child items"),
      el("p", {}, el("label", {}, `${done} of ${kids.length} done `, el("progress", { max: kids.length || 1, value: done }))),
      el(
        "div",
        { class: "columns" },
        COLUMNS.map(([key, name]) =>
          el(
            "section",
            { "aria-labelledby": `col-${key}`, class: `column-${key}` },
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
    say(`Item ${number}: ${kids.length} children, ${done} done.${missing ? ` ${missing}` : ""}`);
  } catch (error) {
    if (mine !== showing) return;
    append(out, el("p", { class: "warn" }, `Could not read the item: ${error.message}`));
    say(`Could not read item ${number}.`);
  }
}

// Fill the picker for `ledger`; answers what it offers, or null when a newer fill replaced this
// one. The picker shows the number input's item when it offers it, and nothing when it does not.
async function fillPicker(ledger) {
  const mine = ++filling;
  const answer = await readPicker(ledger, kinds);
  if (mine !== filling) return null;
  const { entries } = answer;
  picker.replaceChildren(...(entries.length
    ? entries.map((e) => el("option", { value: e.number }, optionText(e, ledger, kinds)))
    : [el("option", { value: "" }, "None open")]));
  picker.disabled = !entries.length;
  picker.value = form.epic.value.trim();
  pickerNote = pickerWords(kinds, answer);
  return entries;
}

// Show the form's item and put it in the address, with the ledger and kinds: a saved board.
function showForm() {
  const ledger = form.ledger.value.trim() || "default";
  const number = form.epic.value.trim();
  setQuery({ ledger, kinds: kinds.join(","), epic: number });
  picker.value = number;
  show(ledger, number);
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  showForm();
});
picker.addEventListener("change", () => {
  if (!picker.value) return;
  form.epic.value = picker.value;
  showForm();
});
// A new ledger refills the picker and leaves the board and the number alone: this fires when
// focus leaves the field, and the person may be on their way to type a number.
form.ledger.addEventListener("change", async () => {
  await fillPicker(form.ledger.value.trim() || "default");
  if (pickerNote) announce(pickerNote);
});

async function start() {
  const q = query();
  kinds = parseKinds(q.get("kinds"));
  form.ledger.value = q.get("ledger") || "default";
  form.epic.value = q.get("epic") || "";
  const offered = await fillPicker(form.ledger.value);
  if (!form.epic.value && offered && offered.length) {
    form.epic.value = String(offered[0].number);
    picker.value = form.epic.value;
  }
  if (form.epic.value) show(form.ledger.value, form.epic.value);
  else if (pickerNote) announce(pickerNote);
}
start();
