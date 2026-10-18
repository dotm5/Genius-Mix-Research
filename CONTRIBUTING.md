# Contributing

## Scope

This repository covers the Genius data layout of the iPod nano 6G: the `.itdb` byte layout, the constraint bytecode, the Mix state machine, and the tools under `tools/`. Changes stay inside that scope.

## Development setup

Python 3.9 or newer, standard library only.

```bash
python -m compileall -q tools
for tool in tools/*.py; do python "$tool" --help; done
python tools/check_links.py
```

CI runs the same commands on Python 3.9 and 3.12, plus the Markdown link check.

## Device measurements

A device measurement enters the documents with its context: firmware version, container count, member counts, genre spread, and the exact tool invocation. Keep libraries out of the repository — describe the schema and the numbers. `.gitignore` excludes `.itdb` files, snapshots and backups.

## Document style

`docs/FORMAT.md` is the reference for document style:

- declarative statements bound to byte offsets and measured numbers;
- tables for layouts, parameter sets and result matrices;
- one structure for every revised round, with the revision noted in place;
- English and Chinese versions of the README stay aligned.

## Commits and pull requests

- English commit titles in the imperative mood; details in the body.
- One logical change per commit.
- `main` takes squash merges, and the head branch is deleted on merge.
- Run the three commands above before opening a pull request.

## Licence

Contributions are released under the MIT licence of the repository.
