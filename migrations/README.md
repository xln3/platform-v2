# Alembic migration allocation

Linear mainline revisions use `sNN_rrrr` identifiers:

- S01 owns `s01_0001`–`s01_0999`.
- S02 owns `s02_0001`–`s02_0999`.
- S03 must not create database revisions; request a contract gap.
- S00 reserves `s00_0001`–`s00_0099`; S04 owns merge revisions `s04_merge_####`.

Each session branches from the frozen S00 base. Parallel heads are expected and S04 creates an explicit merge revision; sessions never renumber or edit another session’s migration.
