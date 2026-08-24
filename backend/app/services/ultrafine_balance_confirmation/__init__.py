"""Ultrafine Balance Confirmation Bulk Mailer — ported from the desktop app
at vishal-sir-balance-confirmation-for-ultrafine (customtkinter + keyring +
Gmail SMTP) into this suite's FastAPI + React architecture.

See processing.py, mail_builder.py, mapping_store.py, and models.py in this
package, plus app/routers/ultrafine_balance_confirmation.py, for the ported
pieces. Company = 'Ultrafine' (see app/models.py's Application.company),
distinct from the suite's RDC-branded tools.
"""
