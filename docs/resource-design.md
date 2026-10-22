# Designing resource-first

This is the design method the ikigai project uses before writing any new app, module,
endpoint family or feature. `AGENTS.md` points here: before you design one, do this pass and
put its result, a `## Resource map`, in the spec change (`openspec/changes/<id>/proposal.md`),
or in the pull request when there is no spec.

**Why.** Software that is written as code which merely NAMES resources gets none of what
resources give. The tic-tac-toe arc in [the ikigai Book](https://ikigai-rs.github.io/ikigai-tutorial/)
(Applied, parts I to VII) was designed IN resources, and it got caching, invalidation,
portability and multi-tenancy almost for free. The method comes from Peter Rodgers's
*Resource Oriented Analysis and Design* series in NetKernel News (3.34 to 3.42). NetKernel
itself was co-designed with Tony Butterfield; the series is Peter's.

**The order matters: names first, code last.** Do the steps in order, and write the result
down before any code is designed.

## The five steps

1. **Sets.** List every kind of thing in the domain. Give each a name pattern
   (`urn:<ns>:<set>:{var}`) and one line on what a member IS. Include the questions people
   ask ("whose turn", "who won") as sets too: a question is a resource.
2. **Atoms.** Mark the FEW sets that must HOLD state: the ones written by Sink. These, and
   only these, are code that stores. Everything else must justify being code.
3. **Compositions.** For every other set, say how it is COMPOSED from other names,
   preferring, in this order:
   - an exact alias or mapping rule (no code);
   - a list of other names (a resource whose value is names);
   - a generic composer (SPARQL over graphs, a template fill, `urn:fn:*`);
   - a composition written as a stored s-expression or script over the kernel verbs (code
     that is itself a resource, whose only authority is the verbs);
   - a tiny pure function over sourced resources (cacheable, golden-threaded by
     construction).

   A handler that reads other resources in code and assembles a result in code is the smell.
   Say why a generic composition cannot do it before accepting one.
4. **Spaces: compose the context, not just the resources.** For each design, name its
   spaces:
   - a TEMPORAL space (the world as of an instant);
   - a PERSONAL space (a principal's own view or overrides, over the shared names);
   - a scenario, tenant or game (a corridor per instance);
   - module internals;
   - SEALED names (what no module may ever answer for).

   Context that varies a whole request is a CORRIDOR, never an argument threaded through
   every endpoint. Corridors stack (a personal scenario over a temporal view), and that
   stacking is where most of the dynamism and security come from. Prefer the arrangement as
   DATA (spaces declared in Turtle or s-expressions), not code.
5. **Views.** Presentation is resources too. A template is a resource (`…:template:{name}`),
   and a view composes the template with the names it shows. A write from a view is a Sink on
   a name. Say which faces (text/plain, Turtle, HTML) each set offers.

**Code is a resource too, and compilation is a transreption.** Where a design carries code (a
query, a stylesheet, a template, a program), make the SOURCE a resource and the compiled or
prepared form a representation reached by transreption. That form is cached and
golden-threaded from the source, so an edit recompiles exactly what depends on it.

Then check the economics before code:

- **Cacheability.** Which compositions are pure (cacheable)? Which atoms do they hang from
  (golden threads)? Cacheability is only as good as the least cacheable dependency: never
  join a volatile source into an expensive cached composite.
- **Errors.** Which refusals are state refusals (Conflict), bad arguments (InvalidArgument),
  authority (Denied), or absence (NotFound)? A refusal is part of the resource's contract.

## Worked example: tic-tac-toe

The reference design, built chapter by chapter in the Book, starting at
[Tic-tac-toe, I: the model and the atoms](https://ikigai-rs.github.io/ikigai-tutorial/applied/tic-tac-toe-1.html).

| set | name | kind | how |
|---|---|---|---|
| stored mark | `urn:iki:tutorial:ttt:stored:{x}:{y}` | ATOM | the only state; NotFound when unplayed |
| cell | `…:cell:{x}:{y}` | tiny function | the stored mark, or `-` |
| line of cells | `…:cells:{list}` | tiny function | cells in order, named by their coordinates |
| row, column, diagonal | `…:row:0` … `…:diagonal:1` | alias rule | eight exact aliases onto `cells:{…}`, no code |
| board | `…:board` | composition | every row |
| checkset | `…:checkset:{x}:{y}` | list of names | the lines a cell is on |
| turn, winner | `…:turn`, `…:winner` | tiny functions | questions about the board |
| move | `…:move:{x}:{y}` | Sink edge | writes the atom. A taken square, a finished game or the wrong turn refuses with Conflict (HTTP 409); off the board is InvalidArgument |
| a game | `…:game:{id}` | corridor | context, not a name: a scope binding its own store |
| template | `…:template:{name}` | resource | HTML with ikigai-fn markers: `$h{…}` splices a value HTML-escaped and never expands it, `$r{…}` splices a trusted sub-view raw, `{x}` is a template argument |
| views | `…:view:board`, `…:view:square:{x}:{y}`, `…:view:play:{x}:{y}` | composition | a template bound at a name. The board splices nine square views, and a conditional over cell and winner picks each square's template. No view code. Play is a Sink |

What fell out with no code written for it:

- **Golden-thread invalidation:** a move recomputes the board and the empty squares, and a
  square taken elsewhere stays cached.
- **Portability:** the same composition answers in the page, on a host, and over the Python
  and TypeScript peers ([part VI](https://ikigai-rs.github.io/ikigai-tutorial/applied/tic-tac-toe-6.html)).
- **Many games** from one corridor ([part IV](https://ikigai-rs.github.io/ikigai-tutorial/applied/tic-tac-toe-4.html)).

The design also exposed real kernel gaps. A resource-first design that hits a wall is FINDING
something: report it rather than coding around it.

## What to write down

**In a spec change or a design note:** a `## Resource map` section with a table like the one
above (set, name, kind, how), the context corridors and sealed names, and one line per piece
of code explaining why it cannot be a composition. In this repository's spec process that
section goes in the change's `proposal.md`; when a change has no spec, in the pull request.

**In a report on built work:** the map AS BUILT. Say where code crept in that the map hoped
to compose, and why.

**A smaller example: node onboarding.**

- A node enrolls itself through a typed lobby layer.
- The controller signs an entitlement.
- Each door applies only a verified one.

The kinds of node are data and the checks are compositions. The only code is the signing step
that holds the key, and the apply step that writes host files.
