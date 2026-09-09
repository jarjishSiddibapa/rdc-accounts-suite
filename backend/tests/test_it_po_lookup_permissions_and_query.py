import unittest
from types import SimpleNamespace

from app.permissions import effective_po_lookup_role
from app.services.it_po_lookup.oracle_lookup import _build_query


class EffectivePoLookupRoleTests(unittest.TestCase):
    def test_admin_is_always_both_regardless_of_stored_value(self):
        admin = SimpleNamespace(role="admin", po_lookup_role=None)
        self.assertEqual(effective_po_lookup_role(admin), "both")
        admin_with_stale_value = SimpleNamespace(role="admin", po_lookup_role="it")
        self.assertEqual(effective_po_lookup_role(admin_with_stale_value), "both")

    def test_regular_user_gets_their_stored_role(self):
        for role in ("it", "accounts", "both"):
            user = SimpleNamespace(role="user", po_lookup_role=role)
            self.assertEqual(effective_po_lookup_role(user), role)

    def test_regular_user_with_no_role_set_fails_closed_to_it(self):
        user = SimpleNamespace(role="user", po_lookup_role=None)
        self.assertEqual(effective_po_lookup_role(user), "it")

    def test_regular_user_with_a_garbage_stored_value_fails_closed_to_it(self):
        user = SimpleNamespace(role="user", po_lookup_role="not-a-real-role")
        self.assertEqual(effective_po_lookup_role(user), "it")


class BuildQueryTests(unittest.TestCase):
    def test_generates_one_named_bind_per_po_and_matching_placeholders(self):
        query, binds = _build_query(["654377", "654423", "652440"])
        self.assertEqual(binds, {"p0": "654377", "p1": "654423", "p2": "652440"})
        self.assertIn(":p0, :p1, :p2", query)
        # Every bound PO value only appears as a bind value, never
        # interpolated directly into the SQL text (no injection surface).
        for po in binds.values():
            self.assertNotIn(po, query)

    def test_joins_ap_suppliers_for_the_po_headers_all_vendor(self):
        query, _ = _build_query(["654377"])
        self.assertIn("pha.vendor_id AS vendor_id", query)
        self.assertIn("APPS.ap_suppliers asup ON asup.vendor_id = p.vendor_id", query)
        self.assertIn("asup.vendor_name AS vendor_name", query)


if __name__ == "__main__":
    unittest.main()
