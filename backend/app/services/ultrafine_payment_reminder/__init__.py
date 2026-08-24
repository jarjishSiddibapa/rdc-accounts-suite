"""Ultrafine Payment Reminder tool — ported from the standalone desktop app
at ultrafine-bulk-payment-reminder/. See processing.py, mail_builder.py and
mapping_store.py for the ported business logic, and
app/routers/ultrafine_payment_reminder.py for the HTTP endpoints.

This is an independent, byte-for-byte port of its own source app — it does
NOT share code or storage with the separately-ported Ultrafine Balance
Confirmation tool (backend/app/services/ultrafine_balance_confirmation/),
even though both follow the same author's architecture pattern.
"""
