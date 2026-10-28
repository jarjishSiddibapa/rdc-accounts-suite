"""Each customer's region, straight from the Oracle ERP.

The ERP holds a region against every customer (``hz_parties.attribute5``,
named in ``xxrdc_region_details``). The Untagged Invoices Report and the
Unapplied Receipts Report use it as the row's Location: it is the customer's
own, so it needs no mapping. The "Location Name -> Location" mappings kept in
this suite stay as the fallback, for the older customers the ERP has no region
for.

The Location values are the ERP's own region names (``ERP_REGIONS``).
``migrate_mappings_to_erp_regions`` brings the mapping tables already in the
database over to those names once, so both sources give the same spelling.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter, defaultdict

import pandas as pd

from app.jobs import JobCancelled

logger = logging.getLogger(__name__)

# The region query, reduced to the two columns that matter (the bill-to,
# address and sales-rep joins of the full customer query add nothing to a
# region and make it slower). One row per customer account.
CUSTOMER_REGION_SQL = """
SELECT
    hca.account_number,
    xrd.region_name
FROM
    apps.hz_cust_accounts       hca,
    apps.hz_parties             hp,
    apps.xxrdc_region_details   xrd
WHERE
        hca.party_id    = hp.party_id
    AND hp.attribute5   = xrd.region_id
"""

# The ERP's regions, spelled as the ERP spells them.
ERP_REGIONS: tuple[str, ...] = (
    "Andhra Pradesh", "Assam", "Aurangabad", "Bangalore", "Bhopal", "Chattisgarh", "Chennai",
    "Coimbatore", "Delhi", "Goa", "Gujarat", "Hyderabad", "Indore", "Jammu", "Jharkhand",
    "Kerala", "Kolkata", "Mangalore", "Mumbai", "Nagpur", "Odisha- Commercial", "Odisha-Captive",
    "Patna", "Pune", "Punjab", "Rajasthan", "Uttar Pradesh", "Uttarakhand",
)

# The Locations this suite used before it read the ERP's region -> the ERP
# region they belong to. Several old Locations fold into one region (GUJ-1 and
# GUJ-2 are both Gujarat); each has always had the same Accounts Incharge
# within a region.
LEGACY_LOCATION_REGION: dict[str, str] = {
    "AYODHYA": "Uttar Pradesh", "GORAKHPUR": "Uttar Pradesh", "LUCKNOW": "Uttar Pradesh",
    "NOIDA": "Uttar Pradesh",
    "NCR": "Delhi",
    "ASSAM": "Assam",
    "BANGALORE": "Bangalore",
    "BHUBANESWAR": "Odisha- Commercial",
    "CHENNAI": "Chennai",
    "CHHATTISGARH": "Chattisgarh",
    "COIMBATORE+TRICHY": "Coimbatore",
    "GOA": "Goa",
    "GUJ-1": "Gujarat", "GUJ-2": "Gujarat",
    "HYDERABAD": "Hyderabad",
    "JAIPUR BHANKROTA": "Rajasthan",
    "JAMSHEDPUR": "Jharkhand", "RANCHI": "Jharkhand",
    "JK-JAMMU": "Jammu",
    "KERALA": "Kerala",
    "KOLKATA": "Kolkata",
    "LUDHIANA": "Punjab", "PB-MOHALI": "Punjab",
    "MANGALORE": "Mangalore",
    "MP-1 (BHOPAL ETC)": "Bhopal",
    "MP-2 (INDORE ETC)": "Indore",
    "MUMBAI": "Mumbai",
    "NAGPUR": "Nagpur",
    "ODISHA-CAPTIVE": "Odisha-Captive",
    "PATNA": "Patna", "PATNA-COMMERCIAL": "Patna",
    "PUNE": "Pune",
    "SAMBHAJI NAGAR": "Aurangabad",
    "UTTARAKHAND": "Uttarakhand",
    "VIJAYAWADA": "Andhra Pradesh", "VIZAG": "Andhra Pradesh",
}


def _fold(name) -> str:
    """Case, spacing and the space around a hyphen do not tell two spellings of
    a region apart ("Odisha- Commercial" / "Odisha-Commercial")."""
    text = re.sub(r"\s+", " ", str(name or "").strip())
    return re.sub(r"\s*-\s*", "-", text).casefold()


_REGION_BY_FOLD = {_fold(region): region for region in ERP_REGIONS}
_REGION_SET = frozenset(ERP_REGIONS)


def canonical_region(name) -> str:
    """The region as this suite spells it; a region the ERP has added since
    keeps its own spelling (spacing tidied)."""
    text = re.sub(r"\s+", " ", str(name or "").strip())
    return _REGION_BY_FOLD.get(_fold(text), text)


def normalise_account(value) -> str:
    """A customer account as one comparable string: trimmed, and a number
    exported as 24455.0 or 0024455 is 24455 (blank when there is none)."""
    text = str(value if value is not None else "").strip()
    if text.lower() in ("", "nan", "none", "nat"):
        return ""
    if re.fullmatch(r"\d+(\.0+)?", text):
        return str(int(text.split(".")[0]))
    return text


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch_customer_regions(oracle_cfg, cancel_event=None) -> dict[str, str]:
    """{customer account -> region} for every customer the ERP has a region
    for. Raises on a connection or query failure."""
    # imported here: these modules import this one's neighbours at load time
    from app.services.unapplied_receipts.processor import (
        _connect_oracle, _init_oracle_client, _start_cancel_watcher,
    )

    _init_oracle_client(oracle_cfg.instant_client_dir)
    conn = _connect_oracle(oracle_cfg)
    stop_watcher, watcher = _start_cancel_watcher(conn, cancel_event)
    try:
        cur = conn.cursor()
        cur.arraysize = 5000
        try:
            cur.execute(CUSTOMER_REGION_SQL)
            rows = cur.fetchall()
        except Exception:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled("ERP fetch cancelled")
            raise
        finally:
            cur.close()
    finally:
        stop_watcher.set()
        if watcher is not None:
            watcher.join(timeout=1)
        conn.close()
    return regions_from_rows(rows)


def regions_from_rows(rows) -> dict[str, str]:
    out: dict[str, str] = {}
    for account, region in rows:
        key = normalise_account(account)
        name = canonical_region(region)
        if key and name:
            out[key] = name
    return out


class RegionFetch:
    """The region lookup, running on its own connection while the main work
    (the ageing query, reading the uploaded file) goes on. A failure never
    stops a report: without regions every row simply uses the mapping."""

    def __init__(self, oracle_cfg, cancel_event=None):
        self._regions: dict[str, str] = {}
        self._error: Exception | None = None
        self._cancelled: JobCancelled | None = None
        self._thread = threading.Thread(
            target=self._run, args=(oracle_cfg, cancel_event), daemon=True, name="erp-customer-regions",
        )
        self._thread.start()

    def _run(self, oracle_cfg, cancel_event) -> None:
        try:
            self._regions = fetch_customer_regions(oracle_cfg, cancel_event)
        except JobCancelled as exc:
            self._cancelled = exc
        except Exception as exc:  # noqa: BLE001 - reported once, from the caller's thread
            self._error = exc

    def result(self, log_q=None) -> dict[str, str]:
        """Wait for the lookup; {} (and a warning) if the ERP could not give it."""
        self._thread.join()
        if self._cancelled is not None:
            raise self._cancelled
        if self._error is not None:
            logger.warning("Customer region lookup failed: %s", self._error)
            if log_q is not None:
                log_q.put(("warn", "Could not read customer regions from the ERP - "
                                   f"the Location mappings are used for every row ({self._error})"))
            return {}
        if log_q is not None:
            log_q.put(("ok", f"Read the region of {len(self._regions):,} customers from the ERP"))
        return self._regions


# ── applying ─────────────────────────────────────────────────────────────────

def regions_for(accounts: pd.Series, region_by_account: dict[str, str] | None) -> pd.Series:
    """The ERP region of each row's customer account (NaN where the ERP has none)."""
    if not region_by_account:
        return pd.Series(float("nan"), index=accounts.index, dtype=object)
    lookup = {raw: region_by_account.get(normalise_account(raw)) for raw in accounts.unique()}
    return accounts.map(lookup)


