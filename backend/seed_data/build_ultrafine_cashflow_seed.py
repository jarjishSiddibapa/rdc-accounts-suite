"""One-off generator for ultrafine-cashflow-seed.json.

Usage (from backend/):
    python seed_data/build_ultrafine_cashflow_seed.py <out.json> <cashflow.xlsx> [<cashflow.xlsx> ...]

Reads the October "UF Cashflow" workbook (Payments / Receipts / Summary
sheets; October is the latest and most reliable) and distils the mappings the
team had been keeping by hand:

  * description -> name   (as normalized description keys; a key that is
                           mostly one name is seeded with that name, a truly
                           split key is NOT seeded)
  * name -> location AND CF location, both stored on the name. A name whose
                           lines go to different CF locations (Salary Payable,
                           TDS Payable ...) gets none: it is chosen per line.
  * sharing groups (RDC, IOCL, ETC Pool) and their ratios - the percentages
                           the team supplied for October (RATIO_SETS below)
  * Summary-sheet settings (authorized person, payable / reserve %)

Not part of the app at runtime; the app only reads the JSON it writes.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.ultrafine_cashflow.keys import casefold_key, clean_name, description_key  # noqa: E402

LOCATION_ALIASES = {"aggreagte": "Aggregate", "tamilnadu": "Tamil Nadu"}

SHARING_PARTY_PATTERNS = (
    ("RDC", re.compile(r"^rdc concrete")),
    ("IOCL", re.compile(r"^indian oil corporation")),
    ("ETC Pool", re.compile(r"^etc pool")),
    # The HDFC card that pays IOCL fuel is allocated with the IOCL ratio.
    ("IOCL", re.compile(r"^hdfc iocl credit card")),
)


# Each sharing group's ratio lines: (particular, CF location it goes to, %).
# A particular that is itself a CF location needs no explicit destination.
RATIO_EFFECTIVE_FROM = "2026-10-01T00:00:00"
RATIO_SETS = {
    "RDC": [("Aggregate Pachami Trading", None, 7), ("Aggregate Panagarh Crusher", None, 6),
            ("Flyash Business", None, 17), ("Main Operation", None, 47), ("Trading Business", None, 23)],
    "IOCL": [("Main", "Main Operation", 29), ("Flyash", "Flyash Business", 21),
             ("Aggregate", "Aggregate Panagarh Crusher", 50)],
    "ETC Pool": [("Main", "Main Operation", 14), ("Aggregate", "Aggregate Pachami Trading", 86)],
}


def _canon_location(value) -> str | None:
    text = clean_name(value)
    if not text or text in {"-", "nan", "None"}:
        return None
    return LOCATION_ALIASES.get(text.casefold(), text)


def _rows(ws, header_word="Description"):
    rows = list(ws.iter_rows(values_only=True))
    start = next(i for i, r in enumerate(rows) if r and header_word in [str(x) for x in r])
    header = [str(x) if x is not None else f"c{j}" for j, x in enumerate(rows[start])]
    for r in rows[start + 1:]:
        yield dict(zip(header, r))


_MONTH_TAGS = {"aug": "2026-08", "sept": "2026-09", "oct": "2026-10"}


def _month_of_file(file: str) -> str:
    name = Path(file).name.lower()
    return next(month for tag, month in _MONTH_TAGS.items() if tag in name)


def _expr(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value).lstrip("=").strip()


def _plan_from_summary(ws, month: str) -> dict:
    """Sections and items under the 'Particulars | Amt in Lacs' header."""
    header = next((r for r in range(1, ws.max_row + 1) if ws.cell(r, 4).value == "Particulars"), None)
    if header is None:
        return {"month": month, "sections": []}
    sections: list[dict] = []
    current = None
    gap = True
    block_no = 1
    for r in range(header + 1, ws.max_row + 1):
        label, value = ws.cell(r, 4).value, ws.cell(r, 5).value
        if not label:
            gap = True
            continue
        label = clean_name(label)
        if label == "Total":
            block_no += 1
            current, gap = None, True
            continue
        if value is None:
            current = {"block_no": block_no, "title": label, "items": []}
            sections.append(current)
            gap = False
            continue
        if current is None or gap:
            current = {"block_no": block_no, "title": "", "items": []}
            sections.append(current)
        current["items"].append({"label": label, "amount_expr": _expr(value)})
        gap = False
    return {"month": month, "sections": sections}


def main(out_path: str, files: list[str]) -> None:
    cf_votes: dict[str, Counter] = defaultdict(Counter)          # location -> cf
    party_loc: dict[str, Counter] = defaultdict(Counter)         # party key -> location
    party_cf: dict[str, Counter] = defaultdict(Counter)          # party key -> cf
    party_spell: dict[str, Counter] = defaultdict(Counter)
    key_parties: dict[str, Counter] = defaultdict(Counter)
    cf_names: dict[str, Counter] = defaultdict(Counter)

    for file in files:
        wb = openpyxl.load_workbook(file, data_only=True)
        for sheet, loc_col in (("Payments", "Location"), ("Receipts", "Tally Location")):
            for row in _rows(wb[sheet]):
                amount, desc, name = row.get("Amount"), row.get("Description"), row.get("Name")
                if not isinstance(amount, (int, float)) or not desc or str(desc).strip() in {"-", "Description"}:
                    continue
                cf = _canon_location(row.get("CF Location"))
                loc = _canon_location(row.get(loc_col))
                if cf:
                    cf_names[casefold_key(cf)][cf] += 1
                if loc and cf:
                    cf_votes[casefold_key(loc)][casefold_key(cf)] += 1
                    cf_names[casefold_key(loc)][loc] += 1
                if not name or not clean_name(name):
                    continue
                pkey = casefold_key(name)
                party_spell[pkey][clean_name(name)] += 1
                key_parties[description_key(desc)][pkey] += 1
                if loc:
                    party_loc[pkey][casefold_key(loc)] += 1
                if cf:
                    party_cf[pkey][casefold_key(cf)] += 1

    def display(folded: str) -> str:
        return cf_names[folded].most_common(1)[0][0] if cf_names.get(folded) else folded.title()

    # Summary-sheet settings come from the newest file's live block.
    summary_cfg = []
    wb = openpyxl.load_workbook(files[-1], data_only=False)
    ws = wb["Summary"]
    live_header = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 3).value == "Location" and ws.cell(r, 5).value)
    group_for_row = {}
    order = 0
    for r in range(live_header + 1, ws.max_row + 1):
        name = ws.cell(r, 3).value
        if not name:
            if str(ws.cell(r, 4).value or "").startswith("Total "):
                label = str(ws.cell(r, 4).value)[6:].strip()
                for item in summary_cfg[-2:]:
                    item["group"] = label
            if str(ws.cell(r, 3).value or "").strip() == "Total":
                break
            continue
        if str(name).strip() == "Total":
            break
        formula = str(ws.cell(r, 7).value or "")
        pct = re.search(r"(\d+(?:\.\d+)?)%", formula)
        reserve = re.search(r"(\d+(?:\.\d+)?)%", str(ws.cell(r, 8).value or ""))
        order += 1
        summary_cfg.append({
            "name": clean_name(name), "sort_order": order,
            "authorized_person": clean_name(ws.cell(r, 4).value or ""),
            "payable_pct": float(pct.group(1)) if pct else 100.0,
            "reserve_pct": float(reserve.group(1)) if reserve else None,
            "group": None,
        })
    # Groups apply to the two rows directly above their "Total X" row.
    cf_all = {casefold_key(c["name"]): c for c in summary_cfg}

    # CF locations: Summary rows first, then any others seen in the data.
    cf_locations = list(summary_cfg)
    for folded, names in cf_names.items():
        pass
    seen_cf = {casefold_key(c["name"]) for c in cf_locations}
    for votes in cf_votes.values():
        for folded in votes:
            if folded not in seen_cf:
                seen_cf.add(folded)
                cf_locations.append({"name": display(folded), "sort_order": len(cf_locations) + 1,
                                     "authorized_person": "", "payable_pct": 100.0, "reserve_pct": None,
                                     "group": None, "show_in_summary": False})

    cf_display = {casefold_key(c["name"]): c["name"] for c in cf_locations}

    parties = []
    party_group = {}
    location_names: dict[str, str] = {}
    for pkey, spellings in sorted(party_spell.items()):
        name = spellings.most_common(1)[0][0]
        group = next((g for g, pat in SHARING_PARTY_PATTERNS if pat.match(pkey)), None)
        entry = {"name": name, "location": None, "cf_location": None, "sharing_group": group}
        cfs = party_cf.get(pkey, Counter())
        # One CF location for every line (a location typed differently on one
        # row is a typo, not a second place); otherwise it is chosen per line.
        if group is None and len(cfs) == 1 and party_loc.get(pkey):
            loc_folded = party_loc[pkey].most_common(1)[0][0]
            entry["location"] = display(loc_folded)
            entry["cf_location"] = cf_display.get(next(iter(cfs)))
            location_names[loc_folded] = entry["location"]
        party_group[pkey] = group
        parties.append(entry)
    for _, lines in RATIO_SETS.items():
        for particular, _cf, _pct in lines:
            location_names.setdefault(casefold_key(particular), particular)
    locations = [{"name": n} for _, n in sorted(location_names.items())]

    aliases = []
    skipped = []
    spelled = {pkey: spellings.most_common(1)[0][0] for pkey, spellings in party_spell.items()}
    for key, counts in sorted(key_parties.items()):
        if not key or re.fullmatch(r"[\d\s/:.-]+", key):
            continue   # empty, or a stray date/time typed into a Description cell
        pkeys = set(counts)
        if len(pkeys) == 1:
            aliases.append({"desc_key": key, "party": spelled[next(iter(pkeys))]})
            continue
        # Several party names, but if they all allocate identically (same
        # sharing group, or same location and CF location) the choice cannot
        # change any figure - seed the most frequent one.
        fingerprints = {
            (party_group.get(p), None if party_group.get(p) else (
                party_loc[p].most_common(1)[0][0] if party_loc.get(p) else None,
                party_cf[p].most_common(1)[0][0] if party_cf.get(p) else None))
            for p in pkeys
        }
        top, top_count = counts.most_common(1)[0]
        if len(fingerprints) == 1:
            aliases.append({"desc_key": key, "party": spelled[top]})
        elif top_count / sum(counts.values()) > 0.5:
            # Mostly one name (e.g. the Ultratech "FT - P2100P2100" receipts,
            # or "FT - TEAMLEASE SALARY"). The rare exceptions are fixed on
            # that one line, so seed the dominant name.
            aliases.append({"desc_key": key, "party": spelled[top]})
        else:
            skipped.append(key)

    ratio_sets = {
        group: [{"effective_from": RATIO_EFFECTIVE_FROM,
                 "lines": [{"location": particular, "ratio": pct / 100, "cf_location": cf}
                           for particular, cf, pct in lines]}]
        for group, lines in RATIO_SETS.items()
    }

    # Planning block of the newest file (the "Particulars / Amt in Lacs" table).
    plan = _plan_from_summary(openpyxl.load_workbook(files[-1], data_only=False)["Summary"],
                              _month_of_file(files[-1]))
    out = {
        "summary_groups": sorted({c["group"] for c in cf_locations if c.get("group")}),
        "cf_locations": cf_locations,
        "locations": locations,
        "sharing_groups": list(ratio_sets),
        "parties": parties,
        "aliases": aliases,
        "ratio_sets": ratio_sets,
        "plan": plan,
        "skipped_ambiguous_keys": skipped,
    }
    Path(out_path).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"cf_locations={len(cf_locations)} locations={len(locations)} parties={len(parties)} "
          f"aliases={len(aliases)} ambiguous_skipped={len(skipped)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
