# ikigai UI conventions: the decision record

**Decided 2026-10-17: every decision below.** The shared pieces they call for (one escape-by-construction rendering path, the WebComponents, a reference app and a "building ikigai UIs" book) are not built yet, and existing apps migrate over time (D19). Write to the decisions, not to any one app's current code.

How to read it: the table is the whole record in one screen. Each section then gives the decision, why
(grounded in the inventory, with file:line where it helps), what it rules out, and its status. Line
numbers are as of 2026-10-17 and drift; the names they point at do not.

The one-sentence version: **a view is a resource**. It is composed (compose) or transformed (XSLT) from
other resources, transrepted into whatever face the client needs, delivered from a directory or compiled
in under the same IRIs, and served through one door whose security, escaping, history, theming and
accessibility behaviors are written once.

## The decisions at a glance

| # | Area | Decision | Rules out |
|---|---|---|---|
| D1 | Rendering | Hypermedia fragments and page shells are **compose templates** (`$h` for data) | Rust code assembling HTML strings per view |
| D2 | Rendering | Graph to page is **XSLT** over RDF/XML or SPARQL results, `xsl:value-of` only | `disable-output-escaping`; JSON-to-HTML in Rust |
| D3 | Rendering | One view model, many faces, by **transreption** (HTML, terminal, JSON-LD; GPUI later) | A second hand-built renderer per target |
| D4 | Rendering | Hand-written HTML only **through FND-H's module**, and a lint finds the rest | `format!`/`write!`/`push_str` HTML anywhere else |
| D5 | Escaping, URLs | FND-H's five functions, one rule set, shared vectors across Rust, XSL, Python, Deno | Per-repo escapers; unencoded values in URLs; unchecked `href` schemes |
| D6 | Doors | Every HTML door adopts one default: strict CSP, Origin admit, security headers, no inline script or style | A door without CSP; same-origin `text/html` from writable data |
| D7 | htmx | One vendored htmx served as a resource; `allowEval:false`, `allowScriptTags:false`; no global boost | CDN htmx; a second copy per repo; `hx-on`/`js:` evaluation |
| D8 | htmx | **Fragment and page are separate resources**; the page composes the fragment | Switching the representation on `HX-Request` |
| D9 | Live data | Live views listen to the **cli push stream** (SSE of golden-thread cuts) and refetch | Timer polling of cacheable fragments |
| D10 | URLs | URL path ⇄ resource IRI by the **mechanical rule**; a route table entry is the exception; every view a URL; Back works | Views without URLs; URLs that are not IRIs; pushing history for in-place actions |
| D11 | URLs | **One `/k` adapter**: `GET /k?c=…` for read verbs, `POST` for the rest | `/k/<cmd>` in a path; JS intercepts that fake it |
| D12 | Themes | **One token set** (gonk.css names) as a stylesheet resource; light, dark, high contrast, auto; contrast floor tested | Per-repo token names; hex colors outside the palette tables; untested palettes |
| D13 | a11y | **WCAG 2.2 AA** floor; landmarks, focus after swap, one status region, keyboard paths; **axe in CI** | Lost focus on swap; unnamed duplicate navs; placeholder-only labels |
| D14 | Errors | **One shell script** for refusals and failures (replaces gonk `denyAt` and ikigai-web `SHELL_JS`); refusals are statuses htmx can show | Errors parsed as markup; silent spinners; per-repo error handling |
| D15 | Forms | Forms from ArgSpecs; same-origin by Origin admit plus `SameSite=Strict`; the server validates | CSRF by trust; client-only validation; GET for a mutating verb |
| D16 | Components | **WebComponents** for ikigai things, themed by custom properties and `::part`, self-describing, fed with the page's authority or a scoped token | Ambient cookies on a third-party page; components that only work with JS |
| D17 | Artifacts | **UI artifacts are resources**, delivered from a directory over any kernel's catalog OR compiled in, same IRIs; directory wins per artifact; hot reload by golden threads; reviewed configuration | UI code that only works compiled in; unreviewed template directories |
| D18 | Tests | **One UI test kit** every UI repo runs: HTML snapshots, escaping vectors, contrast, axe, token and HTML lints | A repo-specific notion of "accessible" or "escaped" |
| D19 | Migration | FND-H now; then the shared path; then the **demo app and book**; then gonk, browse, cms-web, runbook, name, web, web-demo, cli | Migrating a live app before the demo shows the pattern |

---

## Part 1: how a view is made

### D1. Hypermedia fragments and page shells are compose templates

