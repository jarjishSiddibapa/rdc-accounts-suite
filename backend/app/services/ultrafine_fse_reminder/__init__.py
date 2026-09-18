"""Ultrafine FSE Bulk Reminder — sends per-FSE (Field Sales Executive)
collection-vs-target reminder emails, plus one combined broadcast to
management, from the credit-control team's "Coll vs Target" tracker sheet.

See processor.py, mapping_store.py, and models.py in this package, plus
app/routers/ultrafine_fse_reminder.py. Company = 'Ultrafine' (see
app/models.py's Application.company), same grouping as the other
Ultrafine-branded mail tools.
"""
