# Tier 3 — hand-entered statewide totals

The floor of every state's source ladder. When a state publishes only a PDF, a
press release, or nothing machine-readable, type the number here and it appears
on the site with a "manual" provenance badge.

`2026_statewide.csv` (and `2022_`/`2024_` for backfill):

    cycle,state,date,ballots_total,mail_requested,mail_returned,inperson,party_dem,party_rep,party_oth,party_npa,note

- `date` is `YYYY-MM-DD`, the date the count is AS OF (not the date you typed it).
- **Leave a cell blank if the source did not report it. Never type 0 to mean
  "not reported".** A blank renders as "n/a"; a 0 renders as "zero ballots".
- Rows dated in the future are ignored until that date arrives, so you can enter
  a known schedule ahead of time.
- A manual row never overwrites a scraped one for the same state and date — the
  ladder keeps the better tier — so it is safe to leave entries here after a
  scraper starts working.

## Virginia 2022 / 2024 — vendored VPAP daily series

`2022_statewide.csv` and `2024_statewide.csv` carry one VA row per day of the
early-voting window, up to and including Election Day (2022-11-08, 2024-11-05).
They are the statewide daily series from VPAP's early-voting tracker
(vpap.org; its own credit line is "Source: Virginia Department of Elections"),
captured ONCE by hand and typed here as CUMULATIVE totals — `ballots_total`,
`inperson`, `mail_returned`; `mail_requested` and every `party_*` cell are blank
because the tracker does not report requests and Virginia has no party
registration. The `note` column names the tracker and its as-of date.

- **Not scraped, on purpose.** vpap.org sits behind an AWS WAF that escalates
  to a CAPTCHA, so the pipeline never requests it. These are frozen series;
  re-typing them is the only way they change.
- **By BALLOT date, not by as-of date.** VPAP logs each ballot on the day it
  was cast or received, as the final file records it, so the curve runs
  slightly AHEAD of what a live tracker showed on that day: a mail ballot
  received on the 20th may only have been logged on the 22nd. In-person
  ballots are unaffected (the voter is recorded on the spot). The difference
  is a few thousand mail ballots at most and shrinks to nothing by the end of
  the window.
- **The series continues past Election Day in the source** (late-arriving
  mail, provisionals: 2022 ends 11-14 at 976,375; 2024 ends 11-10 at
  2,368,684). Those days are dropped here. The Election-Day row in
  `output/ev_state_daily.csv` stays the state's own certified final from
  `adapters/va.py` (tier 1), which the merge keeps over a manual row on the
  same date; the manual rows fill the days before it.
- ELECT itself holds no 2024 daily file and the Internet Archive has no
  pre-election capture of one, which is why 2024's curve lives here. 2022's
  curve ALSO comes from the state, by locality, via archived captures of the
  retired results site — see `VAScraper.fetch_history`; this file is the
  denser statewide line beside it.

## Idaho, New Mexico, Wyoming — press-release and press-relayed points (2026-10-13)

None of the three published a machine-readable daily file for the 2022 or 2024
general, and none has an archive to walk (Idaho's 2024 tracker was two Tableau
Public embeds that export nothing; New Mexico emails its daily county/party
counts to reporters and never posts them; Wyoming's SoS posted a handful of
statewide press releases in 2024 and none in 2022). What survives is a few
dated statewide numbers:

- **Idaho 2024** — two SoS press releases (10-24: absentee issued/returned,
  no early figure, so `ballots_total` is blank; 10-30: cast = early + absentee).
- **Wyoming 2024** — the 11-04 SoS release; Wyoming's "absentee" is mail and
  in-person absentee combined, so `mail_returned`/`inperson` stay blank.
- **New Mexico 2024 and 2022** — SoS figures as quoted by Source NM, the Las
  Cruces Sun-News, Fox and NM Political Report. "Decline to state" is
  `party_npa`. The 2024-10-29 as-of date is approximate ("one week left"),
  which the note says; rounded or ambiguous press figures were left out.

Each row's `note` names the source. These are the manual tier's intended use:
a number a human read off a release, badged "manual" on the site, never
confused with a scraped series. Their Election-Day finals come from EAVS
(tier 3) and the merge keeps those.
