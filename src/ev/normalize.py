"""Shared vocabulary: party, method and demographic bucket labels.

Every state spells these differently -- NC says "DEM"/"UNA", Nevada says
"Democratic"/"Non-Partisan", Colorado says "DEM"/"UAF", Pennsylvania says "D"/"NF".
Adapters must route every raw label through here rather than inventing their own
spellings, or the frontend has to know 15 vocabularies instead of one.

An UNRECOGNISED label is deliberately NOT silently bucketed into "other". A new
minor party appearing in a file is fine; a column we misread and are quietly
dumping into `party_oth` is a data-quality bug that would never surface. Callers
get `None` back and are expected to raise SchemaDrift.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Party registration
# --------------------------------------------------------------------------
PARTY_DEM = "dem"
PARTY_REP = "rep"
PARTY_NPA = "npa"   # unaffiliated / no party affiliation / independent-of-no-party
PARTY_OTH = "oth"   # a real third party (Libertarian, Green, Working Families...)

PARTY_BUCKETS = (PARTY_DEM, PARTY_REP, PARTY_NPA, PARTY_OTH)

_PARTY_MAP = {
    # Democratic
    "d": PARTY_DEM, "dem": PARTY_DEM, "democrat": PARTY_DEM, "democratic": PARTY_DEM,
    "democratic party": PARTY_DEM, "dem party": PARTY_DEM,
    # Republican
    "r": PARTY_REP, "rep": PARTY_REP, "republican": PARTY_REP, "gop": PARTY_REP,
    "republican party": PARTY_REP,
    # Unaffiliated / no party. NOTE: these are NOT "other" -- an unaffiliated
    # voter has declined a party, a Libertarian has chosen one, and collapsing
    # them loses the single most-watched number in every early-vote story.
    "u": PARTY_NPA, "una": PARTY_NPA, "unaffiliated": PARTY_NPA,
    "uaf": PARTY_NPA, "npa": PARTY_NPA, "no party affiliation": PARTY_NPA,
    "non-partisan": PARTY_NPA, "nonpartisan": PARTY_NPA, "np": PARTY_NPA,
    "no party": PARTY_NPA, "none": PARTY_NPA, "n": PARTY_NPA,
    "no party preference": PARTY_NPA, "npp": PARTY_NPA, "independent": PARTY_NPA,
    "ind": PARTY_NPA, "i": PARTY_NPA, "nf": PARTY_NPA, "no affiliation": PARTY_NPA,
    "decline to state": PARTY_NPA, "dts": PARTY_NPA, "other/none": PARTY_NPA,
    # Real third parties
    "l": PARTY_OTH, "lib": PARTY_OTH, "libertarian": PARTY_OTH,
    "g": PARTY_OTH, "grn": PARTY_OTH, "green": PARTY_OTH,
    "con": PARTY_OTH, "constitution": PARTY_OTH, "conservative": PARTY_OTH,
    "wf": PARTY_OTH, "working families": PARTY_OTH,
    "ain": PARTY_OTH, "americans elect": PARTY_OTH, "reform": PARTY_OTH,
    "socialist": PARTY_OTH, "unity": PARTY_OTH, "forward": PARTY_OTH,
    "o": PARTY_OTH, "oth": PARTY_OTH, "other": PARTY_OTH, "minor": PARTY_OTH,
    "all others": PARTY_OTH, "misc": PARTY_OTH,
}


def party(raw: str | None) -> str | None:
    """Map a state's party label onto one of PARTY_BUCKETS, or None if unknown.

    None means "I do not recognise this" -- the adapter should raise SchemaDrift.
    It does NOT mean "other".
    """
    if raw is None:
        return None
    key = " ".join(str(raw).strip().lower().split())
    if not key:
        return None
    return _PARTY_MAP.get(key)


# --------------------------------------------------------------------------
# Voting method
# --------------------------------------------------------------------------
METHOD_MAIL = "mail"        # a returned/accepted mail or absentee ballot
METHOD_INPERSON = "inperson"  # cast in person during the early-voting period

_METHOD_MAP = {
    "mail": METHOD_MAIL, "by mail": METHOD_MAIL, "absentee": METHOD_MAIL,
    "absentee by mail": METHOD_MAIL, "mail ballot": METHOD_MAIL,
    "mail-in": METHOD_MAIL, "mail in": METHOD_MAIL, "vbm": METHOD_MAIL,
    "vote by mail": METHOD_MAIL, "vote-by-mail": METHOD_MAIL,
    "absentee mail": METHOD_MAIL, "civilian mail": METHOD_MAIL,
    "mail ballot returned": METHOD_MAIL, "returned": METHOD_MAIL,
    "in person": METHOD_INPERSON, "in-person": METHOD_INPERSON,
    "inperson": METHOD_INPERSON, "early voting": METHOD_INPERSON,
    "early vote": METHOD_INPERSON, "one stop": METHOD_INPERSON,
    "one-stop": METHOD_INPERSON, "in person early": METHOD_INPERSON,
    "absentee one-stop": METHOD_INPERSON, "advance": METHOD_INPERSON,
    "advance voting": METHOD_INPERSON, "advanced voting": METHOD_INPERSON,
    "in-person absentee": METHOD_INPERSON, "early in person": METHOD_INPERSON,
    "evip": METHOD_INPERSON, "early voting center": METHOD_INPERSON,
}


def method(raw: str | None) -> str | None:
    """Map a state's method label onto METHOD_MAIL/METHOD_INPERSON, or None."""
    if raw is None:
        return None
    key = " ".join(str(raw).strip().lower().split())
    if not key:
        return None
    return _METHOD_MAP.get(key)


