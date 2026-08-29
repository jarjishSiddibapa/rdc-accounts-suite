import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.unaccounted import mappings, processing
from app.services.unaccounted.models import CreatorMapping, LocationIncharge, SiteOverride


class LiveInchargeResolutionTests(unittest.TestCase):
    """Regression coverage for a real production report: the admin Mappings
    UI's Supplier Site Overrides / Created-By tabs kept showing the Accounts
    Incharge that was stored on the row when it was created, even after the
    Location <-> Incharge master table was edited afterwards - even though
    the generated report itself already resolved the current value live (via
    processing._resolve_incharge). See unaccounted_txn.py's
    list_site_overrides / list_creator_mappings, which now run every row
    through the same resolution the report uses."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            bind=self.engine,
            tables=[SiteOverride.__table__, CreatorMapping.__table__, LocationIncharge.__table__],
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def _resolved_site_overrides(self, db):
        site_overrides, _ = mappings._load_custom_mappings(db)
        loc_inc = mappings._load_location_incharge(db)
        return {
            site: processing._resolve_incharge(loc, inc, loc_inc)
            for site, (loc, inc) in site_overrides.items()
        }

    def _resolved_creator_mappings(self, db):
        _, creator_map = mappings._load_custom_mappings(db)
        loc_inc = mappings._load_location_incharge(db)
        return {
            creator: processing._resolve_incharge(loc, inc, loc_inc)
            for creator, (loc, inc) in creator_map.items()
        }

    def test_site_override_incharge_updates_when_location_incharge_changes(self):
        with self.Session() as db:
            mappings._upsert_site_override("Delhi-Vinta Rea", "NCR + Delhi", "Old Incharge", db)

            # No Location -> Incharge master entry yet: falls back to the
            # value stored on the override row itself.
            self.assertEqual(
                self._resolved_site_overrides(db)["Delhi-Vinta Rea"], "Old Incharge"
            )

            # Admin later fixes the master Location -> Incharge table...
            mappings._upsert_location_incharge("NCR + Delhi", "New Incharge", db)

            # ...and the override's effective incharge reflects it immediately,
            # with no edit to the SiteOverride row itself required.
            self.assertEqual(
                self._resolved_site_overrides(db)["Delhi-Vinta Rea"], "New Incharge"
            )
            self.assertEqual(
                db.query(SiteOverride).filter_by(supplier_site="Delhi-Vinta Rea").one().accounts_incharge,
                "Old Incharge",
            )

    def test_creator_mapping_incharge_updates_when_location_incharge_changes(self):
        with self.Session() as db:
            mappings._upsert_creator_mapping("HCHERUPALLY", "ANDHRA PRADESH", "Old Incharge", db)
            mappings._upsert_location_incharge("ANDHRA PRADESH", "New Incharge", db)

            self.assertEqual(
                self._resolved_creator_mappings(db)["HCHERUPALLY"], "New Incharge"
            )

    def test_falls_back_to_stored_incharge_when_location_has_no_master_entry(self):
        with self.Session() as db:
            mappings._upsert_site_override("Some Site", "Unmapped Location", "Legacy Incharge", db)

            self.assertEqual(
                self._resolved_site_overrides(db)["Some Site"], "Legacy Incharge"
            )


if __name__ == "__main__":
    unittest.main()
