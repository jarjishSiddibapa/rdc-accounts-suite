"""Persistence layer for the Unapplied Receipts Report tool's 2 mapping
tables — now backed by the suite's shared MySQL database (see models.py)
instead of the desktop app's two AppData JSON files.

Original AppData file layout (kept only as the internal startup seed data)
────────────────────────────────────────────────────────────────────────────
1. incharge_mapping.json        : { Location: "Accounts Incharge name" }
   Defaults to the hardcoded ACCOUNT_INCHARGE_MAP dict below (~35 entries)
   when the file doesn't exist yet.
2. supplier_site_mapping.json   : { "RAW ERP LOCATION NAME": "Mapped Location" }
   Defaults to the hardcoded LOCATION_MAP dict below (~300 entries) when the
   file doesn't exist yet — LOCATION_MAP *is* the Oracle ERP raw-location ->
   display-Location table; the original app just duplicated the same values
   into a separately-editable "Supplier Site" copy so ops could add new raw
   ERP site names without a code change.

Internal in-memory formats (processor.py consumes these exact shapes)
────────────────────────────────────────────────────────────────────────────
incharge_map        : { location: accounts_incharge }
supplier_site_map   : { raw_erp_location_upper: mapped_location }
"""

from sqlalchemy.orm import Session

from app.soft_delete import delete_keyed_row, sync_keyed_rows, upsert_keyed_row

from .models import AccountsInchargeMap, SupplierSiteMap