**Decision.** A page shell, a fragment, a card or a form is a compose template (`urn:iki:fn:compose`, the
model `urn:data:page` already follows in the REPL and the web). Data goes in through `$h{}`, which escapes
`&<>"'` and percent-encodes arguments inside an IRI. `$a{}` includes another trusted template; `$r{}`
splices trusted markup. `composeOver` and `conditional` make the choices a view needs (empty states,
capability-dependent controls). A template is a resource: it is cacheable, it acts under its caller's
authority , and it is named by an IRI so it can be overridden (D17).

**Why.** compose is the strongest base the inventory found (ikigai-fn `src/lib.rs:210-290`): its escaping
is right, it is tested (`tests/templates.rs`, `tests/escape.rs`), and the tutorial already fills the same
templates identically from Rust, Python and Deno. The alternative in use today is `format!`: about 176
sites in browse alone, plus gonk's `web.rs` and `k.rs`, cms-web's ~20 fragments, runbook's `lib.rs:954` and
`:985`, and the cli's `error_page.rs:52` and `decide.rs:390`. Those are where the escaping divergences and
the runbook injection came from.

**Rules out.**
- A view built by Rust string formatting (D4 says where the exception lives).
- `$r{}` or `$a{}` on anything that came from a request, an atom a person can write, a peer or a model. A
 lint fails a template whose `$r`/`$a` argument names a request argument (inventory candidate 1).
- Templates that are not resources (a template string in a `const` with no IRI cannot be overridden or
 reviewed as an artifact).

**Status.** Decided 2026-10-17.

### D2. Graph to page is XSLT

**Decision.** When the input is a graph (Turtle, RDF/XML, SPARQL results), the page is an XSLT transform
through ikigai-xslt with `method="html"`. Values go out through `xsl:value-of` and attribute value
templates only. Stylesheets are resources (D17). The shell an XSL page uses is the same shell D1's
templates use: one shell, two ways to fill it.

**Why.** It is already the spine where the data is a graph: gonk renders every page through one
`web/gonk.xsl` (view triples to RDF/XML `<view:page>`, `src/render.rs:1-73`), cms-web through seven
stylesheets over SPARQL, and the edge's FOAF page through XSL served from workspace files. gonk uses no
`disable-output-escaping` and ikigai-xslt's HTML serializer escapes script and style text, so this path
is already safe by construction. Brian: XSLT is a primary mechanism, not an option.

