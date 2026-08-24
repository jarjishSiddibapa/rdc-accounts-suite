"""Standalone Excel COM automation check.

Run this directly on the server having trouble converting .xlsb files:

    cd backend
    venv\\Scripts\\python.exe check_excel_com.py

It isolates the failure into one of three layers instead of guessing from
the app's error message:

  Step 1: can Excel COM even launch at all?
  Step 2: can it create and save a brand new workbook (proves automation
          + the file-save path work, independent of any specific file)?
  Step 3: can it open the *actual* uploaded .xlsb file that's failing in
          the app (proves it's something about that specific file/path
          rather than Excel automation in general)? Optional - pass the
          path as an argument, e.g.:
              venv\\Scripts\\python.exe check_excel_com.py "D:\\path\\to\\file.xlsb"
"""

import os
import sys
import tempfile


def main() -> int:
    print("=" * 70)
    print("Excel COM automation check")
    print("=" * 70)

    try:
        import pythoncom
        import win32com.client as win32
    except ImportError as exc:
        print(f"FAILED: pywin32 isn't installed in this venv: {exc}")
        return 1

    print("Step 1: launching Excel via COM (Excel.Application) ...")
    pythoncom.CoInitialize()
    xl = None
    try:
        xl = win32.DispatchEx("Excel.Application")
        xl.Visible = False
        xl.DisplayAlerts = False
        print(f"  OK - Excel launched, version {xl.Version}")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        print()
        print("  Excel COM couldn't even start. Most common causes on a server:")
        print("  - Excel isn't installed, or isn't activated/licensed, under the")
        print("    Windows account actually running this script")
        print("  - Excel has never been opened once under this account (first")
        print("    launch needs an interactive session to accept the license /")
        print("    finish setup - open Excel manually once, close it, retry)")
        print("  - a leftover EXCEL.EXE process in Task Manager is stuck")
        return 1

    print("Step 2: creating and saving a brand new workbook ...")
    wb = None
    test_path = os.path.join(tempfile.gettempdir(), "excel_com_check.xlsx")
    try:
        wb = xl.Workbooks.Add()
        wb.SaveAs(test_path, FileFormat=51)
        print(f"  OK - saved a test workbook to {test_path}")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        print()
        print("  Excel launched but couldn't create/save even a brand new file.")
        print("  This points at Excel itself being broken/unlicensed on this")
        print("  machine, not at anything specific to the uploaded .xlsb file.")
        _cleanup(xl, wb)
        return 1
    finally:
        try:
            if wb is not None:
                wb.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            os.remove(test_path)
        except OSError:
            pass

    # ── Step 3: optional - try the real file that's actually failing ────────
    real_file = sys.argv[1] if len(sys.argv) > 1 else None
    if real_file:
        print(f"Step 3: opening the real file: {real_file}")
        wb2 = None
        try:
            wb2 = xl.Workbooks.Open(os.path.abspath(real_file), ReadOnly=True, UpdateLinks=False)
            print("  OK - opened successfully.")
            wb2.Close(SaveChanges=False)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            print()
            print("  Excel automation works fine in general (Steps 1-2 passed), but")
            print("  this SPECIFIC file/path fails. Check: is the path exactly right,")
            print("  is antivirus quarantining or locking it, and is that folder (or")
            print("  the file's origin, e.g. downloaded-from-network marking) blocked")
            print("  by Excel's Protected View / Trust Center settings?")
            _cleanup(xl)
            return 1
    else:
        print("Step 3: skipped (pass the failing .xlsb file's path as an argument")
        print("        to test it specifically, e.g.:")
        print('        venv\\Scripts\\python.exe check_excel_com.py "D:\\...\\file.xlsb"')

    _cleanup(xl)
    print()
    print("Excel COM automation is working correctly on this machine.")
    return 0


def _cleanup(xl, wb=None):
    try:
        if wb is not None:
            wb.Close(SaveChanges=False)
    except Exception:
        pass
    try:
        xl.Quit()
    except Exception:
        pass
    try:
        import pythoncom
        pythoncom.CoUninitialize()
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
