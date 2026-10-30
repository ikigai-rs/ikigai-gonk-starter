---
name: skimmable-text
description: Write, or review, text that people skim as a set - lists, titles (sessions, pull requests, commits, ledger items, chapters, headings), cards, table rows, notifications, changelog entries, status lines - so each item parses at a glance and the set reads as one. Use when generating many short items, and when asked to review existing pages or docs against these principles.
license: Apache-2.0 OR MIT
metadata:
  source: "Shreya Shankar, Five Principles for Generating Readable AI Text in Bulk, Full Stack Data Lab, 2026-10-29, https://fsdatalab.github.io/blog/agent-text-at-scale/"
  hermes:
    tags: [writing, lists, titles, dashboards, review, readability]
    category: productivity
---

# Skimmable text

People skim sets of short text: a sidebar of session titles, a list of pull requests, a column of
ledger items, a grid of cards, a What's new page. Skimming works only when the items are
predictable, so the reader can compare them by their first words. These principles come from
Shreya Shankar's *Five Principles for Generating Readable AI Text in Bulk* (Full Stack Data Lab,
2026-10-29; link in the frontmatter), which draws on psycholinguistics, usability research and
data visualization. The procedures here are a paraphrase, with examples of our own.

**Scope.** The principles are about a SET of parallel items that someone scans. They apply to:
- titles (sessions, pull requests, commit subjects, ledger items, chapters, sibling headings);
- list items and table cells in one column;
- cards, notifications, status lines, changelog entries.

Running prose is out of scope, except that principles 2 and 3 still help a paragraph's first
sentence and its terms. A set of one item has nothing to be consistent with.

## The five principles

1. **Follow the order readers expect.** Readers take the first noun as the subject, the next
   verb as the action and the next noun as its object, and re-read when a sentence breaks that.
   - Start each item with its subject or its verb. When every item shares the subject (the
     agent, the user, the service), drop it and start with the verb.
   - Use one tense for the first verb, the one the reader expects: past for what is done
     ("Merged …"), continuous for what is running ("Measuring …"), imperative or future for
     what is to do ("Add …").
2. **Put the distinguishing words first.** Readers of a list often read only the first two
   words of each item.
   - Find the words that tell this item from its neighbors, and move them to the front.
   - A shared prefix ("The issue reports that …", "Update: …", "ikigai-x: …" on every row) wastes
     the two words the reader reads. Drop it, or move it into a column of its own.
3. **Use one structure and one vocabulary.** Parallel structure reads faster, and one word per
   concept is a basic usability rule ("one word, one meaning").
   - Pick one template for the set (for example: verb, object, qualifier) and use it for every item.
   - List the set's concepts and give each one word. If one row says "issue", no row says "page"
     or "ticket" for the same thing.
4. **Vary only where something differs.** Readers assume any difference in length, structure or
   wording means something, as they do with any visual difference in a chart.
   - Give every item the same parts (for a card: one sentence on what it says, one example, a
     count).
   - When one item has a part the others lack, either it marks a real difference (keep it, and
     make that visible) or it is noise (cut it, or give every item that part).
5. **Choose verbs whose tone matches the purpose.** Strongly charged words are recognized faster
   than neutral ones.
   - On a page of problems, say so ("Users complain …", "Fails when …"). On a page of praise, say
     that ("Users love …").
   - Avoid neutral openings ("X is a popular new …") that leave the reader unsure whether an item
     is good or bad news.

## Write mode

Use it when you generate several items that will be read together.

1. Name the set and its reader's purpose: what is the reader scanning for?
2. Choose the template: subject or verb first, the tense, and the parts every item has.
3. Write the distinguishing words first in each item.
4. Fix the vocabulary: one word per concept, the same word in every item.
5. Check variation: every difference between items should mean something.
6. Match the verbs' tone to the purpose.
7. Read only the first two words of every item. If two items look alike from those words alone,
   rewrite them.

House rules still hold: American spelling, plain words, and never rewrite a quotation, an
identifier, an API name or the title of someone else's work.

## Review mode

Use it to read existing text against the principles: a book's table of contents and headings,
READMEs, changelogs, issue or ledger titles, generated pages.

1. **Collect the sets.** `python3 skills/skimmable-text/sets.py FILE…` lists the parallel sets in
   Markdown files: sibling headings, the items of each list, the first column of each table.
   `--lines` reads one item per line instead, for titles exported from elsewhere
   (`git log --format=%s`, a ledger's titles). For each set it prints every item's first two
   words and word count, and flags LEADS: a shared first word, mixed openings, and length
   outliers. A lead is not a finding. Judge each one against the principles.
2. **Read each set** for the five principles in order. Read from top to bottom, as a reader would.
3. **Report findings in one format**, so the report is itself skimmable:

       <file>:<line>  P<n> <principle in two words>  "<item as written>"  ->  "<suggested rewrite>"

   Group findings by set, and open each group with the set's location and its purpose in one
   line. When a whole set breaks one principle, report it once, with the template you suggest
   and two or three rewritten items, rather than once per item.
4. **Do not report:**
   - an exception that marks a real difference (principle 4 says keep it);
   - a quotation, an identifier, an API name or an external title;
   - wording that is merely different from what you would write.
5. **Do not edit unless asked.** A review reports. When fixes are asked for, make them, and
   follow the repository's own rules for changes (a changelog or What's new entry, its checks).

End the report with one line per principle: how many findings, in which sets.
