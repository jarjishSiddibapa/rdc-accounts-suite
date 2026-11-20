"""Test-suite wide fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _no_live_erp_supplier_lookup(monkeypatch):
    """The report jobs ask the ERP for each supplier's region; a unit test must never open a real
    Oracle connection (the dev machine's .env has the live one), so by default no region is known and
    the site mapping alone decides - exactly the behaviour before the lookup existed. A test that
    wants regions patches ``supplier_region.fetch_for_job`` itself."""
    from app.services import supplier_region

    monkeypatch.setattr(supplier_region, "fetch_for_job", lambda messages=None: {})
    yield
