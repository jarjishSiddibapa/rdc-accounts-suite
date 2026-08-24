"""Process-wide lock serializing every Excel COM automation call.

Running multiple win32com "Excel.Application" instances concurrently is a
known source of instability on Windows (COM apartment conflicts, orphaned
EXCEL.EXE processes, occasional hangs) - acceptable risk for a single
interactive desktop user, not for a shared server process where several
background jobs could each try to drive Excel at the same time.

Every function that calls win32com.client.DispatchEx("Excel.Application")
anywhere in this suite (see app/services/unaccounted/excel_writers.py's
autofit/pivot builders and app/services/gst_invoice_adder/processor.py's
.xlsb converter) must acquire this lock for the duration of that COM
session. This does NOT limit how many background jobs can run
concurrently overall (see app/jobs.py) - only how many of them may be
mid-Excel-automation at the same instant, which is deliberately always 1.
"""

import threading

EXCEL_COM_LOCK = threading.Lock()
