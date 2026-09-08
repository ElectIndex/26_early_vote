"""New Hampshire: nothing machine-readable exists during the voting period.

This adapter deliberately publishes nothing. That is the finding, not a stub.

New Hampshire has **no early voting** -- RSA 657 allows absentee voting only for
a listed excuse (out of town, disability, religious observance, work, an
incarcerated pre-trial voter, a protective-order participant, or a declared
winter storm), and there is no in-person early-voting period to count. Absentee
ballots are requested from, issued by, and returned to the 300-odd town and city
clerks; the state's ElectioNet system is not public, and the Secretary of State
publishes no statewide absentee request or return file, in any format, at any
cadence, before Election Day.

What was checked, and what is actually there:

* `sos.nh.gov/elections/voters/absentee-ballots` -- instructions, eligibility and
  blank application PDFs. No counts.
* `sos.nh.gov/elections/elections/election-results` and the SoS media library --
  hundreds of .xls/.xlsx files, all of them RESULTS by town and ward, plus
  "names on checklist" registration counts. There is no absentee data file among
  them, before or after any election.
* The SoS news/notices archive through 2024 -- no absentee-count notice.
* There is no `data.nh.gov` open-data portal (it is not a Socrata domain).

The one exception is the reason `tests/fixtures/nh/` exists. For the 2020 general
only, under the COVID emergency, the SoS posted a weekly prose notice,
"Absentee Ballots Requested for General Election", updated every Tuesday:

    September 29, 2020 - 148,630 absentee ballots have been requested statewide.
    October 6, 2020 - 163,974 requests - (34,461 have been returned to clerks)

Statewide only, weekly only, no county, no party, no method -- and it did not
return in 2022 or in 2024. Even if it came back it could fill nothing but
`ballots_total`/`mail_requested` on a StateDay once a week, so no parser for it
is carried here; the fixture keeps the evidence and the test pins the shape.

`fetch` therefore raises NotYetPublished on every call: there is no state file to
be late, so there is nothing to report and nothing to invent. When New Hampshire
starts publishing, this module gets a real `fetch`; until then it must not guess.

**Know what that costs.** NotYetPublished STOPS the ladder (ladder.py rule 1), so
New Hampshire records as `pending` every day and never reaches the aggregator or
the hand-entered manual tier -- and manual.py exists for exactly this state, "a
state that publishes only a PDF, or a press release, or nothing machine-readable
at all". A permanently-absent tier 1 is not the same condition as "today's file
is not posted yet": raising SourceError here instead is the one-word change that
would let tiers 2 and 3 answer.

**That call was re-examined on 2026-09-08 and deliberately left alone**, now with
a harder number attached to it than "a daily warning in the log". Follow
ladder.py's last-rung refinement through: SourceError here falls to civicAPI, to
the aggregator (which carries no NH series), and finally to ManualAdapter, whose
NotYetPublished FROM THE FLOOR is recorded as STATUS_FAILED rather than
STATUS_PENDING. `data/manual/2026_statewide.csv` is a header and no rows, so New
Hampshire would badge `failed` on every run for ever, `ev_status.json`'s failed
count would never be zero again, ingest.yml would print "Every tier failed for:
NH" twice an hour and `ingest --strict` would exit 1 daily -- for a state that
has not failed at anything. The stale badge on the site would say the same. That
is a worse lie than `pending`, and it would drown the signal STATUS_FAILED
exists to carry. If a human ever types an NH number into data/manual/, revisit
this; while that file is empty, `pending` is the true word.

⚠️ **THE OLD OPERATIONAL NOTE HERE WAS WRONG, AND IT WAS THE KIND OF WRONG THAT
STOPS PEOPLE LOOKING.** It read "sos.nh.gov answers automated requests with HTTP
403 regardless of User-Agent". The User-Agent was never the variable. Measured
2026-09-08, two requests four seconds apart:

    requests + DEFAULT_HEADERS  -> 403, 413 b, Akamai "Access Denied"
                                   (errors&#46;edgesuite&#46;net -- entity-encoded,
                                   which is why _net.WALL_MARKERS lists both forms)
    curl_cffi impersonate=chrome -> 200, 2,960,857 b, the real page, after a
                                   redirect to /elections/absentee-ballots

sos.nh.gov is behind Akamai and Akamai is scoring the TLS fingerprint, so
`_net.get`'s existing 403 -> impersonate retry already walks straight through it.
**New Hampshire is reachable.** The absentee page was re-read from behind the
wall on 2026-09-08 and carries exactly three PDFs -- two blank applications and
the marking-and-mailing instructions -- and no counts, so every finding above is
now confirmed against the live page rather than inferred through a 403. The
emptiness of this adapter is a fact about what New Hampshire publishes, not an
artefact of what we could reach.
"""

from __future__ import annotations

from datetime import date

from ..schema import TIER_SCRAPER
from .base import Adapter, FetchResult, NotYetPublished

#: Documentation only -- nothing here is fetched. These are the pages checked
#: for a machine-readable absentee feed, all VERIFIED to exist and VERIFIED not
#: to carry one. The last entry is the discontinued 2020 notice, preserved via
#: the Internet Archive because the live URL is gone.
#:
#: RE-READ 2026-09-08 through the impersonated fingerprint (see the docstring):
#: the first now redirects to /elections/absentee-ballots and serves 2,960,857
#: bytes carrying three PDFs -- absentee-ballot-application-local-election,
#: absentee-ballot-application-state-election and
#: absentee-ballot-marking-and-mailing-instructions -- and no counts of anything.
SOURCES = (
    "https://www.sos.nh.gov/elections/voters/absentee-ballots",
    "https://www.sos.nh.gov/elections/elections/election-results",
    "https://web.archive.org/web/20210118200612/"
    "https://sos.nh.gov/elections/information/notices/"
    "absentee-ballots-requested-for-general-election/",
)

REASON = (
    "NH has no early voting and its Secretary of State publishes no statewide "
    "absentee request or return file during the voting period; the only counts "
    "ever posted were weekly statewide prose in 2020, discontinued after that "
    "cycle. There is no machine-readable source to scrape."
)


class NHScraper(Adapter):
    """Tier 1 for New Hampshire: intentionally empty. See the module docstring."""

    state = "NH"
    name = "nh-sos"
    tier = TIER_SCRAPER

    def fetch(self, cycle: int, as_of: date) -> FetchResult:
        raise NotYetPublished(f"NH: no source for {cycle}. {REASON}")

    def fetch_history(self, cycle: int) -> FetchResult:
        raise NotYetPublished(f"NH: no archived daily file for {cycle}. {REASON}")