**Rules out.**
- `disable-output-escaping` anywhere.
- JSON assembled by string concatenation inside XSL (cms-web's `hx-vals`, ranked risk 2): JSON for
 `hx-vals` comes from FND-H's `json_attr`, or from an XSL function that the shared vectors hold to the
 same answers (D5).
- Hex colors inside a stylesheet (cms-web's per-XSL hex; D12).
- A Rust walk of a graph that emits HTML when an XSL would do.

**Choosing between D1 and D2.** Graph in, page out: XSLT. Named arguments in, fragment out: compose. A
page that needs both composes the XSL output as one of its parts (`$a{urn:…:xslt?…}`), as gonk's 10-row
cacheable chunks already do.

**Status.** Decided 2026-10-17.

### D3. One model, many faces: transreption

**Decision.** The thing a view shows is a resource with a model representation (a graph or a composed
structure), and each delivery is a face reached by transreption: `text/html` for the web, `text/plain`
for the terminal, `application/ld+json` for an agent or a dashboard, and later GPUI or a wasm renderer.
A door negotiates the face through the catalog's declared faces, as the cli library already does
(`Faces` in `crates/ikigai-web/src/lib.rs`).

**Why.** Brian's requirement: composition, XSLT and transreption feature prominently. It is the kernel's
own idea (representations and transreptors), so it needs no kernel change, and it is what lets the book
reserve GPUI, wasm-in-browser, WebTransport and terminal chapters without a rewrite.

**Rules out.**
- A renderer per target written against its own idea of the data.
- HTML as the only face of a view: every view has at least an HTML face and a machine face (Turtle or
 JSON-LD) linked by `rel=alternate` (the edge precedent).

**Status.** Decided 2026-10-17. The research half is UX 4; this decision fixes only that the model is the
resource and HTML is a face of it.

### D4. Hand-written HTML is allowed only through FND-H's module

**Decision.** Where neither D1 nor D2 fits (a door's error page before any template is resolvable, a
transreptor's inner loop, a test fixture), HTML is built only with FND-H's functions, `ikigai_fn::html`
(`html_text`, `attr`, `url_segment`, `safe_href`, `json_attr`). It ships no builder; if one is wanted,
it goes in the same module. Raw markup is spliced only through one explicit, greppable call. A lint in the UI test kit (D18) fails a
crate that emits `<` tags from `format!`, `write!` or `push_str` outside that module.

**Why.** The inventory counts about ten hand-written escapers that disagree: `&<>"'` (compose, cli x2,
diagram, cms-web x2), `&<>"` (runbook, ikigai-web, ikigai-name, cms-web x2, and browse's `esc`, safe
only under a double-quote convention no test pins), and `&<>` only (cms-web `error_html`). Brian, on
Brian: "I don't want Rust code generating HTML snippets in different ways in different places."

**Rules out.** Any new escaper; any new `format!` HTML outside the module. Existing sites are migrated
by D19, not grandfathered forever: the lint carries a per-repo allowlist that may only shrink.

**Status.** Decided 2026-10-17. FND-H's escaping half merged as `ikigai_fn::html` and
rides the next ikigai-fn release (0.4.1 is the published one), so no consumer can adopt it until then.

### D5. Escaping and URLs follow FND-H

**Decision.** One rule set, five functions, shared vectors:
- `html_text` for element text and `attr` for attribute values: escape `&<>"'`. **Every attribute value
 is quoted.**
- `url_segment` for a value placed in a path segment or a query parameter: percent-encode. A value never
 goes into a URL unencoded, and a whole URL never goes into a template where a value belongs.
- `safe_href` for any URL that came from data: `http`, `https` or a single-slash relative path; anything
 else renders as text, not a link ('s rule, the reference implementation).
- `json_attr` for `hx-vals` and any JSON in an attribute.
- The XSL side gets the same functions as named templates, and a test holds the XSL and Rust answers
 equal on the shared vectors, ikigai-fn's `tests/vectors/html_vectors.json` (
 already does this for links). Python and Deno run the same file. Known gap, stated by: xrust
 reads `&#9;` in an XPath literal as a space, so the XSL `safe_href` cannot refuse an inner tab; an XSL
 port either works around it or records those cases as a known difference.

**Why.** The unencoded-URL bugs are in runbook (`hx-get="/k/{cmd}"`), ikigai-name (`hx-get` query
parameters), cms-web (tags) and gonk (`browse_url` at `src/queue.rs:2490`, layer ids at
`src/layers_view.rs:1519`), and gonk alone has three percent-encoders (`web.rs:1223`, `rules.rs:327`,
`k.rs:475`). The only scheme allowlist in the ecosystem is gonk's https check
(`layers_view.rs:1533`); cms-web renders `dc:identifier` as an `href` with no scheme check (ranked risk 1).

**Rules out.** Escaping by hand; an unquoted attribute; `javascript:`/`data:` hrefs from data; per-repo
URL encoders.

**Status.** Decided 2026-10-17; the functions exist (`ikigai_fn::html`, FND-H), unreleased.

### D6. Door defaults

**Decision.** Every HTML door (anything answering `text/html` to a browser) adopts one default layer,
generalized from the cli `ikigai-web` library (`SecurityHeaders`, `src/lib.rs:689`; per-route CSP;
`admit_fn`, `:362`):

- **CSP** for door-authored HTML: `default-src 'self'; script-src 'self'; style-src 'self';
 img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self';
 object-src 'none'`. No `'unsafe-inline'`, no `'unsafe-eval'`, no inline `<script>` and no `style=`
 attributes (D12 moves them to classes). A route loosens CSP only by an explicit per-route entry that
 names its reason.
- **HTML the door did not author** (a `text/html` representation from a writable mount, a peer, or a
 caller's `content=`) is served with `Content-Security-Policy: sandbox; default-src 'none'` or as
 `text/plain`, never as same-origin HTML with scripts (ikigai-web's ranked risk 3; gonk's
 `urn:iki:gonk:render` with caller `content=`, `src/render.rs:511-549`).
- **Origin admit:** every unsafe method (POST, PUT, PATCH, DELETE) needs an `Origin` (or, failing that,
 `Referer`) that matches the door's own origin or a listed one; otherwise `403`. cms-web's Origin/Host
 check is the precedent.
- **Session cookie:** `HttpOnly; Secure` (where TLS); `SameSite=Strict`; `Path=/`; the cookie carries
 authority only for its own origin's door.
- **Headers:** `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` (or
 `same-origin` where a door needs it), HSTS on HTTPS, `Cross-Origin-Opener-Policy: same-origin`.
- **Caching:** ETag and `304` as the library does; `Expiry::Never` maps to `no-cache` + ETag, never
 `immutable` (field guide); a vendored asset with a digest in its name is the one `immutable` case.

**Why.** The inventory found doors with no CSP at all (ikigai-web, cms-web), and the cli library's own
default CSP blocks the inline `style=` its own pages use, so the defaults and the pages disagree today.
Writing the layer once is FND-H's door half.

**Rules out.** A door with no CSP; per-door hand-rolled headers; `'unsafe-inline'` as the way to make a
page work; serving a stored `text/html` blob same-origin.

**Status.** Decided 2026-10-17; FND-H's door half is an
ikigai-cli arc in flight; its consumers and the migrations.

---

## Part 2: htmx, URLs and history

### D7. htmx: one version, served as a resource, configured once

**Decision.**
- **One vendored htmx** (2.0.4 today; one bump moves every app) served as a resource from the shared
 artifact set (D17) at a digest-named static path, with `integrity=` on the `<script>`. No CDN.
- **Configuration once,** in the shell's `<meta name="htmx-config">`: `allowEval:false`,
 `allowScriptTags:false`, `includeIndicatorStyles:false` (its injected `<style>` conflicts with D6's
 CSP), `historyCacheSize` small, `refreshOnHistoryMiss:true` (D8).
- **No global `hx-boost`.** Every navigational link is a real `href` to the page URL (it works with no
 JS), plus `hx-get` to the fragment, `hx-target="#main"` and `hx-push-url` to the page URL. Boosting is
 allowed only on a form whose answer is a full page.
- **Targets and swaps:** navigation swaps `#main` (`innerHTML`); a card or row that re-renders itself
 swaps `outerHTML` on itself; out-of-band swaps (`hx-swap-oob`) are for the status region and header
 badges only (gonk's nine uses are exactly these).
- **Indicators:** `hx-indicator` with one shared `.busy` class, plus `aria-busy="true"` on the target
 while a request is in flight (browse's `busy_region` precedent; D13).

**Why.** htmx 2.0.4 is everywhere but delivered three ways, and web-demo loads it from unpkg with no SRI
(ranked risk 4). gonk already sets `allowEval:false` (`web/gonk.xsl:48`) and is the model. A global boost
fetches whole pages and hides the fragment/page split D8 depends on.

**Rules out.** A second htmx copy in any repo; `hx-on:*` handlers and `js:` values (they need eval);
inline `<script>` in a swapped fragment; unpinned extension scripts.

**Status.** Decided 2026-10-17.

### D8. Fragment and page are separate resources

**Decision.** A view has a **page** IRI and, where htmx swaps part of it, a **fragment** IRI. The page
composes the fragment (D1), so the two cannot drift. `hx-get` names the fragment's URL; `hx-push-url`
names the page's URL; a reload, a deep link or a history miss fetches the page. The representation of a
URL never depends on `HX-Request`.

**Why.** gonk does exactly this and it is the only app whose history works: its routes name
`urn:iki:gonk:page:…` and `urn:iki:gonk:fragment:…` separately (`src/doors.rs:1090-1146`). Separate
resources are separately cacheable with their own ETags and golden threads, and the push stream (D9)
names the fragment a page must refetch by its own URL. Switching on `HX-Request` (the inventory's
candidate 5) would make one URL mean two representations, which needs `Vary: HX-Request` everywhere and
still breaks a history restore that replays the fragment URL.

**Rules out.** `HX-Request` as a representation switch (it may still be read for logging); a fragment
with no page that contains it; a page assembled in Rust from fragment strings.

**Status.** Decided 2026-10-17. Open question 3 asks Brian to confirm this over the `HX-Request` alternative.

### D9. Live data through the cli push stream, not polling

**Decision.** A view that shows changing data subscribes to the cli library's push stream
(`GET /_ikigai/push?path=<the fragment's path>`, `crates/ikigai-web/src/push.rs`): one SSE stream per
page, under the same capability as the page's reads, with one `cut` event per golden-thread cut that
invalidated something the page read. The shared shell script (D14) maps each event's `paths` to the
elements that were fetched from them and triggers a refetch (`hx-trigger="ikigai:cut"`); an unchanged
fragment answers `304`. Polling remains only for a fragment answered `no-store`, which the push doc
says cannot be heard, and states its interval and reason where it is declared.

**Why.** Live data is polled today (web-demo every 1 s, gonk's badges every 10 s and a layers long poll)
while the cli's push sits unused; push is cheap by construction because it rides the cache partition
the reads already built. gonk's watcher and badge code already special-cases its own retries
(`isOwnRetry` in `web/gonk.js`), which the push would mostly remove.

**Rules out.** New timer polling of cacheable fragments; the htmx SSE extension (one less vendored
script, and a cut event is a signal to refetch, not content to swap); a second push mechanism per app.

**Consequence.** A host that wants pushed pages grants `urn:cap:kernel:listen` in its `CapFn`, to
anonymous callers too if anonymous pages are live. That grant is part of each migration's review.

**Status.** Decided 2026-10-17.

### D10. A page URL is the IRI of the resource it shows

**Decision.**
- **The mechanical rule** is the default: path `/a/b/c` is IRI `urn:a:b:c` and back
 (`iri_from_path`, cli `crates/ikigai-web/src/lib.rs:2733`), each segment through `url_segment`.
- **A route table entry** is the exception, used where a public URL must be friendlier than the IRI
 (gonk's `/l/{ledger}/item/{id}` for `urn:iki:gonk:page:item:{ledger}:{id}`). Every entry is
 bidirectional, so a link can be generated from an IRI and an IRI read from a link.
- **Query parameters are the resource's named arguments** (its ArgSpecs), so a filtered or sorted view
 is a URL, and `theme=` (D12) is one of them.
- **Every view has a URL.** A navigation that changes `#main` pushes it (D7); an in-place action (a
 toggle, a form post that updates a card, a refetch) does not push. A post that creates a thing with its
 own page pushes that page's URL.
- **Back always works**: `refreshOnHistoryMiss:true` (D7) makes a miss a real page load, and a page
 never relies on in-memory state that a reload loses.
- **Deep links are stable**: a URL once published keeps resolving; a rename answers a redirect from the
 old URL. Every page carries `rel=canonical` and `rel=alternate` links to its other faces (the edge
 precedent).

**Why.** No app but gonk pushes history ("Back leaves every app", ranked divergence 10). The mechanical
rule already exists in the cli library and is what the push stream uses to map a cut to a path.

**Rules out.** A view reachable only by clicking; a URL that encodes UI state the resource does not
declare as an argument; client-side routers.

**Status.** Decided 2026-10-17.

### D11. One `/k` adapter form

**Decision.** The engine adapter (a REPL command over HTTP) has one form: `GET /k?c=<engine command>` for
read verbs (source, exists, meta), `POST /k` with the command in the body for sink and delete, under the
caller's session capability, behind D6's Origin admit. Its answer is served in the answer's own media
type; a `text/html` answer gets the sandbox CSP of D6, because the door did not author it.

**Why.** The adapter exists in three forms: ikigai-web's `/k/<cmd>`, gonk's `/k?c=` and web-demo's JS
intercept. A command in a path segment collides with `/` and `:` in IRIs and is exactly where runbook's
unencoded `hx-get` came from; a query parameter is encoded by the platform. A GET that mutates is a CSRF
hole, which is why the verb follows the method.

**Rules out.** `/k/<cmd>`; JS that intercepts clicks to emulate the adapter; a mutating verb over GET;
caller-supplied HTML rendered same-origin with scripts (the gonk render risk).

**Status.** Decided 2026-10-17. Open question 7 asks whether a public door offers `/k` at all.

---

## Part 3: look, feel and access

### D12. One token set, four palettes, a tested contrast floor

**Decision.**
- **Token names start from `web/gonk.css`** (lines 11-23): `--bg --surface --fg --muted --accent
 --accent-fg --danger --danger-fg --ok --line --radius --gap`, plus what the other palettes need:
 `--ok-fg`, `--focus` (the focus ring) and `--warn`/`--warn-fg`. A new token is a change to this record.
 The old names map once: `--ground`→`--bg`, `--ink`→`--fg`, `--mut`/`--cms-mut`→`--muted`,
 `--edge`/`--browse-rule`→`--line`.
- **The token stylesheet is a resource** (D17), generated from palette TABLES in Rust data, as
 ikigai-name's are, not hand-written CSS.
- **Palettes: light, dark, high contrast and auto.** `auto` (the default) follows
 `prefers-color-scheme`, and `prefers-contrast: more` selects high contrast. The high-contrast palette
 comes from ikigai-name and holds itself to AAA (7:1).
- **The override works with no JS**: a theme chooser that is a real `<select>` in a `<form>` submitting
 `theme=` (ikigai-name's chooser, `src/docs.rs:342-370`, the best in the inventory), which the door
 renders as `data-theme` on `<html>`. With JS, the shell script remembers the choice and applies it
 before first paint (open question 4 on how it is remembered).
- **The contrast floor is a test**, through ikigai-a11y, for every palette and every text/ground pair
 (`--fg`/`--bg`, `--fg`/`--surface`, `--muted`/`--bg`, `--muted`/`--surface`,
 `--accent-fg`/`--accent`, `--danger-fg`/`--danger`, `--ok-fg`/`--ok`): 4.5:1 text, 3:1 for `--line`
 and `--focus` against their ground, 7:1 in high contrast. ikigai-diagram's test is the model.
- **Components and pages use tokens only**: no hex color outside the palette tables, and a lint that
 every `var(--x)` used is defined.

**Why.** There are five theme schemes and only three test contrast; token names diverge for the same
roles; cms-web uses `--cms-mut` without defining it; ikigai-web uses `light-dark()`, which cannot honor an
override or a high-contrast palette. Brian: start from gonk.css's names (he likes gonk's look), add
ikigai-name's override and high-contrast palette; themable and a11y-compliant are requirements.

**Rules out.** Per-repo token names; `light-dark()` in shared CSS; a palette no test checks; a theme that
needs JS to choose.

**Status.** Decided 2026-10-17.

### D13. Accessibility: WCAG 2.2 AA as the floor

**Decision.** Every page and component meets WCAG 2.2 AA, and the shared shell and script implement the
parts that are behavior:
- **Landmarks:** one `<header>` (banner), each `<nav>` named with `aria-label` when there is more than
 one, one `<main id="main" tabindex="-1">` (the edge precedent), `<footer>`; a skip link to `#main`
 (gonk has one).
- **Focus after a swap**, done once in the shell script on `htmx:afterSettle`: after a navigation, focus
 moves to `#main`'s first heading (or `#main`); when a swap replaced the element that had focus, focus
 goes to the element with the same `id` in the new content, else to the swapped region; it is never
 left on `<body>`. Runbook's tab swap is the
 test case.
- **One status region:** `#status`, `role="status"`, polite, for results and progress (gonk's `#flash`);
 `role="alert"` only for the failure of the person's own action. Badges and watchers never write to it
 (gonk's `isOwnRetry` rule).
- **Busy:** `aria-busy="true"` on a target in flight; a "loading…" placeholder is always replaced, by
 content or by a refusal note (D14).
- **Keyboard:** every action reachable with Tab and operable with Enter/Space; Escape closes what opens;
 no traps; a visible `:focus-visible` ring in `--focus` at 3:1; targets at least 24×24 CSS pixels
 (2.5.8); focus not obscured by sticky headers (2.4.11).
- **Forms:** every control has a `<label>`; errors are tied with `aria-describedby` and `aria-invalid`
 (D15).
- **Motion:** honor `prefers-reduced-motion`.
- **Each repo with a UI keeps an `ACCESSIBILITY.md`** (ikigai-name lacks one today).
- **axe runs in CI** against every page the snapshot tests render and every component's demo page (D18).

**Why.** Focus after a swap is handled nowhere; browse has duplicate unnamed navs and placeholder-only
labels; there is no axe anywhere. gonk's shell (`web/gonk.xsl:40-104`) already has the skip link,
landmarks, the status region, `:focus-visible` and `.sr-only`, so the shared shell starts there.

**Rules out.** A swap that strands focus; announcing badge polls; a placeholder as a label; an
"accessible" claim with no automated check behind it.

**Status.** Decided 2026-10-17.

### D14. Errors, refusals and loading: one shell script

**Decision.** One shared shell script, served as a resource (D17), replaces gonk's `denyAt`
(`web/gonk.js:60`) and ikigai-web's `SHELL_JS` (`src/serve.rs:949`). It is their union:
- A failed request's text is set with `textContent`, never parsed as markup.
- An element that swaps into itself gets the note in place of its placeholder; any other control gets it
 right after itself, **replacing** a previous note rather than stacking (ikigai-web's behavior).
- A `403` marks the control `aria-disabled="true"` (and `disabled` for a button): an authority refusal is
 permanent for this grant. Every other status leaves the control usable.
- A failure outside any region goes to `#status`; a send error says "The server did not answer."
- Elements that retry on their own (badges, watchers) are silent.
- The same script carries D9's push mapping, D13's focus handling and D12's theme memory, so there is
 exactly one first-party script per page.

On the server side a **refusal is an HTTP status with a short plain body** that says what was refused
and which capability would allow it (the denied `urn:cap:…`), so the script can show it and an agent can
read it. A full-page navigation that fails gets the one error page template (the cli's `error_page.rs`
made a compose template), with the same words.

**Why.** Error and refusal rendering differs between repos (ranked divergence 9), and the two existing
scripts are already near-copies that each documents its own deliberate differences.

**Rules out.** Swapping an error body as HTML; a spinner that never resolves; per-repo error scripts;
refusals that say "error" without saying what authority was missing.

**Status.** Decided 2026-10-17.

### D15. Forms

**Decision.**
- **Forms come from the contract** where they can: a Sink or Source action's ArgSpecs and ActionSpecs
 give the labels, `required`, `one_of` (a `<select>`), `class` (the input type) and `default`. This is
 what lets the generic engine of D17 render forms against any kernel's catalog.
- **Methods follow verbs:** a Source with arguments is a `GET` form whose query is the view's URL (D10);
 Sink and Delete are `POST`.
- **Same-origin by construction:** D6's Origin admit on every unsafe method plus the `SameSite=Strict`
 session cookie is the CSRF defense. A door that must accept requests with no `Origin` header adds a
 synchronizer token; none does today.
- **The server validates** (SHACL pre-flight through `urn:kernel:validate` where a shape exists); HTML
 constraint attributes are hints. A refusal renders beside its field with `aria-describedby` and
 `aria-invalid`, and the form keeps what was typed.
- **After success:** with htmx, swap the result and write `#status`; without JS, Post/Redirect/Get to
 the resulting page.

**Why.** cms-web's Origin/Host check and `SameSite=Strict` are the working precedent; ikigai-web has no
CSRF defense at all (ranked risk 3). ArgSpecs exist precisely so that something other than the endpoint's
author can render its form.

**Rules out.** A mutating `GET`; validation only in the browser; a form whose labels are hand-copied
from the endpoint's description.

**Status.** Decided 2026-10-17.

---

## Part 4: components and artifacts

### D16. WebComponents for ikigai things

**Decision.** A small set of custom elements, one per ikigai thing worth embedding: `<ikigai-ledger>` (items and next),
`<ikigai-item>` (an item card), `<ikigai-layer>` (channel entries with a post form; task tuples),
`<ikigai-review-queue>`, `<ikigai-resource>` (a resource card), `<ikigai-refusal>` (the note of D14) and
`<ikigai-theme>` (the chooser of D12). Rules for every one:
- **Progressive enhancement:** the element's content is server-rendered (compose or XSLT) and is a link
 to the full page; with JS it hydrates. Server-rendered shadow content uses declarative shadow DOM
 (`<template shadowrootmode="open">`).
- **Theming:** tokens reach inside through CSS custom properties (they cross the shadow boundary);
 structure is exposed through `::part`, with part names stated in the component's description. A host
 page restyles without forking.
- **a11y per component:** its roles, keyboard path and focus behavior are stated in its description and
 tested by axe on its demo page. ARIA references stay inside one shadow root.
- **Self-description:** the host element carries the IRI it shows (`src="urn:…"`) and RDFa Lite
 `about`/`typeof` for it, so a dashboard or an agent can find what a page shows and fetch the resource's
 own Turtle or JSON-LD face (D3) instead of scraping (open question 5 on inline JSON-LD).
- **Data from resource sources:** the same IRIs gonk serves, read through the embedding origin's door.
- **Authority:** on its own origin, the page's session. Embedded on someone else's page ("advertising
 ledgers"), a **scoped capability token** passed in an attribute: attenuated, read-only by default,
 expiring, and checked by the door; CORS opened to listed origins with credentials off. Write controls
 render only when the token carries the write capability (the capability-aware `web::nav` precedent).
- **Packaging:** each component's definition, template, style and part list are resources
 (`…:component:{name}`, D17), versioned, so a page can pin one.

**Why.** Brian wants reusable components "for things like advertising ledgers, layers", themable and
a11y-compliant. web-demo's `<ikigai-cli>` is the only component today. Self-describing components are
what lets a dashboard aggregate views from resource data sources.

**Rules out.** Ambient cookies (`credentials: "include"`) from a third-party page; a component that is
blank without JS; hard-coded colors or fonts inside a shadow root; a component with no stated keyboard
path.

**Status.** Decided 2026-10-17. Open question 9 asks Brian to accept shadow DOM's costs for `::part`.

### D17. UI artifacts are resources, delivered two ways

**Decision.**
- **What counts as an artifact:** compose templates, XSL stylesheets, the token stylesheet, the shell
 script, vendored htmx, component definitions, render rules (gonk's `/render-rules`, Turtle, is the
 precedent) and the route-to-view map. Each has an IRI.
- **Delivery A, a directory:** artifacts read through bound IRIs (`urn:file:*` and the config-home
 layering), so a **generic UI engine plus a directory** renders an app against ANY binary's kernel
 catalog: navigation from the catalog (`urn:kernel:catalog`, and `urn:kernel:actions` under the caller's
 capability), forms from ArgSpecs (D15), views from the directory's templates. Edits hot-reload: a file
 watcher cuts the thread, golden threads recompute, and the push stream (D9) refreshes open pages.
- **Delivery B, compiled in:** the same artifacts embedded with `include_str!`/`include_bytes!` (or one
 embedded bundle) under the **same IRIs**, for a standalone binary that behaves identically.
- **Precedence:** a directory artifact overrides the compiled-in default **per IRI**, layered in the
 config home's order. A test in each app renders its pages from both deliveries and asserts the same
 output, so the compiled-in set is the baseline.
- **Artifacts are reviewed configuration:** a template can make requests (htmx attributes), so a
 directory is code-like. Write authority on it is a capability (as R2RML mappings are), it is under
 version control, and an artifact cannot loosen the door: CSP and headers are door configuration, not
 artifacts. Signed artifacts are later work.

**Why.** Brian: artifacts should live outside the binary and run against an engine and a
directory over the binary's kernel catalog, and compiled in for a standalone mode. The precedents: the
edge already serves its FOAF XSL from workspace files; gonk compiles `web/gonk.xsl` in; the cms-web
"externalize templates" note asked for this.

**Rules out.** UI that only works compiled in; a directory that can change headers or CSP; unreviewed
writable template directories on a live host.

**Status.** Decided 2026-10-17. Open question 11 asks where the generic engine lives.

### D18. One UI test kit

**Decision.** One shared test crate (with Python and Deno vector files where those faces render) that
every UI repo runs:
- HTML **snapshot** tests per page and fragment, so a migration proves it kept the look;
- FND-H's **escaping and safe_href vectors**, and the XSL-equals-Rust check;
- the **contrast** test per palette (D12);
- **axe** on every snapshot and every component demo page (D13);
- **lints:** no `format!` HTML outside FND-H (D4), no `$r`/`$a` on request arguments (D1), every used
 `var(--x)` defined (D12), every emitted class styled (browse's `every_emitted_class_is_styled`),
 every attribute quoted, no inline `style=` or `<script>` (D6);
- the **two-delivery** equality test (D17).

**Why.** cms-web's focus checks, the cli's `assert_page`, browse's class lint, gonk's ~230 contains-checks
and the book's contrast gate are each one repo's private notion of "correct" (inventory candidate 7).

**Rules out.** "Accessible" or "escaped" as a claim without the shared check behind it.

**Status.** Decided 2026-10-17.

### D19. Migration order

**Decision.**
1. **FND-H**, now: the escaping and URL module (merged in ikigai-fn, unreleased) and the door layer
 (ikigai-cli, in flight). The security fixes proceed now too.
2. **The shared path**: the shell, the shell script,
 the token stylesheet, vendored htmx as resources, and the test kit.
3. **The demo app and the book**: a small real app built only this way, and "building ikigai
 UIs" as the third book in ikigai-tutorial, structured for later targets (Part I the model, Part II
 delivery targets with GPUI, wasm-in-browser, WebTransport and terminal reserved as stubs, Part III
 components). The components are proved there first.
4. **Then the migrations, one repo and one satellite at a time, each citing the book:** gonk first
 (pixel-equivalent; Brian likes its look), then browse, cms-web, runbook, ikigai-name, ikigai-web
 (standalone), web-demo, and the cli's own pages (`error_page.rs`, `decide::page`). Each PR keeps the
 current look, adds snapshot tests, and passes axe and contrast.

**Why.** Brian: a demo app and a new tutorial book before migrating the existing apps.
Refactors touch live pages, so they go repo by repo with visual checks.

**Rules out.** Migrating a live app before the demo exists; a cross-repo big-bang refactor; deferring the
security fixes behind this record.

**Status.** Decided 2026-10-17; ordering already given by Brian.

---
