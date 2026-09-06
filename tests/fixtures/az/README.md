# Arizona fixtures

| file | what it is |
| --- | --- |
| `2024-election-info.html` | REAL. A capture of the Secretary of State's 2024 election-information page, carrying the Sent/Accepted Early Ballots table the `az-sos` route parses. |
| `2026-election-info-no-table.html` | REAL. The same page before the table appears — the normal state of the world outside an early-vote window, and the `NotYetPublished` case. |
| `recorder-party-shape.SYNTHETIC.csv` | **NOT A REAL SOURCE FILE.** See below. |

## Why one fixture here is synthetic

Every other fixture in this repo is a saved capture of a real state file, which
is the rule in CLAUDE.md and the only thing that catches schema drift. This one
cannot be, and the reason is the finding rather than a shortcut:

**No Arizona county recorder publishes early-ballot returns broken down by party
to the public, in any format.** All fifteen were surveyed on 2026-09-06; the
endpoints and their exact HTTP statuses are recorded in `az.RECORDER_SURVEY` and
narrated in `docs/arizona-party.md`. Maricopa's answer is the clearest: its
election-data downloads sit behind a sign-in that says in as many words that
*"cities, towns, and political parties"* may use it. There is no file to save.

`recorder-party-shape.SYNTHETIC.csv` therefore pins the *contract*
`az.parse_party_table()` enforces, not a dialect anybody has seen:

* columns matched by header NAME, never by position;
* `PND` → `npa` (the label `normalize.party()` does not know, and the one that
  would move a third of Arizona if it were bucketed as `oth`);
* a blank cell stays blank — `None`, not `0`;
* a header the parser cannot place raises `SchemaDrift` instead of being swept
  into "other".

Those rules are required of every parser in this project whatever the file, which
is why they are worth holding now. The dialect gets corrected against the real
file the first day one exists — and on that day this fixture is replaced by a
capture of it and this section is deleted.

The party counts in it are invented. They are never compared to a published
Arizona number anywhere in the tests.
