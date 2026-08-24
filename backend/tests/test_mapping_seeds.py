import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.rdc_payables import mapping_store as payables_mappings
from app.services.rdc_payables.models import (
    InvoiceOverride,
    LocationCodeMap,
    RegionInchargeMap,
    RowExclusion,
    TransactionTypeOverride,
    VendorSiteMapping,
)
from app.services.unaccounted import mappings as unaccounted_mappings
from app.services.unaccounted.models import (
    CreatorMapping,
    ExcludedPo,
    LocationIncharge,
    PoKeyword,
    PoKeywordSettings,
    SiteOverride,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]


class MappingSeedTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.models = (
            VendorSiteMapping,
            LocationCodeMap,
            RowExclusion,
            InvoiceOverride,
            RegionInchargeMap,
            TransactionTypeOverride,
            SiteOverride,
            CreatorMapping,
            LocationIncharge,
            PoKeyword,
            PoKeywordSettings,
            ExcludedPo,
        )
        Base.metadata.create_all(
            bind=self.engine,
            tables=[model.__table__ for model in self.models],
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_payables_seed_is_additive_idempotent_and_archive_safe(self):
        workbook = BACKEND_DIR / "seed_data" / "vendor-site-code-mapping.xlsx"
        with self.Session() as db:
            db.add(VendorSiteMapping(
                vendor_site_code="AEROCITY-CAPEX",
                region="ADMIN REGION",
                accounts_incharge="Admin Choice",
            ))
            db.add(RowExclusion(
                vendor_number="13595",
                invoice_number="DN/MU9/DEVCHGS",
                is_deleted=True,
            ))
            db.commit()

            first = payables_mappings.seed_missing_from_excel(db, workbook)
            self.assertEqual(first, {
                "vendor_site_codes": 1146,
                "location_codes": 214,
                "row_exclusions": 6,
                "invoice_overrides": 10,
                "region_incharge": 32,
                "transaction_type_overrides": 7,
            })
            self.assertEqual(
                payables_mappings.seed_missing_from_excel(db, workbook),
                {name: 0 for name in first},
            )

            existing = db.query(VendorSiteMapping).filter_by(
                vendor_site_code="AEROCITY-CAPEX"
            ).one()
            self.assertEqual(existing.region, "ADMIN REGION")
            archived = db.query(RowExclusion).filter_by(
                vendor_number="13595",
                invoice_number="DN/MU9/DEVCHGS",
            ).one()
            self.assertTrue(archived.is_deleted)

    def test_unaccounted_seed_is_additive_idempotent_and_archive_safe(self):
        seed_file = BACKEND_DIR / "seed_data" / "unaccounted-mappings.json"
        with self.Session() as db:
            db.add(CreatorMapping(
                created_by="HCHERUPALLY",
                location="ADMIN LOCATION",
                accounts_incharge="Admin Choice",
            ))
            db.add(SiteOverride(
                supplier_site="Delhi-Vinta Rea",
                location="NCR + Delhi",
                accounts_incharge="Dharampal Sir",
                is_deleted=True,
            ))
            db.commit()

            first = unaccounted_mappings.seed_missing_from_json(db, seed_file)
            self.assertEqual(first, {
                "site_overrides": 25,
                "creator_mapping": 21,
                "location_incharge": 30,
                "po_keywords": 4,
                "excluded_pos": 6,
                "po_keyword_settings": 1,
            })
            self.assertEqual(
                unaccounted_mappings.seed_missing_from_json(db, seed_file),
                {name: 0 for name in first},
            )

            existing = db.query(CreatorMapping).filter_by(
                created_by="HCHERUPALLY"
            ).one()
            self.assertEqual(existing.location, "ADMIN LOCATION")
            archived = db.query(SiteOverride).filter_by(
                supplier_site="Delhi-Vinta Rea"
            ).one()
            self.assertTrue(archived.is_deleted)


if __name__ == "__main__":
    unittest.main()
