"""Unidentified Entries routes (administrators and the users granted the application).

The Unapplied Receipts Register's "***** Unidentified" receipts, fetched from the
ERP into one Excel file and mailed (see app.services.unidentified_entries.auto_mail):

  /auto-mail/...   - schedule (weekly or monthly, several times a day, and every
                     few hours), recipients, wording, the send log, "send now".
  /mail/...        - preview the mail, then send it.
  Everything goes from the system email; nobody can choose another sender.
  /jobs/{id}       - poll / cancel the background jobs above.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.jobs import cancel_job, get_job
from app.models import User
from app.permissions import require_app_access
from app.routers.auto_mail_routes import make_auto_mail_router
from app.routers.report_mail_routes import make_report_mail_router
from app.services.unidentified_entries import auto_mail

APP_KEY = auto_mail.APP_KEY

router = APIRouter(
    prefix=f"/api/tools/{APP_KEY}", tags=[APP_KEY],
    dependencies=[Depends(require_app_access(APP_KEY))],
)

router.include_router(make_auto_mail_router(APP_KEY, run_job=auto_mail.run_auto_mail_job,
                                            access=require_app_access(APP_KEY)))
router.include_router(make_report_mail_router(APP_KEY, auto_mail.run_preview_job, sender_choice=True))


@router.get("/jobs/{job_id}")
def job_status(job_id: str, user: User = Depends(get_current_user)):
    job = get_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, user: User = Depends(get_current_user)):
    job = cancel_job(job_id, owner_id=user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
