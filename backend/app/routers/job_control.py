"""Shared lifecycle endpoints for browser-tab-owned background work."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.client_context import current_tab_id, normalize_tab_id
from app.jobs import abandon_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class AbandonJobRequest(BaseModel):
    tab_id: str


@router.post("/{job_id}/abandon")
def abandon(job_id: str, body: AbandonJobRequest, user=Depends(get_current_user)):
    tab_id = normalize_tab_id(body.tab_id)
    if tab_id is None:
        raise HTTPException(status_code=422, detail="Invalid browser tab identifier")
    if current_tab_id() != tab_id:
        raise HTTPException(status_code=403, detail="Browser tab identity mismatch")
    job = abandon_job(job_id, owner_id=user.id, client_tab_id=tab_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
