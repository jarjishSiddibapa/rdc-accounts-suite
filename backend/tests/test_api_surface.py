import unittest

from app.main import app
from app.routers import auth_routes, erp_converter, iocl_balance, job_control, unaccounted_txn


class RemovedApiSurfaceTests(unittest.TestCase):
    def test_favicon_route_is_available(self):
        route_paths = {
            route.path
            for route in app.routes
            if getattr(route, "path", None) is not None
        }
        self.assertIn("/favicon.svg", route_paths)

    def test_mapping_workbook_import_export_routes_are_absent(self):
        route_paths = {
            route.path
            for route in app.routes
            if getattr(route, "path", None) is not None
        }
        removed_paths = {
            "/api/tools/rdc-payables/mappings/export",
            "/api/tools/rdc-payables/mappings/import",
            "/api/tools/unaccounted/mappings/export",
            "/api/tools/unaccounted/mappings/import",
        }
        self.assertTrue(removed_paths.isdisjoint(route_paths))

    def test_hard_delete_mapping_routes_are_absent(self):
        route_paths = {
            route.path
            for route in app.routes
            if getattr(route, "path", None) is not None
        }
        self.assertFalse(any(path.endswith("/purge") for path in route_paths))

    def test_unaccounted_mail_defaults_route_is_available(self):
        route_paths = {
            route.path
            for route in unaccounted_txn.router.routes
            if getattr(route, "path", None) is not None
        }
        self.assertIn("/api/tools/unaccounted/mail/defaults", route_paths)

    def test_desktop_parity_action_routes_are_available(self):
        unaccounted_paths = {
            (route.path, next(iter(route.methods), ""))
            for route in unaccounted_txn.router.routes
            if getattr(route, "path", None) is not None
        }
        self.assertIn(("/api/tools/unaccounted/po/excluded/{po_number}", "PUT"), unaccounted_paths)
        self.assertIn(("/api/tools/unaccounted/po/excluded", "DELETE"), unaccounted_paths)
        self.assertIn(("/api/tools/unaccounted/mail/download/{job_id}/{report_key}", "GET"), unaccounted_paths)

        erp_paths = {
            (route.path, next(iter(route.methods), ""))
            for route in erp_converter.router.routes
            if getattr(route, "path", None) is not None
        }
        self.assertIn(("/api/tools/erp-to-excel/jobs/{job_id}/cancel", "POST"), erp_paths)
        self.assertIn(("/api/tools/erp-to-excel/download-all", "POST"), erp_paths)

    def test_idle_activity_refresh_route_is_available(self):
        route_paths = {
            route.path
            for route in auth_routes.router.routes
            if getattr(route, "path", None) is not None
        }
        self.assertIn("/api/auth/activity", route_paths)

    def test_tab_owned_job_abandon_route_is_available(self):
        route_paths = {
            route.path
            for route in job_control.router.routes
            if getattr(route, "path", None) is not None
        }
        self.assertIn("/api/jobs/{job_id}/abandon", route_paths)

    def test_retired_dms_routes_are_absent(self):
        route_paths = {
            route.path
            for route in app.routes
            if getattr(route, "path", None) is not None
        }
        self.assertFalse(any(path.startswith("/api/tools/dms") for path in route_paths))

    def test_iocl_balance_monitor_routes_are_available(self):
        route_paths = {
            route.path
            for route in iocl_balance.router.routes
            if getattr(route, "path", None) is not None
        }
        self.assertIn("/api/tools/iocl-balance/settings", route_paths)
        self.assertIn("/api/tools/iocl-balance/status", route_paths)
        self.assertIn("/api/tools/iocl-balance/check-now", route_paths)
        self.assertIn("/api/tools/iocl-balance/session", route_paths)

    def test_iocl_configuration_routes_are_admin_only(self):
        admin_paths = {
            "/api/tools/iocl-balance/settings",
            "/api/tools/iocl-balance/test-mail",
            "/api/tools/iocl-balance/session",
            "/api/tools/iocl-balance/session/clear",
        }
        for route in iocl_balance.router.routes:
            if route.path not in admin_paths:
                continue
            dependency_names = {
                getattr(dependency.call, "__name__", "")
                for dependency in route.dependant.dependencies
            }
            self.assertIn("require_admin", dependency_names, route.path)

        user_paths = {
            "/api/tools/iocl-balance/status",
            "/api/tools/iocl-balance/check-now",
            "/api/tools/iocl-balance/checks",
            "/api/tools/iocl-balance/notifications",
        }
        for route in iocl_balance.router.routes:
            if route.path not in user_paths:
                continue
            dependency_names = {
                getattr(dependency.call, "__name__", "")
                for dependency in route.dependant.dependencies
            }
            self.assertNotIn("require_admin", dependency_names, route.path)


if __name__ == "__main__":
    unittest.main()
