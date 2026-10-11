# Tasks

## 1. Service

- [ ] 1.1 Accept a hold on a title whose copies are all out (A member may hold a title with no copy on the shelf)
- [ ] 1.2 Keep a returned copy for the first holder (A returned copy of a held title waits for the first holder)
- [ ] 1.3 Refuse a renewal while a hold waits (A loan of a held title is not renewed)
- [ ] 1.4 Add a vector to openspec/vectors.json for every new scenario

## 2. Client

- [ ] 2.1 Add `library.py hold MEMBER ISBN` (A member may hold a title with no copy on the shelf)
- [ ] 2.2 Show who a kept copy waits for in `library.py copy` (A returned copy of a held title waits for the first holder)