# ── Location mapping (raw ERP value → display name) ───────────────────────
# Copied verbatim from the original unapplied_processor.py's LOCATION_MAP
# constant — this is also the original SUPPLIER_SITE_MAP's default value
# (SUPPLIER_SITE_MAP = dict(LOCATION_MAP)).
LOCATION_MAP: dict[str, str] = {
    "AYODHYA": "AYODHYA",
    "BANGALORE": "BANGALORE",
    "BARH-BHEL": "PATNA-CAPTIVE",
    "BHILAI": "CHHATTISGARH",
    "BHOPAL": "MP-1 (BHOPAL ETC)",
    "BHUBANESWAR": "BHUBANESWAR",
    "CHENNAI": "CHENNAI",
    "COIMBATORE": "COIMBATORE+TRICHY",
    "DELHI": "NCR",
    "DERABASSI": "PB-MOHALI",
    "DURG-CHATTISGARH": "CHHATTISGARH",
    "FARIDABAD": "NCR",
    "GOA": "GOA",
    "GORAKHPUR": "GORAKHPUR",
    "GREATER NOIDA": "NOIDA",
    "GUJARAT MUNDRA": "GUJ-2",
    "GURGAON": "NCR",
    "GURGAON M3M": "NCR",
    "GUWAHATI": "ASSAM",
    "HO MUMBAI": "UNIDENTIFIED COLL",
    "HYDERABAD": "HYDERABAD",
    "INDORE": "MP-2 (INDORE ETC)",
    "JAIPUR": "JAIPUR BHANKROTA",
    "JAJPUR KEC": "ODISHA-CAPTIVE",
    "JAJPUR": "ODISHA-CAPTIVE",
    "JAMMU AND KASHMIR": "JK-JAMMU",
    "JAMSHEDPUR": "JAMSHEDPUR",
    "KERALA": "KERALA",
    "KERALA CAPTIVE": "KERALA",
    "KHALAPUR": "MUMBAI",
    "KHANDSA": "NCR",
    "KOCHI": "KERALA",
    "KOLKATA": "KOLKATA",
    "KOTTAYAM": "KERALA",
    "KOZHIKODE": "KERALA",
    "LANKA-ASSAM": "ASSAM",
    "LUCKNOW-BIJNOR": "LUCKNOW",
    "LUDHIANA": "LUDHIANA",
    "MANGALORE": "MANGALORE",
    "MOHALI": "PB-MOHALI",
    "MUMBAI - LOWER PAREL": "MUMBAI",
    "MUMBAI - MANKOLI": "MUMBAI",
    "MUMBAI-ANDHERI": "MUMBAI",
    "MUMBAI-BHOIWADA": "MUMBAI",
    "MUMBAI-DAHISAR": "MUMBAI",
    "MUMBAI-DEONAR": "MUMBAI",
    "MUMBAI-DOMBIVALI": "MUMBAI",
    "MUMBAI-GHATKOPAR": "MUMBAI",
    "MUMBAI-GODREJ": "MUMBAI",
    "MUMBAI-GOREGAON": "MUMBAI",
    "MUMBAI-HCC WORLI": "MUMBAI",
    "MUMBAI-MIRA ROAD": "MUMBAI",
    "MUMBAI-MR": "MUMBAI",
    "MUMBAI-OBEROI": "MUMBAI",
    "MUMBAI-RAHEJA": "MUMBAI",
    "MUMBAI-TRADING": "MUMBAI",
    "MUMBAI-VIKROLI": "MUMBAI",
    "MUM-KASHIMIR": "MUMBAI",
    "MUM-KHARGHAR": "MUMBAI",
    "MUM-SITAL BAUG": "MUMBAI",
    "MUM-TURBHE": "MUMBAI",
    "MUNDWA": "RAJ-AMBUJA",
    "NAGPUR-HIGNA ROAD": "NAGPUR",
    "NAVSARI": "GUJ-1",
    "PATNA-MAKHDUMPUR": "PATNA-COMMERCIAL",
    "PUNE": "PUNE",
    "PUNE-CAPTIVE": "PUNE",
    "RAIPUR": "CHHATTISGARH",
    "RANCHI": "RANCHI",
    "SACHIN SURAT": "GUJ-2",
    "SAGARDIGHI-BHEL": "KOLKATA",
    "SATNA KEC": "MP-1 (BHOPAL ETC)",
    "SURAT": "GUJ-2",
    "SURAT ICHHAPORE": "GUJ-2",
    "SURAT ITD HAZIRA": "GUJ-2",
    "SURAT MOHINI": "GUJ-1",
    "SURAT-GULERMAK": "GUJ-1",
    "SURAT-HAZIRA TATA": "GUJ-2",
    "SAMBHAJI NAGAR": "SAMBHAJI NAGAR",
    "TALCHER": "ODISHA-CAPTIVE",
    "TALCHER BHEL": "ODISHA-CAPTIVE",
    "TALCHER TEKNOW": "ODISHA-CAPTIVE",
    "TALOJA": "MUMBAI",
    "TATA-STEEL": "ODISHA-CAPTIVE",
    "THRISSUR": "KERALA",
    "TIRICHY": "COIMBATORE+TRICHY",
    "TRICHY": "COIMBATORE+TRICHY",
    "TRIVANDRUM": "KERALA",
    "UMRANGSHU-ASSAM": "ASSAM",
    "VADODARA DUMAD": "GUJ-1",
    "VADODARA-L&T": "GUJ-1",
    "VIZAG": "VIZAG",
    "VIJAYAWADA-1": "VIJAYAWADA",
    "VIJAYAWADA": "VIJAYAWADA",
    "BANGLORE-7 RMC PLANT": "BANGALORE",
    "BANGLORE-8 RMC PLANT": "BANGALORE",
    "MUMBAI-MIRA ROAD RMC PLANT": "MUMBAI",
    "BANGLORE-5 RMC PLANT": "BANGALORE",
    "BANGLORE-6 RMC PLANT": "BANGALORE",
    "HYDERABAD-MADHAPUR PLANT": "HYDERABAD",
    "HYDERABAD - ALIENS PLANT": "HYDERABAD",
    "JAJPUR-TATASTEEL": "GDCL - CAPTIVE",
    "JAJPUR-TATASTEEL 2": "GDCL - CAPTIVE",
    "JAJPUR - GDCL": "ODISHA-CAPTIVE",
    "KOCHI-EDAYAR RMC PLANT": "KERALA",
    "MOHALI - 2 RMC PLANT": "PB-MOHALI",
    "BHEL SAGARDIGHI": "KOLKATA",
    "THRISSUR-VELAKKODE RMC PLANT": "KERALA",
    "THIRUVANANTHAPURAM-SASTHAVATTOM RMC PLANT": "KERALA",
    "THIRUVANANTHAPURAM-RUSSELPURAM RMC PLANT": "KERALA",
    "JAJPUR-KEC": "ODISHA-CAPTIVE",
    "PUNE-4 RMC PLANT": "PUNE",
    "AMBUJA-MUNDWA PROJECT": "RAJ-AMBUJA",
    "HYDERABAD-KOKAPET": "HYDERABAD",
    "SURAT - GULERMAK": "GUJ-1",
    "RAIPUR-SERIKHEDI": "CHHATTISGARH",
    "GHATAMPUR RMC PLANT": "GHATAMPUR",
    "CUTTACK-GDCL": "GDCL - CAPTIVE",
    "MUMBAI - TURBHE": "MUMBAI",
    "KOTTAYAM - NATTAKOM": "KERALA",
    "PUNE-ELEVATEDMETRO": "PUNE",
    "BHUBANESWAR1-PAHAL": "BHUBANESWAR",
    "MUMBAI-MANKOLI PLANT": "MUMBAI",
    "HYDERABAD - PATANCHERU": "HYDERABAD",
    "HYDERABAD - THUKKUGUDA PLANT": "HYDERABAD",
    "CUTTACK-JAGATPUR": "BHUBANESWAR",
    "CHENNAI-5 RMC PLANT": "CHENNAI",
    "PUNE- VADGAON": "PUNE",
    "MUMBAI - KHARGHAR": "MUMBAI",
    "CHENNAI- MADHAVARAM": "CHENNAI",
    "GURGAON-KHERKI": "NCR",
    "INDORE-BARDARI": "MP-2 (INDORE ETC)",
    "GOA-PONDA": "GOA",
    "NAVSARI-L&T": "GUJ-1",
    "MUMBAI - KASHIMIRA": "MUMBAI",
    "CHENNAI-AMBATTUR": "CHENNAI",
    "DELHI-RANIKHERA": "NCR",
    "MUMBAI-OBEROI REALTY": "MUMBAI",
    "PATNA-DIDARGANJ": "PATNA",
    "AHMEDABAD-SHELA PLANT": "GUJ-1",
    "JAJPUR-JINDAL": "GDCL - CAPTIVE",
    "KOLKATA-TARATALA": "KOLKATA",
    "FARIDABAD PLANT": "NCR",
    "MUMBAI - SAKINAKA PLANT": "MUMBAI",
    "BHILAI-BHEL": "CHHATTISGARH",
    "HYDERABAD- KOLLUR": "HYDERABAD",
    "JAJPUR PLANT": "ODISHA-CAPTIVE",
    "MUMBAI-GHATKOPAR RMC PLANT": "MUMBAI",
    "MUMBAI - DOMBIVALI PLANT": "MUMBAI",
    "KOCHI-AMBALAMUGAL RMC PLANT": "KERALA",
    "MUMBAI-BHOIWADA RMC PLANT": "MUMBAI",
    "MUMBAI-TRADING RMC PLANT": "MUMBAI",
    "MOHALI - RMC PLANT": "PB-MOHALI",
    "KALYANI EXPRESSWAY": "KOLKATA",
    "HARIDWAR": "UTTARAKHAND",
    "HYDERABAD- LB NAGAR": "HYDERABAD",
    "BANGALORE-LODHA CAPTIVE": "BANGALORE",
    "CHENNAI-SAIDAPET": "CHENNAI",
    "HOSUR": "BANGALORE",
    "PUNE-LODHA CAPTIVE": "PUNE",
    "GURGAON-CLASSIC ENGINEERS": "NCR",
    "CHENNAI - ASIA CAPTIVE": "CHENNAI",
    "KHARKHODA": "NCR",
    "VIZAG-MADHURAWADA": "VIZAG",
    "LUDHIANA-IRISE CAPTIVE": "LUDHIANA",
    "HYDERABAD-KALPATARU CAPTIVE": "HYDERABAD",
    "MUMBAI-VIKROLI (LODHA)": "MUMBAI",
    "LUDHIANA-LADOWAL": "LUDHIANA",
    "WAYANAD-KRISHNAGIRI": "KERALA",
    "AHMEDABAD - SANAND": "GUJ-1",
    "GUJARAT MUNDRA-ITD": "GUJ-2",
    "HAZIRA-SUNVALI-GUJARAT": "GUJ-2",
    "BELGAUM-DALMIA CAPTIVE": "BANGALORE",
    "GREATER NOIDA-GAUTAM BUDDHA NAGAR-2": "NOIDA",
    "JAIPUR-BHANKROTA": "JAIPUR BHANKROTA",
    "CHENNAI - TRISULAM": "CHENNAI",
    "CHENNAI- MADHAVARAM 2": "CHENNAI",
    "HYDERABAD-RAJENDRA NAGAR (TRADING)": "HYDERABAD",
    "AHMEDABAD-SANAND": "GUJ-1",
    "JAMMU-KATHUA": "JK-JAMMU",
    "GUJARAT-VAPI": "GUJ-1",
    "GUJARAT-MUNDRA": "GUJ-2",
    "MUMBAI-URAN": "MUMBAI",
    "MUMBAI-RUNWAL MAHALAXMI": "MUMBAI",
    "GURGAON-DAULTABAD": "NCR",
    "PUNE-JAMBE": "PUNE",
    "BANGALORE-BAGLURU": "BANGALORE",
    "GUWAHATI-JAGI ROAD": "ASSAM",
    "GUWAHATI-TPL JAGIROAD": "ASSAM",
    "INDORE-SANAWADIYA": "MP-2 (INDORE ETC)",
    "FARAKKA-GDCL": "GDCL - CAPTIVE",
    "CHHATTISGARH-TOT": "CHHATTISGARH",
    "SATNA-KEC": "MP-1 (BHOPAL ETC)",
    "FARIDABAD-MATHURA ROAD": "NCR",
    "KOZHIKODE - ELATHUR": "KERALA",
    "THRISSUR - GURUVAYUR": "KERALA",
    "BANGALORE- HEGDE NAGAR": "BANGALORE",
    "SURAT- SACHIN": "GUJ-2",
    "DELHI-AEROCITY": "NCR",
    "SURAT-ICHHAPORE": "GUJ-2",
    "DIGHI-ADANI PORT": "PUNE",
    "LANKA-DALMIA ASSAM": "ASSAM",
    "DURG-CHHATTISGARH": "CHHATTISGARH",
    "GREATER NOIDA-GAUTAM BUDDHA NAGAR": "NOIDA",
    "SURAT-L&T CH261": "GUJ-1",
    "MUMBAI-GODREJ RIVIERA": "MUMBAI",
    "SURAT-L&T CH290": "GUJ-1",
    "HYDERABAD-VASAVI NARSINGI": "HYDERABAD",
    "HYDERABAD-UIC SATTVA": "HYDERABAD",
    "KHALAPUR-MAHARASHTRA": "MUMBAI",
    "GUWAHATI-ASSAM": "ASSAM",
    "TALCHER-TEKNOW OVERSEAS": "ODISHA-CAPTIVE",
    "GURGAON-BADHSHAHPUR": "NCR",
    "THIRUVANANTHAPURAM -STARCON INFRA": "KERALA",
    "BHUBANESWAR-NORTH": "BHUBANESWAR",
    "COIMBATORE - SARAVANAMPATTI": "COIMBATORE+TRICHY",
    "NAGPUR-WARDHA": "NAGPUR",
    "BANGALORE-WHITEFIELD": "BANGALORE",
    "RAIPUR-GOGAON": "CHHATTISGARH",
    "SURAT-ITD HAZIRA": "GUJ-2",
    "CHENNAI-THIRUMUDIVAKKAM": "CHENNAI",
    "KARJAT - TRAINING CENTER": "GUJ-2",
    "DELHI-GHEVRA": "NCR",
    "BANGALORE - KANNUR": "BANGALORE",
    "GOA-NAVELIM": "GOA",
    "HOOGHLY-1": "KOLKATA",
    "SITAMMA SAGAR- L&T": "HYDERABAD",
    "MUMBAI-RAHEJA-WORLI": "MUMBAI",
    "AYODHYA-UP": "AYODHYA",
    "GURGAON-SHIKOHPUR": "NCR",
    "GOA-VERNA SOUTH": "GOA",
    "BANGALORE-DODDABALLAPUR": "BANGALORE",
    "BHOPAL-MUNGALIYA": "MP-1 (BHOPAL ETC)",
    "MUMBAI-TALOJA-PANVEL": "MUMBAI",
    "DERABASSI-PUNJAB": "PB-MOHALI",
    "HAZIRA TATA-GUJARAT": "GUJ-2",
    "MUMBAI-SITAL BAUG": "MUMBAI",
    "NAGPUR - HIGNA ROAD": "NAGPUR",
    "BHOPAL-DIPDI": "MP-1 (BHOPAL ETC)",
    "GORAKHPUR-UP": "GORAKHPUR",
    "PUNE-SINHGARH": "PUNE",
    "BANGALORE-DEVANAHALLI": "BANGALORE",
    "VADODARA- L&T": "GUJ-1",
    "BANGALORE- ANJANAPURA": "BANGALORE",
    "VADODARA-DUMAD": "GUJ-1",
    "CHENNAI-PUDUPAKKAM": "CHENNAI",
    "RANCHI-NAGIRI": "RANCHI",
    "GURUGRAM-M3M": "NCR",
    "BANGALORE-KALPATARU CAPTIVE": "BANGALORE",
    "BANGLORE-2 RMC PLANT": "BANGALORE",
    "CHENNAI-1 RMC PLANT": "CHENNAI",
    "COIMBATORE-1 RMC PLANT": "COIMBATORE+TRICHY",
    "INDORE-1 RMC PLANT": "MP-2 (INDORE ETC)",
    "CORPORATE OFFICE MUMBAI": "UNIDENTIFIED COLL",
    "NOIDA-1 RMC PLANT": "NOIDA",
    "GREATER NOIDA-1 RMC PLANT": "NOIDA",
    "KHANDSA RMC PLANT": "NCR",
    "HYDERABAD-1 RMC PLANT": "HYDERABAD",
    "VIZAG1 RMC PLANT": "VIZAG",
    "KOCHI RMC PLANT - 1 (KOCHI)": "KERALA",
    "KOCHI RMC PLANT - 2 (KOCHI)": "KERALA",
    "KOCHI RMC PLANT - 3 (KOCHI)": "KERALA",
    "BANGLORE-1 RMC PLANT": "BANGALORE",
    "PUNE-1 RMC PLANT": "PUNE",
    "KOLKATA-1 RMC PLANT": "KOLKATA",
    "KOLKATA-2 RMC PLANT": "KOLKATA",
    "GURGAON-1 RMC PLANT": "NCR",
    "KOLKATA-3 RMC PLANT": "KOLKATA",
    "BANGLORE-3 RMC PLANT": "BANGALORE",
    "CHENNAI-2 RMC PLANT": "CHENNAI",
    "MANGALORE 1 RMC PLANT": "MANGALORE",
    "BANGLORE-4 RMC PLANT": "BANGALORE",
    "HYDERABAD-2 RMC PLANT": "HYDERABAD",
    "JAIPUR 1 RMC PLANT": "JAIPUR BHANKROTA",
    "CHENNAI-3 RMC PLANT": "CHENNAI",
    "HYDERABAD-3 RMC PLANT": "HYDERABAD",
    "PUNE-3 RMC PLANT": "PUNE",
    "HYDERABAD-UPPAL RMC PLANT1": "HYDERABAD",
    "CHENNAI-4 RMC PLANT": "CHENNAI",
    "PUNE-2 RMC PLANT": "PUNE",
    "THIRUVANANTHAPURAM-RUSSELPURAM": "KERALA",
    "DEHRADUN-VIKASNAGAR": "UTTARAKHAND",
    "AHMEDABAD": "GUJ-1",
}

