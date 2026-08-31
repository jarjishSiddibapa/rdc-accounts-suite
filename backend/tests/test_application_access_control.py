import json
import unittest
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Application
from app.permissions import APP_KEYS, parse_allowed_apps, seed_applications, user_has_app_access
from app.routers.admin_routes import ApplicationCompanyBody, PermissionsBody


class ApplicationAccessControlTests(unittest.TestCase):
    def test_regular_user_with_legacy_null_access_is_denied_by_default(self):
        user = SimpleNamespace(role="user", allowed_apps=None)

        self.assertEqual(parse_allowed_apps(user), [])
        for app_key in APP_KEYS:
            self.assertFalse(user_has_app_access(user, app_key))

    def test_regular_user_only_receives_explicit_valid_grants(self):
        user = SimpleNamespace(
            role="user",
            allowed_apps=json.dumps(["rdc-payables", "dms", "unknown", 123]),
        )

        self.assertEqual(parse_allowed_apps(user), ["rdc-payables"])
        self.assertTrue(user_has_app_access(user, "rdc-payables"))
        self.assertFalse(user_has_app_access(user, "dms"))

    def test_malformed_permissions_fail_closed(self):
        for stored_value in ("not json", json.dumps({"dms": True}), "null"):
            with self.subTest(stored_value=stored_value):
                user = SimpleNamespace(role="user", allowed_apps=stored_value)
                self.assertEqual(parse_allowed_apps(user), [])

    def test_admin_access_is_inherent(self):
        user = SimpleNamespace(role="admin", allowed_apps=json.dumps([]))

        for app_key in APP_KEYS:
            self.assertTrue(user_has_app_access(user, app_key))

    def test_omitted_permissions_payload_means_no_access(self):
        self.assertEqual(PermissionsBody().allowed_apps, [])

    def test_company_classification_accepts_only_supported_companies(self):
        self.assertEqual(ApplicationCompanyBody(company="RDC").company, "RDC")
        self.assertEqual(ApplicationCompanyBody(company="Ultrafine").company, "Ultrafine")
        with self.assertRaises(ValidationError):
            ApplicationCompanyBody(company="Other")

    def test_retired_dms_catalogue_entry_is_soft_deleted(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine, tables=[Application.__table__])
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        try:
            with Session() as db:
                db.add(Application(
                    key="dms",
                    label="DMS Document Downloader",
                    company="RDC",
                ))
                db.add(Application(
                    key="rdc-payables",
                    label="RDC Payables Report",
                    company="RDC",
                ))
                db.commit()

                seed_applications(db)

                retired = db.query(Application).filter_by(key="dms").one()
                self.assertTrue(retired.is_deleted)
                payables = db.query(Application).filter_by(key="rdc-payables").one()
                self.assertEqual(
                    payables.label,
                    "Loans & Advance, IOCL, TDS Report Generator",
                )
                self.assertEqual(
                    {row.key for row in db.query(Application).filter_by(is_deleted=False).all()},
                    set(APP_KEYS),
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
