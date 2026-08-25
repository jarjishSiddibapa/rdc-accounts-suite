import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, User
from app.routers.admin_routes import list_users
from app.routers.system_admin_routes import get_audit_log


class AdminSearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        User.__table__.create(self.engine)
        AuditLog.__table__.create(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        self.db.add_all(
            [
                User(
                    id=1,
                    email="sneha.raman@rdc.in",
                    first_name="Sneha",
                    last_name="Raman",
                    password_hash="test",
                    role="admin",
                    is_active=True,
                    is_deleted=False,
                ),
                User(
                    id=2,
                    email="jarjish@example.com",
                    first_name="Jarjish",
                    last_name="Siddibapa",
                    password_hash="test",
                    role="user",
                    is_active=False,
                    is_deleted=False,
                ),
                User(
                    id=3,
                    email="archived@example.com",
                    first_name="Former",
                    last_name="User",
                    password_hash="test",
                    role="user",
                    is_active=False,
                    is_deleted=True,
                ),
            ]
        )
        self.db.add_all(
            [
                AuditLog(
                    id=1,
                    timestamp=datetime(2026, 8, 25, 8, 0),
                    user_id=1,
                    actor_email="sneha.raman@rdc.in",
                    action="GET /api/admin/users",
                    status_code=200,
                    ip_address="10.0.0.1",
                    details='{"duration_ms": 12}',
                    is_deleted=False,
                ),
                AuditLog(
                    id=2,
                    timestamp=datetime(2026, 8, 25, 9, 0),
                    user_id=2,
                    actor_email="jarjish@example.com",
                    action="POST /api/tools/unaccounted/mail/send",
                    status_code=500,
                    ip_address="10.0.0.2",
                    details='{"error": "SMTP unavailable"}',
                    is_deleted=False,
                ),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_user_search_is_server_paginated_and_matches_name_email_or_id(self):
        by_name = list_users(search="Sneha Raman", paginated=True, db=self.db)
        self.assertEqual(by_name["total"], 1)
        self.assertEqual(by_name["items"][0]["email"], "sneha.raman@rdc.in")

        by_id = list_users(search="2", paginated=True, db=self.db)
        self.assertEqual(by_id["total"], 1)
        self.assertEqual(by_id["items"][0]["id"], 2)

    def test_user_status_and_archived_filters_preserve_legacy_list_response(self):
        inactive = list_users(status="inactive", paginated=True, db=self.db)
        self.assertEqual([item["id"] for item in inactive["items"]], [2])

        archived = list_users(status="archived", paginated=True, db=self.db)
        self.assertEqual([item["id"] for item in archived["items"]], [3])

        legacy = list_users(db=self.db)
        self.assertIsInstance(legacy, list)
        self.assertEqual([item["id"] for item in legacy], [1, 2])

    def test_audit_search_covers_user_api_ip_details_method_and_status(self):
        by_user = get_audit_log(search="Sneha", db=self.db)
        self.assertEqual([item["id"] for item in by_user["items"]], [1])

        by_api = get_audit_log(search="unaccounted/mail", method="POST", db=self.db)
        self.assertEqual([item["id"] for item in by_api["items"]], [2])

        by_detail = get_audit_log(search="SMTP unavailable", status_group="server_error", db=self.db)
        self.assertEqual([item["id"] for item in by_detail["items"]], [2])

        by_ip = get_audit_log(search="10.0.0.1", status_group="success", db=self.db)
        self.assertEqual([item["id"] for item in by_ip["items"]], [1])


if __name__ == "__main__":
    unittest.main()
