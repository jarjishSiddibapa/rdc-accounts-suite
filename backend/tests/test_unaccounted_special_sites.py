"""OFFICE / HOME / TDS TAX Authori behave like the desktop app: a saved site mapping applies, and
only rows still without a Location are inferred from Created By."""
from app.services.unaccounted import processing as P


def test_saved_site_mapping_for_special_sites_applies_like_the_desktop_app():
    ov = {"TDS TAX Authori": ("HEAD OFFICE", "Sneha"), "Some-Site": ("PUNE", "Pramod Sir")}
    eff = P._effective_site_map(ov)
    assert eff["TDS TAX Authori"] == ("HEAD OFFICE", "Sneha")
    assert eff["Some-Site"] == ("PUNE", "Pramod Sir")


def test_regular_overrides_still_win_over_built_in_table():
    site = next(iter(P.SITE_MAPPING))
    eff = P._effective_site_map({site: ("X", "Y")})
    assert eff[site] == ("X", "Y")
