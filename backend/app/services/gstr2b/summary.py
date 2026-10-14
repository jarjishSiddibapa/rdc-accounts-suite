"""State-wise "Summary" sheet for the combined GSTR-2B workbook.

Layout and formulas follow the team's manual summary (one row per state;
B2B Non-RCM / RCM / Blocked, CDNR, Imports, Manual RCM, Net, Net
(Consultants), Difference). Everything derivable from the combined data is a
live SUMIFS/SUMIF formula over the workbook's own B2B, B2B-CDNR and IMPG
sheets, so it recalculates if those sheets are edited.

Hand-typed by the team, therefore left empty here: Manual RCM and
Net (Consultants). Difference stays blank until the consultants' figure is
entered.
"""

from __future__ import annotations

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter

SHEET = "Summary"

_STATE = "State Name"
_RCM = "Supply Attract Reverse Charge"
_ITC = "ITC Availability"
_IGST = "Tax Amount - Integrated Tax(₹)"
_CGST = "Tax Amount - Central Tax(₹)"
_SGST = "Tax Amount - State/UT Tax(₹)"
_NOTE_TYPE = "Credit note/Debit note details - Note type"
_IMPORT_IGST = "Amount of tax (₹) - Integrated Tax(₹)"

_THIN = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_FONT = Font(name="Calibri", size=11)
_BOLD = Font(name="Calibri", size=11, bold=True)
_PEACH = PatternFill("solid", start_color="FCE4D6", end_color="FCE4D6")
_INT = '_ * #,##0_ ;_ * \\-#,##0_ ;_ * "-"??_ ;_ @_ '
_DEC = '_ * #,##0.00_ ;_ * \\-#,##0.00_ ;_ * "-"??_ ;_ @_ '

FIRST_ROW = 4

# (group title, first column letter, number of columns)
_GROUPS = (
    ("B2B (Non-RCM)", "C", 3), ("B2B (RCM)", "F", 3), ("B2B (Blocked)", "I", 3),
    ("CDNR (Credit Notes - Debit Notes)", "L", 3), ("Imports", "O", 1),
    ("Manual RCM", "P", 3), ("Net", "S", 3), ("Net (Consultants)", "V", 3),
    ("Difference", "Y", 3),
)


def _ref(sheet: str, columns: list[str], header: str) -> str | None:
    """Absolute whole-column reference like 'B2B'!$M:$M, or None when the
    sheet has no such column (e.g. an empty sheet)."""
    if header not in columns:
        return None
    letter = get_column_letter(columns.index(header) + 1)
    quoted = f"'{sheet}'" if "-" in sheet else sheet
    return f"{quoted}!${letter}:${letter}"


def _sumifs(total: str | None, *criteria: tuple[str | None, str]) -> str:
    if total is None or any(rng is None for rng, _ in criteria):
        return "0"
    pairs = ",".join(f"{rng},{crit}" for rng, crit in criteria)
    return f"SUMIFS({total},{pairs})"