# --------------------------------------------------------------------------
# Demographics
# --------------------------------------------------------------------------
#: Fixed age bands. Every state's raw ages are bucketed into these so the
#: frontend renders one consistent set of bars regardless of source granularity.
AGE_BANDS = ("18-24", "25-34", "35-44", "45-54", "55-64", "65+")

RACE_BUCKETS = ("white", "black", "hispanic", "asian", "native", "other", "unknown")
SEX_BUCKETS = ("female", "male", "unknown")

_RACE_MAP = {
    "w": "white", "wh": "white", "white": "white",
    "white non-hispanic": "white", "caucasian": "white",
    "b": "black", "bl": "black", "black": "black", "african american": "black",
    "black or african american": "black", "black non-hispanic": "black",
    "h": "hispanic", "hl": "hispanic", "hispanic": "hispanic",
    "hispanic or latino": "hispanic", "latino": "hispanic",
    "a": "asian", "as": "asian", "asian": "asian",
    "asian american": "asian", "pacific islander": "asian",
    "asian or pacific islander": "asian", "ap": "asian",
    "native american": "native", "american indian": "native", "ai": "native",
    "american indian or alaska native": "native", "in": "native",
    "o": "other", "oth": "other", "other": "other", "multi-racial": "other",
    "two or more races": "other", "m": "other", "mu": "other",
    "u": "unknown", "un": "unknown", "unknown": "unknown",
    "undesignated": "unknown", "not designated": "unknown", "": "unknown",
}

_SEX_MAP = {
    "f": "female", "female": "female", "w": "female", "woman": "female",
    "m": "male", "male": "male", "man": "male",
    "u": "unknown", "unknown": "unknown", "undesignated": "unknown",
    "n": "unknown", "x": "unknown", "other": "unknown", "": "unknown",
}


def age_band(value) -> str | None:
    """Bucket a raw age (int, numeric string, or an already-banded label).

    Accepts an age in years or a range label like "18-24"/"65+"/"65 and over".
    Returns None for anything unrecognised so the adapter can raise SchemaDrift.
    """
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in AGE_BANDS:
        return raw

    # A bare age in years.
    if raw.isdigit():
        age = int(raw)
        if age < 18:
            return None  # not eligible; a real one of these means we misread a column
        for band in AGE_BANDS[:-1]:
            lo, hi = (int(x) for x in band.split("-"))
            if lo <= age <= hi:
                return band
        return "65+"

    # A range label in some other spelling.
    if "65" in raw and any(w in raw for w in ("+", "over", "older", "plus", "and up")):
        return "65+"
    m = re.match(r"^(\d{2})\s*(?:-|to|–)\s*(\d{2})$", raw)
    if m:
        lo = int(m.group(1))
        for band in AGE_BANDS[:-1]:
            blo, bhi = (int(x) for x in band.split("-"))
            if blo <= lo <= bhi:
                return band
        return "65+"
    return None


def race(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _RACE_MAP.get(" ".join(str(raw).strip().lower().split()))


def sex(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _SEX_MAP.get(" ".join(str(raw).strip().lower().split()))


# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------
STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08",
    "CT": "09", "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15",
    "ID": "16", "IL": "17", "IN": "18", "IA": "19", "KS": "20", "KY": "21",
    "LA": "22", "ME": "23", "MD": "24", "MA": "25", "MI": "26", "MN": "27",
    "MS": "28", "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38", "OH": "39",
    "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53",
    "WV": "54", "WI": "55", "WY": "56",
}


def county_fips(state: str, county_code: str | int) -> str:
    """Compose a 5-digit county FIPS from a state code and a 3-digit county code.

    The site's county geometry is keyed by 5-digit FIPS, so county rows must be
    too -- county NAMES do not join reliably (Louisiana has parishes, Alaska has
    boroughs, and "St." vs "Saint" alone breaks a name join in six states).
    """
    st = STATE_FIPS[state.upper()]
    code = str(county_code).strip()
    if not code.isdigit():
        raise ValueError(f"non-numeric county code {county_code!r} for {state}")
    return f"{st}{int(code):03d}"
