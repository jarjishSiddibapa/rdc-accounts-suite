"""Per-user application access and canonical application metadata.

Regular users have no application access by default. Their ``allowed_apps``
column contains the JSON list of keys an admin explicitly granted; missing,
legacy NULL, malformed, and non-list values all fail closed to an empty list.
Admins always have full access regardless of this field.
"""

import json

from fastapi import Depends, HTTPException

from app.auth import get_current_user
from sqlalchemy.orm import Session

from app.models import Application, User

APP_KEYS = [
    "erp-to-excel", "rdc-payables", "unaccounted", "trial-balance", "gstr2b-combinator",
    "unapplied-receipts", "ultrafine-balance-confirmation", "ultrafine-payment-reminder",
    "gst-invoice-adder", "closing-period-report",
    "iocl-balance-monitor",
]
RETIRED_APP_KEYS = {"dms"}

APP_LABELS = {
    "erp-to-excel": "ERP to Excel Converter",
    "rdc-payables": "RDC Payables Report",
    "unaccounted": "Unaccounted Transactions, Pending MRN & Uninvoiced Expense POs Report Generator",
    "trial-balance": "Trial Balance Location Wise Report Generator",
    "gstr2b-combinator": "GSTR 2B File Combinator",
    "unapplied-receipts": "Unapplied Receipts Report Generator",
    "ultrafine-balance-confirmation": "Ultrafine Balance Confirmation Bulk Sender",
    "ultrafine-payment-reminder": "Ultrafine Bulk Payment Reminder Sender",
    "gst-invoice-adder": "GST Invoice Number Adder",
    "closing-period-report": "Closing Period Report Generator",
    "iocl-balance-monitor": "Ultrafine IOCL Balance Monitor",
}

APP_COMPANIES = ("RDC", "Ultrafine")
DEFAULT_APP_COMPANY = "RDC"
# Apps whose default company classification is Ultrafine rather than the
# suite-wide RDC default (see seed_applications below).
_ULTRAFINE_APP_KEYS = {
    "ultrafine-balance-confirmation",
    "ultrafine-payment-reminder",
    "iocl-balance-monitor",
}

# Apps that actually send mail using an admin-managed, application-wide
# default To/Cc (see ApplicationEmailRecipient / mailer_shared's
# get_report_recipient_defaults). Everything else either has no email
# feature at all (erp-to-excel, rdc-payables, trial-balance,
# gstr2b-combinator, unapplied-receipts) or is a bulk per-customer sender
# whose To/Cc always comes from that customer's own saved mapping/upload,
# not a shared default (ultrafine-balance-confirmation,
# ultrafine-payment-reminder) - so the admin "default recipients" concept
# doesn't apply to them and they must NOT be offered in that picker.
REPORT_RECIPIENT_APP_KEYS = {"unaccounted"}


def parse_allowed_apps(user: User) -> list[str]:
    """Return explicitly granted app keys, failing closed for invalid data."""
    if not user.allowed_apps:
        return []
    try:
        value = json.loads(user.allowed_apps)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [key for key in value if isinstance(key, str) and key in APP_KEYS]


def user_has_app_access(user: User, app_key: str) -> bool:
    if user.role == "admin":
        return True
    return app_key in parse_allowed_apps(user)


def seed_applications(db: Session) -> None:
    """Ensure routed apps are active and retired apps are soft-deleted."""
    managed_keys = [*APP_KEYS, *RETIRED_APP_KEYS]
    existing = {
        row.key: row
        for row in db.query(Application).filter(Application.key.in_(managed_keys)).all()
    }
    changed = False
    for key in APP_KEYS:
        row = existing.get(key)
        if row is None:
            company = "Ultrafine" if key in _ULTRAFINE_APP_KEYS else DEFAULT_APP_COMPANY
            db.add(
                Application(
                    key=key,
                    label=APP_LABELS[key],
                    company=company,
                )
            )
            changed = True
        else:
            if row.label != APP_LABELS[key]:
                row.label = APP_LABELS[key]
                changed = True
            if row.is_deleted:
                row.is_deleted = False
                changed = True
    for key in RETIRED_APP_KEYS:
        row = existing.get(key)
        if row is not None and not row.is_deleted:
            row.is_deleted = True
            changed = True
    if changed:
        db.commit()


def require_app_access(app_key: str):
    """FastAPI dependency factory: use as
    `APIRouter(..., dependencies=[Depends(require_app_access("rdc-payables"))])`
    to gate every route in a tool's router behind that app's permission."""

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if not user_has_app_access(user, app_key):
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this application. Contact your admin.",
            )
        return user

    return _dependency
