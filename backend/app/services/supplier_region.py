"""Each supplier's region, straight from the Oracle ERP.

The payables-side reports (Unaccounted Transactions, Pending MRN, Uninvoiced
Expense POs, and Loans & Advance / IOCL / TDS) used to take a row's Location
from its supplier SITE alone, through the suite's own site mapping. The ERP
also holds a region against some suppliers (``hz_parties.attribute5``, named in
``xxrdc_region_details``, reached from the supplier through ``party_id``), the
same field the receivables reports read for customers (see ``customer_region``).

The order for a row is now the one the receivables reports use:

  1. the supplier's ERP region, when the ERP holds one - the ERP is the source of truth;
  2. otherwise the suite's own mapping of the supplier site (as before);
  3. only when neither knows the row, the "mapping not found" popup.

The ERP's region names are not the labels these reports have always carried
(the ERP says "Delhi" where the reports say "NCR + Delhi"), so a region is turned
into the report's own label before it is used (``LABEL_FOR_REGION``): both
spellings then land on the same pivot row, and the Accounts Incharge that is
already kept against that label applies. A region the table does not know keeps
the ERP's own spelling.
"""

from __future__ import annotations

import logging

import pandas as pd

from app.services import customer_region

logger = logging.getLogger(__name__)

# One row per supplier: its number (as the reports print it) and its region.
SUPPLIER_REGION_SQL = """
SELECT
    s.segment1 AS supplier_number,
    xrd.region_name
FROM
    apps.ap_suppliers           s,
    apps.hz_parties             hp,
    apps.xxrdc_region_details   xrd
WHERE
        s.party_id      = hp.party_id
    AND hp.attribute5   = xrd.region_id
"""

# The ERP region -> the Location label these reports use. Taken from how the suite's own site mapping
# already places those regions' suppliers (e.g. 145 of 207 sites of "Delhi" suppliers are "NCR + Delhi").
LABEL_FOR_REGION: dict[str, str] = {
    "Andhra Pradesh": "ANDHRA PRADESH",
    "Assam": "ASSAM",
    "Aurangabad": "SAMBHAJI NAGAR",
    "Bangalore": "BANGALORE",
    "Bhopal": "BHOPAL",
    "Chattisgarh": "CHHATTISGARH",
    "Chennai": "CHENNAI",
    "Coimbatore": "COIMBATORE + TRICHY",
    "Delhi": "NCR + Delhi",
    "Goa": "GOA",
    "Gujarat": "GUJARAT",
    "Hyderabad": "HYDERABAD",
    "Indore": "INDORE",
    "Jammu": "JAMMU",
    "Jharkhand": "ODISHA + RANCHI",
    "Kerala": "KERALA",
    "Kolkata": "WEST BENGAL",
    "Mangalore": "MANGALORE",
    "Mumbai": "MUMBAI",
    "Nagpur": "NAGPUR",
    "Odisha- Commercial": "ODISHA + RANCHI",
    "Odisha-Captive": "ODISHA + RANCHI",
    "Patna": "BIHAR",
    "Pune": "PUNE",
    "Punjab": "PUNJAB",
    "Rajasthan": "RAJASTHAN",
    "Uttar Pradesh": "UTTAR PRADESH",
    "Uttarakhand": "UTTARAKHAND",
}
_LABEL_BY_FOLD = {customer_region._fold(region): label for region, label in LABEL_FOR_REGION.items()}

# the column that holds the supplier's number in each export (compared without regard to case)
NUMBER_COLUMNS = ("supplier number", "vendor number")


def label_for(region) -> str:
    """The Location label a report uses for an ERP region."""
    name = customer_region.canonical_region(region)
    return _LABEL_BY_FOLD.get(customer_region._fold(name), name)


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch_supplier_regions(oracle_cfg, cancel_event=None) -> dict[str, str]:
    """{supplier number -> ERP region} for every supplier the ERP has a region for. Raises on a
    connection or query failure."""
    rows = customer_region.fetch_rows(oracle_cfg, SUPPLIER_REGION_SQL, cancel_event)
    return customer_region.regions_from_rows(rows)


