# Catalog

## Purpose

The books the library owns: a title, cataloged under its ISBN, and the copies of it on the
shelves. A loan is always of a copy, never of a title.

Artifacts: `service`, `cli`

## Requirements

### Requirement: A title is cataloged once

The library SHALL catalog a title under its ISBN with its title and author, and MUST refuse a
second title with an ISBN already cataloged.

#### Scenario: Cataloging a title

- **WHEN** a title is cataloged with an ISBN, a title and an author
- **THEN** the library answers with all three

#### Scenario: Cataloging an ISBN twice is refused

- **WHEN** a title is cataloged with an ISBN that is already cataloged
- **THEN** it is refused as `duplicate-title`

### Requirement: A copy is of a cataloged title

The library SHALL add a copy under its own id, of a title already cataloged, and the new copy
SHALL be available. It MUST refuse a copy of an ISBN that is not cataloged, and a copy id that
is already in use.

#### Scenario: Adding a copy

- **WHEN** a copy is added for a cataloged ISBN
- **THEN** the copy is available, with no loan

#### Scenario: A copy of an unknown title is refused

- **WHEN** a copy is added for an ISBN that is not cataloged
- **THEN** it is refused as `unknown-title`

#### Scenario: A copy id is used once

- **WHEN** a copy is added with an id another copy already has
- **THEN** it is refused as `duplicate-copy`
