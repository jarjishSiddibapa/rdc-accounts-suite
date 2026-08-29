# User guide

## Sign in

Open the server's LAN URL, enter your assigned email and password, and choose
an application from the dashboard. The sidebar can be collapsed with the
hamburger control; the layout adapts to smaller screens.

## Reports

Upload the source files requested by the selected tool, complete the visible
date/month/year picker, and wait for validation or period detection to finish.
Pending MRN and Uninvoiced Expense PO period detection is mandatory: processing
remains disabled until it succeeds. Review mappings and missing-value prompts,
then generate/download the workbook or open the mail preview.

Report mail defaults are editable where the workflow permits. In the Unaccounted
workflow, one, two, or all three selected reports update the subject and body
automatically before sending.

### Ultrafine Creditors Ageing

Upload the fresh Tally workbook containing the TB and Bill Wise data. Leave
**Report as-on date** blank to detect it from the Tally period header, or choose
an exact date with the calendar picker; ageing is calculated through the
previous day. The output preserves live formulas and also includes cached
values so totals are visible immediately when the workbook opens.

If new vendors are detected, classify them in the result panel using searchable
existing Location, Vendor Type, and Vendor Sub Type suggestions, save each
mapping, and choose **Regenerate with updated mappings**. You can also download
the unresolved-vendor CSV. The Vendor mappings tab is the shared source of
truth for every user; archived mappings remain restorable.

### Ultrafine Trial Balance Formatter

Upload the raw Tally trial-balance workbook and choose **Format trial balance**.
The application detects the report period, reproduces the approved Ultrafine
letterhead and report structure, preserves the original ledger figures and
formatting, adds the signed TB Balance formula column, highlights primary groups
in yellow and subgroup totals in orange, and downloads a date-based workbook.
Formula results are cached so totals are visible immediately without Excel COM.

The packaged central mapping contains all 202 ledger classifications proven by
the supplied June 2026 reference. If a later export contains a new ledger, the
workbook is still produced with an explicit provisional classification. Review
and save it in the result panel or the searchable **Ledger mappings** tab. An
edited or archived mapping remains centralized for every user and worker.

## IOCL monitor

Assigned users can select **Ultrafine IOCL Balance Monitor**, choose **Check
balance now**, and review the check and notification tables. The page shows
monitoring status, current balance, last/next check, delivery status, filters,
and pagination. Configuration and credentials are intentionally administrator-
only.

## If something fails

Read the on-page error and retry after correcting the input. Do not refresh or
close a tab during a cancellable report unless you intend to abandon it. For
server, Oracle, SMTP, or scheduler issues, contact the administrator with the
timestamp and the visible job/error identifier; never send passwords or session
files in a ticket.
