"""One-off helper: append curated small/mid-cap names to ticker_universe.csv.

Appended names are common, liquid US-listed non-S&P-500 tickers that tend
to trade below ~$30/share — chosen to give option 3 ("$N this week") a
meaningful pool at low budgets. Sector/industry follow GICS-style labels
consistent with the existing rows.
"""

from __future__ import annotations

import csv
from pathlib import Path


# (ticker, name, sector, industry)
CURATED: list[tuple[str, str, str, str]] = [
    # Consumer / retail
    ("AAL",  "American Airlines",          "Industrials",            "Passenger Airlines"),
    ("AMC",  "AMC Entertainment Holdings", "Communication Services", "Movies & Entertainment"),
    ("CHWY", "Chewy",                       "Consumer Discretionary", "Internet & Direct Marketing Retail"),
    ("DKNG", "DraftKings",                  "Consumer Discretionary", "Casinos & Gaming"),
    ("DNUT", "Krispy Kreme",                "Consumer Discretionary", "Restaurants"),
    ("FUBO", "fuboTV",                      "Communication Services", "Movies & Entertainment"),
    ("GME",  "GameStop",                    "Consumer Discretionary", "Specialty Retail"),
    ("KSS",  "Kohl's",                      "Consumer Discretionary", "Department Stores"),
    ("M",    "Macy's",                      "Consumer Discretionary", "Apparel Retail"),
    ("PINS", "Pinterest",                   "Communication Services", "Interactive Media & Services"),
    ("SIRI", "Sirius XM Holdings",          "Communication Services", "Broadcasting"),
    ("SNAP", "Snap Inc.",                   "Communication Services", "Interactive Media & Services"),
    ("WBA",  "Walgreens Boots Alliance",    "Consumer Staples",       "Drug Retail"),

    # EV / mobility / cleantech
    ("CHPT", "ChargePoint Holdings",        "Industrials",            "Electrical Components & Equipment"),
    ("JOBY", "Joby Aviation",               "Industrials",            "Aerospace & Defense"),
    ("LCID", "Lucid Group",                 "Consumer Discretionary", "Automobile Manufacturers"),
    ("LYFT", "Lyft",                        "Industrials",            "Passenger Ground Transportation"),
    ("NIO",  "NIO Inc.",                    "Consumer Discretionary", "Automobile Manufacturers"),
    ("PLUG", "Plug Power",                  "Industrials",            "Electrical Components & Equipment"),
    ("QS",   "QuantumScape",                "Consumer Discretionary", "Automobile Components"),
    ("RIVN", "Rivian Automotive",           "Consumer Discretionary", "Automobile Manufacturers"),
    ("XPEV", "XPeng",                       "Consumer Discretionary", "Automobile Manufacturers"),

    # Fintech / crypto-adjacent
    ("AFRM", "Affirm Holdings",             "Financials",             "Consumer Finance"),
    ("BITF", "Bitfarms",                    "Financials",             "Capital Markets"),
    ("CLSK", "CleanSpark",                  "Financials",             "Capital Markets"),
    ("MARA", "Marathon Digital Holdings",   "Financials",             "Capital Markets"),
    ("NU",   "Nu Holdings",                 "Financials",             "Diversified Banks"),
    ("RIOT", "Riot Platforms",              "Financials",             "Capital Markets"),
    ("RKT",  "Rocket Companies",            "Financials",             "Mortgage Finance"),
    ("SOFI", "SoFi Technologies",           "Financials",             "Consumer Finance"),
    ("WULF", "TeraWulf",                    "Financials",             "Capital Markets"),

    # Tech / software / "science"-adjacent
    ("BB",   "BlackBerry",                  "Information Technology", "Application Software"),
    ("BBAI", "BigBear.ai",                  "Information Technology", "Application Software"),
    ("PATH", "UiPath",                      "Information Technology", "Application Software"),
    ("RUM",  "Rumble",                      "Communication Services", "Interactive Media & Services"),
    ("U",    "Unity Software",              "Information Technology", "Application Software"),

    # Biotech / pharma small caps (often relevant for "science" theme)
    ("ALEC", "Alector",                     "Health Care",            "Biotechnology"),
    ("CLOV", "Clover Health",               "Health Care",            "Managed Health Care"),
    ("CRSP", "CRISPR Therapeutics",         "Health Care",            "Biotechnology"),
    ("EDIT", "Editas Medicine",             "Health Care",            "Biotechnology"),
    ("HIMS", "Hims & Hers Health",          "Health Care",            "Health Care Services"),
    ("IONS", "Ionis Pharmaceuticals",       "Health Care",            "Biotechnology"),
    ("IOVA", "Iovance Biotherapeutics",     "Health Care",            "Biotechnology"),
    ("NVAX", "Novavax",                     "Health Care",            "Biotechnology"),
    ("OPK",  "OPKO Health",                 "Health Care",            "Health Care Distributors"),
    ("TLRY", "Tilray Brands",               "Health Care",            "Pharmaceuticals"),

    # Energy / commodities
    ("DNN",  "Denison Mines",               "Energy",                 "Uranium Mining"),
    ("ET",   "Energy Transfer",             "Energy",                 "Oil & Gas Storage & Transportation"),
    ("RIG",  "Transocean",                  "Energy",                 "Oil & Gas Drilling"),
    ("VALE", "Vale S.A.",                   "Materials",              "Diversified Metals & Mining"),

    # Real estate / other
    ("OPEN", "Opendoor Technologies",       "Real Estate",            "Real Estate Services"),
    ("ZIM",  "ZIM Integrated Shipping",     "Industrials",            "Marine Transportation"),
]


def main() -> int:
    path = Path("tradingagents/agent_assist/data/ticker_universe.csv")
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    existing = {r[0] for r in rows[1:]}

    to_add = [r for r in CURATED if r[0] not in existing]
    skipped = [r[0] for r in CURATED if r[0] in existing]

    if not to_add:
        print(f"Nothing new to add ({len(skipped)} already present).")
        return 0

    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\r\n")
        for row in to_add:
            writer.writerow(row)

    print(f"Appended {len(to_add)} rows. Skipped {len(skipped)} duplicates: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
