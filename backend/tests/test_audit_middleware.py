import unittest
from unittest.mock import patch

from starlette.background import BackgroundTask, BackgroundTasks
from starlette.responses import Response

from app import audit_middleware


class AuditMiddlewareBackgroundTests(unittest.IsolatedAsyncioTestCase):
    def _fields(self):
        return {
            "user_id": 7,
            "actor_email": "user@example.test",
            "action": "GET /api/example",
            "status_code": 200,
            "ip_address": "127.0.0.1",
            "details": {"duration_ms": 12.5},
        }

    async def test_file_is_immediate_and_mysql_is_deferred(self):
        response = Response()
        fields = self._fields()

        with (
            patch.object(audit_middleware, "_append_audit_file") as append_file,
            patch.object(audit_middleware, "_write_db_log") as write_db,
        ):
            audit_middleware._defer_request_db_log(response, **fields)

            append_file.assert_called_once_with(**fields)
            write_db.assert_not_called()
            self.assertIsInstance(response.background, BackgroundTask)

            await response.background()
            write_db.assert_called_once_with(**fields)

    async def test_existing_background_cleanup_runs_before_audit(self):
        calls = []
        response = Response(background=BackgroundTask(calls.append, "cleanup"))
        fields = self._fields()

        with (
            patch.object(audit_middleware, "_append_audit_file"),
            patch.object(
                audit_middleware,
                "_write_db_log",
                side_effect=lambda **_fields: calls.append("audit"),
            ),
        ):
            audit_middleware._defer_request_db_log(response, **fields)
            self.assertIsInstance(response.background, BackgroundTasks)
            await response.background()

        self.assertEqual(calls, ["cleanup", "audit"])


if __name__ == "__main__":
    unittest.main()
