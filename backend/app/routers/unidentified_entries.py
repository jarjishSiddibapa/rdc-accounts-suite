"""Unidentified Receipt Entries Report routes (administrators and the users granted the application).

The Unapplied Receipts Register's "***** Unidentified" receipts, fetched from the
ERP into one Excel file and mailed (see app.services.unidentified_entries.auto_mail).
The application has any number of MAIL SCHEDULES (see app.routers.schedule_routes):

  /schedules/...   - the schedules; each has its own timing (weekly or monthly, several times a day, and every
                     few hours), the date the entries are as on, recipients, wording and send log, and is
                     previewed and sent by hand under /schedules/{id}/mail.
  Everything goes from the system email; nobody can choose another sender.
"""

from fastapi import APIRouter, Depends

from app.permissions import require_app_access
from app.routers.schedule_routes import make_schedules_router
from app.services.unidentified_entries import auto_mail

APP_KEY = auto_mail.APP_KEY

router = APIRouter(
    prefix=f"/api/tools/{APP_KEY}", tags=[APP_KEY],
    dependencies=[Depends(require_app_access(APP_KEY))],
)

router.include_router(make_schedules_router(APP_KEY, auto_mail.run_preview_job, sender_choice=True))
