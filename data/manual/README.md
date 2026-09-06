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
