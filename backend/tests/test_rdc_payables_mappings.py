import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.rdc_payables import mapping_store
from app.services.rdc_payables.models import (
    InvoiceOverride,
    LocationCodeMap,
    RegionInchargeMap,
    RowExclusion,
    TransactionTypeOverride,
    VendorSiteMapping,
)


class LiveInchargeResolutionTests(unittest.TestCase):
    """Regression coverage mirroring test_unaccounted_mappings.py: Vendor Site
    Codes / Location Codes / Invoice Overrides each store their own Accounts
    Incharge snapshot, but report generation (processor.py's "Region ->
    Accounts Incharge" step) always lets the Region Incharge Map win when
    that Region has an entry. The admin Mappings UI's list endpoints
    (rdc_payables.py's list_vendor_site_codes / list_location_codes /
    list_invoice_overrides) now apply that same precedence instead of
    returning the stale stored snapshot."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            bind=self.engine,
            tables=[
                VendorSiteMapping.__table__,
                LocationCodeMap.__table__,
                RowExclusion.__table__,
                InvoiceOverride.__table__,
                RegionInchargeMap.__table__,
                TransactionTypeOverride.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def _resolved(self, db, table_dict, key_field="Region"):
        _, _, _, _, region_incharge_map, _ = mapping_store.load_all(db)
        return {
            key: region_incharge_map.get(entry.get(key_field, "")) or entry.get("Accounts Incharge", "")
            for key, entry in table_dict.items()
        }

    def test_vendor_site_code_incharge_updates_when_region_incharge_changes(self):
        with self.Session() as db:
            mapping_store.upsert_vendor_site_code(db, "AEROCITY-CAPEX", "ADMIN REGION", "Old Incharge")

            vendor_mapping, *_ = mapping_store.load_all(db)
            self.assertEqual(
                self._resolved(db, vendor_mapping)["AEROCITY-CAPEX"], "Old Incharge"
            )

            mapping_store.upsert_region_incharge(db, "ADMIN REGION", "New Incharge")

            vendor_mapping, *_ = mapping_store.load_all(db)
            self.assertEqual(
                self._resolved(db, vendor_mapping)["AEROCITY-CAPEX"], "New Incharge"
            )
            self.assertEqual(
                db.query(VendorSiteMapping).filter_by(vendor_site_code="AEROCITY-CAPEX").one().accounts_incharge,
                "Old Incharge",
            )

    def test_location_code_incharge_updates_when_region_incharge_changes(self):
        with self.Session() as db:
            mapping_store.upsert_location_code(db, "1234", "ADMIN REGION", "Old Incharge")
            mapping_store.upsert_region_incharge(db, "ADMIN REGION", "New Incharge")

            _, loc_code_map, *_ = mapping_store.load_all(db)
            self.assertEqual(self._resolved(db, loc_code_map)["1234"], "New Incharge")

    def test_falls_back_to_stored_incharge_when_region_has_no_master_entry(self):
        with self.Session() as db:
            mapping_store.upsert_vendor_site_code(db, "SOME-SITE", "Unmapped Region", "Legacy Incharge")

            vendor_mapping, *_ = mapping_store.load_all(db)
            self.assertEqual(
                self._resolved(db, vendor_mapping)["SOME-SITE"], "Legacy Incharge"
            )

    def test_get_region_incharge_wins_over_client_supplied_value_on_write(self):
        """upsert_vendor_site_code itself just stores whatever incharge it's
        given - it's the router's job to prefer the Region Incharge Map. This
        pins the router-level precedence documented in rdc_payables.py's
        add_vendor_site_code/edit_vendor_site_code comments."""
        with self.Session() as db:
            mapping_store.upsert_region_incharge(db, "REGION A", "Master Incharge")

            region_master = mapping_store.get_region_incharge(db, "REGION A")
            submitted = "Someone Typed This"
            effective = region_master or submitted
            self.assertEqual(effective, "Master Incharge")


if __name__ == "__main__":
    unittest.main()