# ── Accounts Incharge mapping (Location → person name) ────────────────────
# Copied verbatim from the original's ACCOUNT_INCHARGE_MAP constant.
# Source (per the original comment): region-account-incharge-mapping.xlsx
ACCOUNT_INCHARGE_MAP: dict[str, str] = {
    "MUMBAI":             "Haresh",
    "BANGALORE":          "Elairaja",
    "NCR":                "Dharampal",
    "HYDERABAD":          "Rajesh",
    "CHENNAI":            "Veerabagu",
    "NOIDA":              "Dharampal",
    "PUNE":               "Pramod",
    "KOLKATA":            "Sapan",
    "NAGPUR":             "Makrand",
    "BHUBANESWAR":        "Manas",
    "GUJ-2":              "Dinesh",
    "ODISHA-CAPTIVE":     "Manas",
    "GOA":                "Makrand",
    "CHHATTISGARH":       "Makrand",
    "KERALA":             "Aswathy",
    "GUJ-1":              "Dinesh",
    "MANGALORE":          "Elairaja",
    "MP-2 (INDORE ETC)":  "Makrand",
    "LUDHIANA":           "Mukesh",
    "RANCHI":             "Manas",
    "MP-1 (BHOPAL ETC)":  "Makrand",
    "SAMBHAJI NAGAR":     "Pramod",
    "JAIPUR BHANKROTA":   "Dharampal",
    "AYODHYA":            "Dharampal",
    "VIJAYAWADA":         "Rajesh",
    "ASSAM":              "Sapan",
    "LUCKNOW":            "Dharampal",
    "PB-MOHALI":          "Mukesh",
    "PATNA-COMMERCIAL":   "Sapan",
    "VIZAG":              "Rajesh",
    "COIMBATORE+TRICHY":  "Sulaiman",
    "JAMSHEDPUR":         "Manas",
    "GORAKHPUR":          "Dharampal",
    "PATNA":              "Sapan",
    "JK-JAMMU":           "Mukesh",
    "UTTARAKHAND":        "Dharampal",
}


