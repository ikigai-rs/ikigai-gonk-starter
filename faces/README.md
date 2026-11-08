# Faces: a deployment's own pages

gonk serves a deployment's own static pages from its origin: one line in gonk's `config.toml`
per face,

```toml
gonk.face = "board /path/to/this/checkout/faces/board"
```

and the files of that directory are served under the door's default CSP, read-only, jailed to
that directory. A face reads the ledger only through gonk's existing JSON routes, as the person
viewing it, under their own session: it can show nothing that person could not already read.
Faces need an ikigai-gonk carrying them: **e08ac9f or later** (ikigai-gonk PR 295, ledger item
3199). This starter does not build or pin gonk itself; `init --face` prints that revision with
the line to add.

| Directory | The question it answers |
| --- | --- |
| `template/` | The smallest working face: read one item and show it. Copy this one. |
| `board/` | How far along is this epic, and which of its children can be worked now? |
| `flight/` | In what order can this plan's steps land, and which lands next? |
| `project/` | What is open across this project's ledgers, with these labels and not those? |
| `claims/` | Who holds what (expired leases first), and whose turn is each hand-back? |

## Adding one

```sh
python3 tools/gonk.py init --face board      # prints the gonk.face line for faces/board
python3 tools/new.py <dir> --face board      # a new repository carrying faces/, and the line
```

Add the printed line to gonk's `config.toml`, restart gonk, and open
`http://127.0.0.1:1062/face/name/<name>/` (the door gonk serves the face on), replacing
`<name>` with the configured face name, for example `board`. Keep the trailing slash so
relative script and style paths resolve inside the face's directory. The face's form takes
the ledger and item; the address carries them, so a bookmark reopens the same view.

## What a face may do, and may not

- **May**: serve `.html`, `.css`, `.js`, `.mjs`, `.json`, `.jsonld`, `.ttl`, `.txt`, `.svg`,
  `.png`, `.jpg`, `.webp`, `.ico` and `.woff2` from its own directory; `fetch` gonk's JSON
  routes on the same origin with `Accept: application/json`; keep its state in the query
  string.
- **May not**: reach a file outside its directory (so each face carries its own copy of
  `kit.js` and `app.css`, and `tests/test_faces.py` keeps the copies identical to
  `template/`); write anything (no sink, no claim, no comment: a face is for looking); load
  a script or style from another origin; read anything the viewer could not.

