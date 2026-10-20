"""Child process for xlsb_convert: open one .xlsx in Excel and save it as .xlsb.

Runs as its own interpreter (``python -I xlsb_convert_worker.py SRC DST PIDFILE``)
so that the parent can kill it, and the Excel it started, if Excel ever hangs.
Uses only the standard library and pywin32.
"""

import sys

XL_EXCEL12 = 50  # xlExcel12: Excel Binary Workbook (.xlsb)


def main(src: str, dst: str, pid_file: str) -> None:
    import pythoncom
    import win32com.client as win32
    import win32process

    pythoncom.CoInitialize()
    excel = None
    try:
        excel = win32.DispatchEx("Excel.Application")   # always our own instance, never the user's
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        excel.EnableEvents = False
        with open(pid_file, "w") as handle:              # lets the parent kill this exact Excel
            handle.write(str(win32process.GetWindowThreadProcessId(excel.Hwnd)[1]))
        workbook = excel.Workbooks.Open(src, UpdateLinks=0, ReadOnly=True)
        workbook.SaveAs(dst, FileFormat=XL_EXCEL12)
        workbook.Close(False)
    finally:
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