# ── DB -> in-memory ──────────────────────────────────────────────────────────

def _maybe_seed(db: Session) -> None:
    """First-run only: if a mapping table is completely empty (a brand-new
    MySQL database), seed it from the hardcoded defaults above so the suite
    launches with zero data loss from what the desktop app already shipped.
    Unlike the rdc_payables/unaccounted seed helpers, there is no bundled
    seed workbook/JSON for this tool — the desktop app's defaults were
    themselves hardcoded Python dicts, so those are seeded directly.

    This runs lazily on first ``load_all()`` call rather than from
    ``app.database.init_db()`` (that startup wiring belongs to the file's
    human owner) - functionally equivalent for the "zero data loss on first
    use" goal.
    """
    if db.query(AccountsInchargeMap.id).first() is None:
        for location, incharge in ACCOUNT_INCHARGE_MAP.items():
            db.add(AccountsInchargeMap(location=location, accounts_incharge=incharge))
        db.commit()

    if db.query(SupplierSiteMap.id).first() is None:
        for raw_site, location in LOCATION_MAP.items():
            db.add(SupplierSiteMap(supplier_site=raw_site, location=location))
        db.commit()


def load_all(db: Session) -> tuple[dict[str, str], dict[str, str]]:
    """Return (incharge_map, supplier_site_map) read from the shared MySQL
    tables. Seeds from the hardcoded defaults on first run if empty.

    incharge_map      : { location: accounts_incharge }
    supplier_site_map : { raw_erp_location: mapped_location }
    """
    _maybe_seed(db)

    incharge_map: dict[str, str] = {}
    for row in db.query(AccountsInchargeMap).filter(AccountsInchargeMap.is_deleted == False).all():  # noqa: E712
        incharge_map[row.location] = row.accounts_incharge or ""

    supplier_site_map: dict[str, str] = {}
    for row in db.query(SupplierSiteMap).filter(SupplierSiteMap.is_deleted == False).all():  # noqa: E712
        supplier_site_map[row.supplier_site] = row.location or ""

    return incharge_map, supplier_site_map