def location_with_regions(accounts: pd.Series, fallback: pd.Series,
                          region_by_account: dict[str, str] | None) -> tuple[pd.Series, pd.Series]:
    """(Location per row, mask of the rows whose Location came from the ERP).
    The ERP region wins; the mapped `fallback` is used where there is none."""
    region = regions_for(accounts, region_by_account)
    from_erp = region.notna()
    return region.where(from_erp, fallback), from_erp


# ── one-time move of the stored mappings to the ERP's names ──────────────────

def migrate_mappings_to_erp_regions(db, location_model, incharge_model) -> bool:
    """Bring one tool's mapping tables over to the ERP's region names, once.

    * every stored "name -> Location" whose Location is one of the old names
      (GUJ-1, NCR, PB-MOHALI ...) now points at the ERP region (Gujarat, Delhi,
      Punjab ...);
    * each region gets the Accounts Incharge its old Locations had (the most
      common one, as currently stored, so earlier edits carry over). An old row
      already spelt like the region (MUMBAI) is renamed (Mumbai), not duplicated:
      the database treats the two spellings as one key;
    * the old Location rows nothing uses any more are archived (soft delete).

    Done once: it is skipped when the incharge table already has a row spelt
    exactly as a region (the old names are all upper case, the regions are not).
    Returns True when it changed anything. Does not commit.
    """
    incharge_rows = db.query(incharge_model).all()
    if any(row.location in _REGION_SET for row in incharge_rows):
        return False

    stored = {row.location: (row.accounts_incharge or "").strip()
              for row in incharge_rows if not row.is_deleted}
    votes: dict[str, Counter] = defaultdict(Counter)
    for legacy, region in LEGACY_LOCATION_REGION.items():
        if stored.get(legacy):
            votes[region][stored[legacy]] += 1
    new_incharge = {region: counter.most_common(1)[0][0] for region, counter in votes.items()}
    if not new_incharge:
        return False   # nothing to carry over (an empty table is seeded first)

    by_fold = {_fold(row.location): row for row in incharge_rows}
    for region, name in new_incharge.items():
        same_spelling = by_fold.get(_fold(region))
        if same_spelling is not None:
            same_spelling.location = region
            same_spelling.accounts_incharge = name
            same_spelling.is_deleted = False
        else:
            db.add(incharge_model(location=region, accounts_incharge=name, is_deleted=False))

    still_used: set[str] = set()
    for row in db.query(location_model).filter(location_model.is_deleted == False).all():  # noqa: E712
        current = (row.location or "").strip()
        region = LEGACY_LOCATION_REGION.get(current.upper())
        if region:
            row.location = region
        elif current:
            still_used.add(current)

    for row in incharge_rows:
        if not row.is_deleted and row.location in LEGACY_LOCATION_REGION and row.location not in still_used:
            row.is_deleted = True
    db.flush()
    return True
