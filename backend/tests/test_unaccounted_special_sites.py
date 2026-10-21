"""OFFICE / HOME / TDS TAX Authori always resolve through Created By."""
from app.services.unaccounted import processing as P


def test_site_override_for_special_sites_is_ignored():
    ov = {
        "TDS TAX Authori": ("HEAD OFFICE", "Sneha"),
        " OFFICE ": ("HEAD OFFICE", "Sneha"),
        "Some-Site": ("PUNE", "Pramod Sir"),
    }
    eff = P._effective_site_map(ov)
    assert "TDS TAX Authori" not in eff and " OFFICE " not in eff
    assert eff["Some-Site"] == ("PUNE", "Pramod Sir")


def test_regular_overrides_still_win_over_built_in_table():
    site = next(iter(P.SITE_MAPPING))
    eff = P._effective_site_map({site: ("X", "Y")})
    assert eff[site] == ("X", "Y")


def test_unmapped_special_site_is_listed_per_creator():
    import pandas as pd
    df = pd.DataFrame({
        "Supplier Site": ["TDS TAX Authori", "TDS TAX Authori", "HOME", "Plain-Site", "Mapped"],
        "Created By": ["amit", "amit", "nan", "bob", "x"],
        "Location": ["", "", "", "", "PUNE"],
    })
    assert P.unmapped_site_labels(df) == [
        "HOME", "Plain-Site", "TDS TAX Authori" + P.CREATOR_LABEL_SEP + "amit",
    ]
