# Decisions

Program-level decisions live in the owner's refactor log; this file records the ones taken for
this repository.

| # | Date | Decision | Choice | Why |
| --- | --- | --- | --- | --- |
| 1 | 2026-10-28 | Identity | Keep the UNDB course-project identity; the original mobile front-end was written by a teammate and is not in this repo | Honest origin story; the backend was the owner's part |
| 2 | 2026-10-28 | History before the rebuild | Scanned with gitleaks v8.28.0 (no leaks); every historical `aluno.parquet` is identical to Faker 0.7.4 seed 42 output, so no real student data ever entered git | Required check before rewriting a public history |
| 3 | 2026-10-28 | History rewrite | One `git filter-repo` pass: author and committer email mapped to the personal address, the 7 placeholder messages replaced by English messages describing each diff, trees and dates unchanged; one force-push of `main` (approved by the owner) | Employer address must not appear in a personal public repo |
| 4 | 2026-10-28 | Repository state and name | Unarchived (the repo had been archived as finished coursework) and renamed `undb-programacao-mobile` to `undb-smart-parking` | The rebuild needs PRs, CI and branch protection; the new name says what the project does |
| 5 | 2026-10-28 | License | MIT, copyright Emanuel Luis | Public portfolio repo; the code in this repo is the owner's |