The served types match `MEDIA_TYPES` in
[gonk's `src/face.rs` at e08ac9f](https://github.com/ikigai-rs/ikigai-gonk/blob/e08ac9f/src/face.rs).
Markdown READMEs document the source directories; gonk refuses to serve them.
`tests/test_faces.py` checks that the list above matches its allowed extensions.

## The CSP rules

The door's default CSP is `script-src 'self'` with no `'unsafe-inline'` for scripts or styles:

- No inline `<script>` and no `on…=` handler attribute: every script is a file,
  `<script type="module" src="app.js">`, and handlers are `addEventListener`.
- No `<style>` element and no `style=` attribute: every look is a class in `app.css`.
  `kit.el` refuses a `style` attribute rather than let the browser drop it silently.
- A bar is a `<progress>` or `<meter>` with its value, never a width set from script.

`tests/test_faces.py` checks the first two for every HTML file, and that every face asset has
an allowed extension (source `README.md` files are excluded).

## The frame and the theme are gonk's

gonk draws the frame around a face (ledger item 3279): a bar across the top of the page and its
own theme script, which sets `data-theme` on `<html>` from the viewer's choice. So a face carries
no breadcrumb, no link back to gonk, no theme chooser, and never reads `localStorage`.

`app.css` holds gonk's palettes under `[data-theme="light"]`, `"dark"` and `"high-contrast"`,
falling back to `prefers-color-scheme` and `prefers-contrast: more` when the attribute is
absent or `auto`, as on a gonk that does not draw the frame yet. Every color in a face is a token
from those blocks; `tests/test_faces.py` fails on one written anywhere else, and computes each
theme's contrast: 4.5:1 for text (7:1 in high contrast) and 3:1 for control borders, the focus
ring and the board's column colors. `--rule` is the decorative line, with no floor.

## kit.js

| Export | What it does |
| --- | --- |
| `el(tag, attrs, ...children)` | builds an element; text is always text, never markup |
| `append(node, ...children)` | puts children into an existing node (the page's output region), skipping `null`, `undefined` and `false` as `el` does; DOM `append(null)` would write "null" |
| `read(path, params)` | a same-origin GET with `Accept: application/json`; throws with the status |
| `paths.item / items` | gonk's mechanical JSON paths for an item and a listing, default or named ledger |
| `paths.itemPage / labelPage` | gonk's own pages, for links: an item, and `/l/{ledger}?labels=` |
| `item(ledger, n)` | one item's JSON face |
| `itemAt(iri)`, `paths.itemByIri` | one item's JSON face by its IRI, from its own ledger (the item carries `ledger`) |
| `iriOf`, `childIris(item)`, `linkedIris(item, type)` | an item's identity and what it points at, by IRI: key by these when items may span ledgers |
| `labelOf(item)` | `{ledger}#{number}`, in the item's own ledger |
| `listAll(ledger, params)` | every item of a listing (open only unless `status` says), paging by `total` |
| `pool(inputs, task, limit)` | runs tasks with at most `limit` in flight |
| `readEach(iris)`, `refusal(error)` | reads each IRI by `itemAt`; answers `{items, unreadable}`, each unreadable one as `{iri, reason}` (`denied`, `not found` or `failed`) |
| `unreadableWords(unreadable, one, many)`, `unreadableNote(...)` | says how many could not be read, grouped by reason, as a sentence and as the line under a heading (IRIs only, in a `<details>`) |
| `chips(entries, link)` | `[label, count]` entries as a list of chips, the label first (a link when `link` is given) and the count in its own element |
| `orderByCount`, `orderByPriority` | a count's entries most first and then by name; or 0 to 4 and then `none`, whatever the counts |
| `children(item)` | the NUMBERS of an item's children from `item.children` (one ledger only: a target elsewhere has `number: null` and is left out) |
| `linked(item, type)`, `stateOf(item)` | links by type (`blocks`, `parent`, `related`); open, claimed, expired, awaiting or done |
| `announce(text)` | says it in the page's polite `role=status` region |
| `query()`, `setQuery(params)` | the address as the face's state |

The paths follow gonk's mechanical rule (`docs/ui-conventions.md`, D10: path `/a/b/c` is IRI
`urn:a:b:c`): an item of the default ledger is `/iki/ledger/item/{n}`, of ledger `acme`
`/iki/ledger/acme/item/{n}`, and a listing `/iki/ledger/{ledger}/items`. If your gonk answers
them elsewhere, change `paths` in `template/kit.js` and copy it to every face.

The shapes kit.js reads: an item's JSON face is `{schema, ledger, item}`, where `item.children`
is `[{number, display, iri}]`, `item.links` is `[{type, target: {number, display, iri}}]` (types
`blocks`, `parent`, `related`; there is no `child` link), `item.claim` is null or
`{holder, kind, expires, lease, principal, purpose}`, `item.awaiting` is null or whose turn it
is (`"dispatcher"`, ...), and `item.status` is `open` or `closed`. A listing is
`{schema, ledger, count, total, offset, items, unreadable}`, and answers open items unless
asked otherwise: **find an item's members from its own face, never by listing the ledger.**
A member may be in another ledger: its target then carries `number: null` and an IRI ending in
an opaque id, so read it by that IRI (`itemAt`) and key members by IRI, never by number.
A member the viewer cannot read (403 for a ledger they hold no read grant for, 404) is never
left out silently: read members with `readEach`, and say what it could not read, under the
heading and in the `announce`, with `unreadableNote` and `unreadableWords`. Show nothing of an
unreadable member but its IRI.

## Checking one in a browser

1. Open the face, give it an item, and check that what it shows matches gonk's own page.
2. Open the developer console: a CSP violation is reported there, and must be none.
3. Tab through the page: every control and link is reached, in reading order, with a visible
   focus ring.
4. Narrow the window to 320 px: nothing scrolls sideways (a wide table scrolls in its own box).
5. Choose light, dark and high contrast in gonk's header, and switch the system between light
   and dark with gonk's choice on `auto`.
6. Run axe (or your browser's accessibility audit) and fix what it finds.

None of these has been run by this repository's CI: it has no browser. `ACCESSIBILITY.md`
says what has and has not been tested.
