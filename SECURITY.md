# Security Policy

## Scope

This repository ships offline tools that read and rewrite personal music databases. The security-relevant outcomes are data loss in the local library and the disclosure of personal data in a report.

## Reporting

Report a vulnerability through the repository's private reporting channel: **Security → Report a vulnerability**. Include the commit id, the Python version, the command, and the observed result.

Keep device databases, library contents and account identifiers out of the report; describe the schema and the numbers instead. Reports are acknowledged within seven days.

## Handling of data

The tools write to `Library.itdb`, `Genius.itdb` and `Dynamic.itdb`, and take a SQLite snapshot before writing. Keep snapshots and backups outside the repository: `.gitignore` excludes `*.itdb`, `*.db`, `*.sqlite*`, `backups/` and `out/`.

## Supported versions

The latest commit on `main` receives fixes. Device measurements are recorded per round in `docs/`.