def save_all(db: Session, incharge_map: dict[str, str], supplier_site_map: dict[str, str]) -> None:
    """Sync both mapping tables in MySQL to match these in-memory dicts -
    soft-delete-aware (see app/soft_delete.py): a key missing from the
    incoming dict gets is_deleted=True, never actually removed; a key that
    reappears (even if previously soft-deleted) is revived and updated in
    place."""
    sync_keyed_rows(db, AccountsInchargeMap, ("location",), {
        location: {"accounts_incharge": incharge}
        for location, incharge in incharge_map.items()
    })

    sync_keyed_rows(db, SupplierSiteMap, ("supplier_site",), {
        supplier_site: {"location": location}
        for supplier_site, location in supplier_site_map.items()
    })

    db.commit()


# ── Single-row CRUD (safe under concurrent edits — see app/soft_delete.py's
#    upsert_keyed_row/delete_keyed_row) ──────────────────────────────────────
#
# save_all() above always rewrites both tables from one combined in-memory
# snapshot - fixing/adding/editing/deleting a single row used to go through
# load_all() -> mutate one entry -> save_all(), which races: two such
# requests close together (completely normal usage) can each read a
# snapshot that doesn't yet include the other's write, then each write their
# own stale snapshot back - whichever commits last silently soft-deletes the
# other one's row. The functions below touch only the ONE row being changed.

def upsert_accounts_incharge(db: Session, location: str, accounts_incharge: str) -> None:
    upsert_keyed_row(db, AccountsInchargeMap, ("location",), location.strip(), {
        "accounts_incharge": accounts_incharge.strip(),
    })
    db.commit()


def delete_accounts_incharge(db: Session, location: str) -> bool:
    result = delete_keyed_row(db, AccountsInchargeMap, ("location",), location.strip())
    db.commit()
    return result


def upsert_supplier_site(db: Session, supplier_site: str, location: str) -> None:
    upsert_keyed_row(db, SupplierSiteMap, ("supplier_site",), supplier_site.strip(), {
        "location": location.strip(),
    })
    db.commit()


def delete_supplier_site(db: Session, supplier_site: str) -> bool:
    result = delete_keyed_row(db, SupplierSiteMap, ("supplier_site",), supplier_site.strip())
    db.commit()
    return result
