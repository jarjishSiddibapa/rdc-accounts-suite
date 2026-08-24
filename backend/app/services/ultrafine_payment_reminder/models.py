"""SQLAlchemy model for the Ultrafine Payment Reminder tool's persistent
Customer -> Email mapping.

The desktop app required a fresh "Emails" Excel workbook on every send; this
table lets the suite remember each customer's To/CC addresses so a later
send can omit the Emails file entirely (or only cover exceptions) — see
processing.merge_and_group_data for the fallback/merge behavior.

Table name is prefixed "ultrafine_pr_" (Payment Reminder) so it never
collides with the separately-ported Ultrafine Balance Confirmation tool's
own `ultrafine_bc_customer_emails` table — the two tools are independent
ports of the same author's architecture pattern applied to different
business needs, and each owns entirely separate storage.

Importing this module (done from the router) is enough for SQLAlchemy to
register it against `app.database.Base` — the existing
`Base.metadata.create_all()` call in `database.init_db()` then creates the
table automatically.
"""

from sqlalchemy import Boolean, Column, Integer, String, Text

from app.database import Base


class CustomerEmailMap(Base):
    """Customer Name -> To/CC email addresses (comma-separated). Soft-delete
    aware (see app/soft_delete.py) — mapping_store.py's load_all() only reads
    is_deleted=False rows; save_all() revives a row (is_deleted=False) if its
    key reappears instead of inserting a duplicate."""

    __tablename__ = "ultrafine_pr_customer_emails"

    id = Column(Integer, primary_key=True)
    customer_name = Column(String(255), unique=True, index=True, nullable=False)
    to_emails = Column(Text, nullable=False, default="")
    cc_emails = Column(Text, nullable=True, default="")
    is_deleted = Column(Boolean, default=False, nullable=False)