def write_summary_sheet(wb, state_names: list[str], sheet_columns: dict[str, list[str]]) -> None:
    """Insert the Summary sheet first. `sheet_columns` maps each data sheet
    name to its header row as written, so formulas point at the right
    letters whatever the column order."""
    ws = wb.create_sheet(SHEET, 0)
    b2b, cdn, imp = (sheet_columns.get(n, []) for n in ("B2B", "B2B-CDNR", "IMPG"))
    states = sorted({s for s in state_names if s}, key=str.casefold)
    last = FIRST_ROW + len(states) - 1

    ws["B3"] = "Particulars"
    for title, start, width in _GROUPS:
        c0 = column_index_from_string(start)
        ws.cell(row=2, column=c0, value=title)
        if width > 1:
            ws.merge_cells(start_row=2, start_column=c0, end_row=2, end_column=c0 + width - 1)
        for i, tax in enumerate(("IGST", "CGST", "SGST")[:width]):
            ws.cell(row=3, column=c0 + i, value=tax)

    for offset, state in enumerate(states):
        r = FIRST_ROW + offset
        key = f"Summary!$B{r}"
        ws.cell(row=r, column=2, value=state)

        def b2b_split(tax_header: str, flag_header: str, flag: str) -> str:
            return "=" + _sumifs(_ref("B2B", b2b, tax_header), (_ref("B2B", b2b, _STATE), key), (_ref("B2B", b2b, flag_header), f'"{flag}"'))

        # B2B Non-RCM / RCM / Blocked (ITC availability "no")
        for col, tax in zip("CDE", (_IGST, _CGST, _SGST)):
            ws[f"{col}{r}"] = b2b_split(tax, _RCM, "no")
        for col, tax in zip("FGH", (_IGST, _CGST, _SGST)):
            ws[f"{col}{r}"] = b2b_split(tax, _RCM, "Yes")
        for col, tax in zip("IJK", (_IGST, _CGST, _SGST)):
            ws[f"{col}{r}"] = b2b_split(tax, _ITC, "no")

        # CDNR: credit notes less debit notes (Reverse charge "no" only)
        note_type = _ref("B2B-CDNR", cdn, _NOTE_TYPE)
        for col, tax in zip("LMN", (_IGST, _CGST, _SGST)):
            total = _ref("B2B-CDNR", cdn, tax)
            base = ((_ref("B2B-CDNR", cdn, _STATE), key), (_ref("B2B-CDNR", cdn, _RCM), '"no"'))
            all_notes = _sumifs(total, *base)
            debit_notes = _sumifs(total, *base, (note_type, '"Debit Note"'))
            ws[f"{col}{r}"] = f"={all_notes}-2*{debit_notes}" if total is not None else "=0"

        imp_total = _ref("IMPG", imp, _IMPORT_IGST)
        imp_state = _ref("IMPG", imp, _STATE)
        ws[f"O{r}"] = f"=SUMIF({imp_state},{key},{imp_total})" if imp_total and imp_state else "=0"

        # Net = Non-RCM + RCM - Blocked - CDNR (+ Imports, IGST only) + Manual RCM
        ws[f"S{r}"] = f"=C{r}+F{r}-I{r}-L{r}+O{r}+P{r}"
        ws[f"T{r}"] = f"=D{r}+G{r}-J{r}-M{r}+Q{r}"
        ws[f"U{r}"] = f"=E{r}+H{r}-K{r}-N{r}+R{r}"

        for net, theirs, diff in (("S", "V", "Y"), ("T", "W", "Z"), ("U", "X", "AA")):
            ws[f"{diff}{r}"] = f'=IF({theirs}{r}="","",{net}{r}-{theirs}{r})'

    total_row = last + 1
    ws.cell(row=total_row, column=2, value="Total")
    for letter in ("C D E F G H I J K L M N O P Q R S T U V W X").split():
        ws[f"{letter}{total_row}"] = f"=SUM({letter}{FIRST_ROW}:{letter}{last})"
    for net, theirs, diff in (("S", "V", "Y"), ("T", "W", "Z"), ("U", "X", "AA")):
        ws[f"{diff}{total_row}"] = (
            f'=IF(COUNT({theirs}{FIRST_ROW}:{theirs}{last})=0,"",{net}{total_row}-{theirs}{total_row})'
        )

    # -- look: bordered grid, bold headers/total, peach consultant + difference blocks
    for row in range(2, total_row + 1):
        for col in range(2, 28):
            cell = ws.cell(row=row, column=col)
            cell.border = _BORDER
            cell.font = _BOLD if row <= 3 or row == total_row else _FONT
            cell.number_format = _DEC if col >= 22 else _INT
            if row <= 3:
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if col >= 22:
                    cell.fill = _PEACH
    ws.column_dimensions["B"].width = 24
    for col in range(3, 22):
        ws.column_dimensions[get_column_letter(col)].width = 11
    for col in range(22, 28):
        ws.column_dimensions[get_column_letter(col)].width = 14
    ws.freeze_panes = f"C{FIRST_ROW}"
