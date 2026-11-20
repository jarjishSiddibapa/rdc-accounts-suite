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

### Where a row's Location comes from (Unaccounted, Pending MRN, Uninvoiced POs, Loans & Advance)

For each row the application first looks up the supplier (by its Supplier Number / Vendor Number) in the ERP. If the
ERP holds a region for that supplier, that region is the row's Location. Otherwise the Supplier Site / Vendor Site
Code mapping decides, as before. Only a row that neither knows is listed in the "mapping not found" popup. The report
log says how many rows took their Location from the ERP and how many of those differ from the site mapping. If the ERP
cannot be reached, the log says so and every row uses the site mapping.

### Loans & Advance, IOCL, TDS Report Generator

Upload the Oracle ERP workbook and select the GL cutoff month and year. The
application proposes a filename in this form:

```text
Loans and Advance, IOCL, TDS, Other till Aug-26 as on 31.08.2026.xlsx
```

The `till` period follows the selected cutoff month. The `as on` portion uses
today's date in Indian Standard Time and is recalculated when an untouched
default is submitted. You may edit the filename before generating the report or
again before downloading it. The `.xlsx` extension is added automatically.
Characters that Windows does not allow in filenames are rejected with a clear
validation message.

The application key and URL remain `rdc-payables` for backward-compatible user
grants and bookmarks; only the user-facing name has changed.

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
formatting, keeps the Opening, Debit, Credit and Closing figures exactly as Tally exported them,
adds the TB Balance formula column (Dr positive, Cr negative, with each ledger's side
read from that month's own figures where they prove it), highlights primary groups
in yellow and subgroup totals in orange, and downloads a date-based workbook.
Formula results are cached so totals are visible immediately without Excel COM.

The packaged central mapping contains all 202 ledger classifications proven by
the supplied June 2026 reference. If a later export contains a new ledger, the
workbook is still produced with an explicit provisional classification. Review
and save it in the result panel or the searchable **Ledger mappings** tab. An
edited or archived mapping remains centralized for every user and worker.

### RDC Sales Register Report

The register is mailed (by hand, or automatically on a mail schedule) as one Excel file per calendar month, with real
Excel dates in every date column. Each schedule - and each mail you send by hand - sets **The days the report covers**:

- **Automatic** - the month so far, through yesterday; through the first eight days of a month the whole of the month
  before as well.
- **Last month** - the whole of the previous month, worked out again for every mail.
- **A month or days I pick** - choose a whole month, or a first and last day. A report can cover at most one complete
  month plus the first eight days of the next (never past yesterday); anything longer is refused with the latest last
  day allowed.

### Ultrafine Cashflow Generator: mail schedules

Open **Mail schedules** in the Cashflow Generator. Like the other report mailers, the cashflow can have any number of
schedules, each with its own days and times, recipients, sender, wording and month. Every mail first rebuilds the
cashflow of its month from the stored bank lines and the mappings as they are at that moment, lays the Summary out as the
table in the mail, and attaches the workbook. A schedule's **month** is:

- **This month** - the month the mail goes out in, up to the last bank line stored.
- **Last month** - the month before the one the mail goes out in, worked out again for every mail.
- **A month I pick** - one fixed month (never one that has not started yet), for a one-off pack.

**If a bank line is not mapped yet**, a schedule either holds the mail (the default for a new schedule, so a cashflow
with lines missing is never mailed on its own) or sends it anyway, with those lines highlighted in the workbook and
left out of the table. A held mail is shown in the schedule's send history with the reason. A month with no bank lines
yet is held the same way. **Send now** on a schedule previews its mail and sends it only when you confirm, and can use
another month for that one mail; **Regenerate & mail** on a month under Upload and months still mails that one month by
hand.

## IOCL monitor

Assigned users can select **Ultrafine IOCL Balance Monitor**, choose **Check
balance now**, and review the check and notification tables. The page shows
monitoring status, current balance, last/next check, delivery status, filters,
and pagination. Configuration and credentials are intentionally administrator-
only.

## Ultrafine Invoice Booking Tracker

Assigned users can choose **Check tracker now** at any time and review the
complete per-location result. Each successful history row shows the pending
breakdown (pending for approval, submitted to accounts), records scanned,
pages scanned, responsible person, and every location. The newest successful
result is also kept in the always-visible **Latest tracker** table, which
matches the table used for the next scheduled email (mailed as a styled HTML
table in the message body, with no Excel attachment). A later failed attempt
does not erase that last complete snapshot.
Only two DMS statuses count as pending, matched case- and whitespace-
insensitively: **Pending for approval** and **Submitted to accounts**. Every
other status - Booked, Rejected, or anything else - is excluded from the
count. Manual checks never send the daily scheduled mail.

The daily schedule, DMS credentials, recipients, sender, templates, and work-
queue mappings are administrator-only. A scan is all-or-nothing: if even one
queue cannot be fully paginated, no partial tracker email is sent.

If a manual update finds the shared DMS ID already active, the page says so
directly. Wait for that DMS user to sign out and try again; other technical
failures continue to use the normal safe support message for regular users.

## If something fails

Regular users see `We have encountered an issue, please contact Jarjish 🥲`
for failures; administrators retain the technical details needed to diagnose
them. Retry after correcting any clearly identified input validation. Do not
refresh or close a tab during a cancellable report unless you intend to abandon it. For
server, Oracle, SMTP, or scheduler issues, contact the administrator with the
timestamp and the visible job/error identifier; never send passwords or session
files in a ticket.