def fetch_for_job(messages: list | None = None) -> dict[str, str]:
    """The supplier regions for a report that is about to run. A failure never stops a report: without
    regions every row simply uses the site mapping, and the log says so.

    ``messages`` (optional): a list the (level, text) log lines are appended to."""
    def say(level: str, text: str) -> None:
        if messages is not None:
            messages.append((level, text))

    try:
        from app.services import erp_ageing

        regions = fetch_supplier_regions(erp_ageing.oracle_config())
    except Exception as exc:  # noqa: BLE001 - reported in the log, never raised
        logger.warning("Supplier region lookup failed: %s", exc)
        say("warn", f"Could not read supplier regions from the ERP - the site mapping is used for every row ({exc})")
        return {}
    say("ok", f"Read the region of {len(regions):,} suppliers from the ERP")
    return regions


# ── applying ─────────────────────────────────────────────────────────────────

def find_number_column(columns) -> str | None:
    """The supplier-number column of an export, whatever its capitalisation."""
    for name in columns:
        if str(name).strip().lower() in NUMBER_COLUMNS:
            return name
    return None


def apply_supplier_regions(df: pd.DataFrame, number_col, location_col: str, incharge_col: str,
                           region_by_supplier: dict[str, str] | None,
                           incharge_by_location: dict[str, str] | None) -> dict:
    """Put the supplier's ERP region (as the report's label) in ``location_col`` for every row whose
    supplier the ERP has a region for, and the Accounts Incharge kept against that label in
    ``incharge_col``. Rows the ERP has nothing for are left exactly as the site mapping made them.
    Changes ``df`` in place.

    Returns {"from_erp": rows given a Location by the ERP, "changed": of those, the rows whose Location
    the site mapping had given differently (or not at all), "no_incharge": the labels with no Accounts Incharge}."""
    stats = {"from_erp": 0, "changed": 0, "no_incharge": []}
    if not region_by_supplier or number_col is None or number_col not in df.columns or df.empty:
        return stats
    numbers = df[number_col]
    region = numbers.map({raw: region_by_supplier.get(customer_region.normalise_account(raw))
                          for raw in numbers.unique()})
    has = region.notna()
    if not has.any():
        return stats

    label = region[has].map(label_for)
    by_fold = {customer_region._fold(name): who for name, who in (incharge_by_location or {}).items() if who}
    incharge = label.map(lambda name: by_fold.get(customer_region._fold(name), ""))
    old_location = df.loc[has, location_col].fillna("").astype(str)
    # a label with no incharge on file keeps the one the site mapping gave - but only when that mapping
    # placed the row in this very Location
    carry = (incharge == "") & (old_location == label)
    incharge = incharge.where(~carry, df.loc[has, incharge_col])

    df.loc[has, location_col] = label
    df.loc[has, incharge_col] = incharge
    stats["from_erp"] = int(has.sum())
    stats["changed"] = int((old_location != label).sum())
    stats["no_incharge"] = sorted({str(name) for name in label[incharge.fillna("") == ""]})
    return stats


def apply_with_log(df: pd.DataFrame, location_col: str, incharge_col: str, region_by_supplier,
                   incharge_by_location, log_q) -> dict:
    """``apply_supplier_regions`` for the export's own supplier-number column, with the log lines the
    reports show (``log_q.put((level, text))``)."""
    if not region_by_supplier:
        return {"from_erp": 0, "changed": 0, "no_incharge": []}
    number_col = find_number_column(df.columns)
    if number_col is None:
        log_q.put(("info", "No supplier number column in this file - the Location comes from the supplier site mapping"))
        return {"from_erp": 0, "changed": 0, "no_incharge": []}
    stats = apply_supplier_regions(df, number_col, location_col, incharge_col, region_by_supplier, incharge_by_location)
    log_q.put(("info", f"Location from the supplier's ERP region for {stats['from_erp']:,} of {len(df):,} rows "
                       f"({stats['changed']:,} of them different from the site mapping); "
                       "the rest from the supplier site mapping"))
    if stats["no_incharge"]:
        log_q.put(("warn", "No Accounts Incharge on file for: " + ", ".join(stats["no_incharge"])
                           + " - add it under the Location / Incharge mapping"))
    return stats
