"""RDC Sales Register Report routes (administrators only).

The Accounts Sales Register is fetched from the ERP as it is, written to one
Excel file and mailed (see app.services.sales_register.auto_mail):

  /auto-mail/...   - schedule (weekly or monthly, several times a day),
                     recipients, who sends it, wording, the send log, "send now".
  /mail/...        - preview the mail, then send it from the chosen sender
                     (the signed-in person's own email, the system email or
                     another person's).
  /jobs/{id}       - poll / cancel the background jobs above.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user, require_admin
from app.jobs import cancel_job, get_job
from app.models import User
from app.routers.auto_mail_routes import make_auto_mail_router
from app.routers.report_mail_routes import make_report_mail_router
from app.services.sales_register import auto_mail

APP_KEY = auto_mail.APP_KEY

router = APIRouter(
    prefix=f"/api/tools/{APP_KEY}", tags=[APP_KEY],
    dependencies=[Depends(require_admin)],
)

router.include_router(make_auto_mail_router(APP_KEY, run_job=auto_mail.run_auto_mail_job))
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
