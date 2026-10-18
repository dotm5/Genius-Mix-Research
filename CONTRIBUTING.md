# Contributing

## Scope

This repository covers the Genius data layout of the iPod nano: the `.itdb` byte layout, the constraint bytecode, the Mix state machine, and the tools under `tools/`. Changes stay inside that scope.

## Development setup

Python 3.9 or newer, standard library only.

```bash
python -m compileall -q tools
for tool in tools/*.py; do python "$tool" --help; done
```

CI runs the same commands on Python 3.9 and 3.12.

## Device measurements

A device measurement enters the documents with its context: firmware version, container count, member counts, genre spread, and the exact tool invocation. Keep libraries out of the repository — describe the schema and the numbers. `.gitignore` excludes `.itdb` files, snapshots and backups.

## Document style

Documents follow the structure of `docs/FORMAT.md`: measured numbers next to the claim, one section per topic, and the Chinese and English README kept in step.

## Commits and pull requests

- English commit titles in the imperative mood; details in the body.
- One logical change per commit.
- `main` takes squash merges, and the head branch is deleted on merge.
- Run the two commands above before opening a pull request.

## Licence

Contributions are released under the MIT licence of the repository.
