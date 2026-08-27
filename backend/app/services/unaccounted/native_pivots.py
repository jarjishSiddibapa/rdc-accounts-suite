"""Native Excel PivotTable support without automating a local Excel process.

OpenPyXL can preserve an existing PivotTable but cannot create one through a
public management API.  Pending MRN therefore starts from a tiny, sanitized
workbook containing one genuine Vendorwise Pivot definition and replaces the
non-pivot sheets with the freshly generated report.  Excel refreshes the pivot
from that workbook's own Summary range when the user opens the attachment.
"""

from __future__ import annotations

from copy import copy
import os
from pathlib import Path
import tempfile

from openpyxl import load_workbook

from app.services.xlsx_formula_cache import cache_formula_values, inject_cached_values


_PIVOT_SHEET = "Vendorwise Pivot"
_SUMMARY_SHEET = "Summary"
_TEMPLATE_PATH = Path(__file__).with_name("pivot_templates") / "pending_mrn_vendorwise.xlsx"


def _copy_report_sheet(source, target) -> None:
    """Copy one generated report sheet into the sanitized template workbook."""
    target.sheet_format = copy(source.sheet_format)
    target.sheet_properties = copy(source.sheet_properties)
    target.views = copy(source.views)
    target.freeze_panes = source.freeze_panes
    target.auto_filter.ref = source.auto_filter.ref
    target.sheet_state = source.sheet_state
    target.print_options = copy(source.print_options)
    target.page_margins = copy(source.page_margins)
    target.page_setup = copy(source.page_setup)

    for key, dimension in source.column_dimensions.items():
        copied = target.column_dimensions[key]
        copied.width = dimension.width
        copied.hidden = dimension.hidden
        copied.bestFit = dimension.bestFit
        copied.outlineLevel = dimension.outlineLevel

    for key, dimension in source.row_dimensions.items():
        copied = target.row_dimensions[key]
        copied.height = dimension.height
        copied.hidden = dimension.hidden
        copied.outlineLevel = dimension.outlineLevel

    for row in source.iter_rows():
        for source_cell in row:
            target_cell = target[source_cell.coordinate]
            target_cell.value = source_cell.value
            if source_cell.has_style:
                # Assign components individually so OpenPyXL registers them in
                # the destination workbook's own style tables.
                target_cell.font = copy(source_cell.font)
                target_cell.fill = copy(source_cell.fill)
                target_cell.border = copy(source_cell.border)
                target_cell.alignment = copy(source_cell.alignment)
                target_cell.protection = copy(source_cell.protection)
                target_cell.number_format = source_cell.number_format
            if source_cell.hyperlink:
                target_cell._hyperlink = copy(source_cell.hyperlink)
            if source_cell.comment:
                target_cell.comment = copy(source_cell.comment)

    for merged_range in source.merged_cells.ranges:
        target.merge_cells(str(merged_range))


def _validate_native_pivot(path: Path, expected_source_ref: str) -> None:
    workbook = load_workbook(path, data_only=False)
    try:
        pivots = workbook[_PIVOT_SHEET]._pivots
        if len(pivots) != 1:
            raise ValueError("Pending MRN output does not contain its native Vendorwise PivotTable")
        pivot = pivots[0]
        source = pivot.cache.cacheSource.worksheetSource
        if source.sheet != _SUMMARY_SHEET or source.ref != expected_source_ref:
            raise ValueError("Pending MRN PivotTable cache points at the wrong source range")
        if pivot.cache.refreshOnLoad is not True or pivot.cache.missingItemsLimit != 0:
            raise ValueError("Pending MRN PivotTable is not configured for a clean open-time refresh")
    finally:
        workbook.close()


def attach_pending_mrn_native_pivot(path: str | Path) -> None:
    """Replace the static Vendorwise sheet with a refreshable native PivotTable.

    This function runs entirely through Python/Open XML.  The template contains
    only one explicitly synthetic row; it contains no report or user data and
    its obsolete placeholder items are discarded on the first refresh.
    """
    output_path = Path(path)
    if not _TEMPLATE_PATH.is_file():
        raise FileNotFoundError(f"Native PivotTable template is missing: {_TEMPLATE_PATH}")

    generated = load_workbook(output_path, data_only=False)
    template = load_workbook(_TEMPLATE_PATH, data_only=False)
    temp_path: Path | None = None
    try:
        if _PIVOT_SHEET not in generated.sheetnames or _SUMMARY_SHEET not in generated.sheetnames:
            raise ValueError("Pending MRN workbook is missing the PivotTable source or destination sheet")

        # Keep the template's native Vendorwise Pivot sheet and replace every
        # ordinary worksheet with the freshly generated report equivalent.
        for name in list(template.sheetnames):
            if name != _PIVOT_SHEET:
                template.remove(template[name])

        for index, name in enumerate(generated.sheetnames):
            if name == _PIVOT_SHEET:
                continue
            destination = template.create_sheet(
                name,
                index if index == 0 else len(template.sheetnames),
            )
            _copy_report_sheet(generated[name], destination)

        desired_order = [name for name in generated.sheetnames if name in template.sheetnames]
        template._sheets = [template[name] for name in desired_order]

        summary = template[_SUMMARY_SHEET]
        source_ref = f"A1:{summary.cell(1, summary.max_column).column_letter}{summary.max_row}"
        pivot = template[_PIVOT_SHEET]._pivots[0]
        pivot.cache.cacheSource.worksheetSource.ref = source_ref
        pivot.cache.cacheSource.worksheetSource.sheet = _SUMMARY_SHEET
        pivot.cache.refreshOnLoad = True
        pivot.cache.enableRefresh = True
        pivot.cache.invalid = False
        pivot.cache.missingItemsLimit = 0
        pivot.cache.refreshedBy = None
        pivot.cache.refreshedDate = None
        template.calculation.fullCalcOnLoad = True
        template.calculation.forceFullCalc = True

        cached_values = cache_formula_values(template)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{output_path.stem}-pivot-",
            suffix=".xlsx",
            dir=str(output_path.parent),
        )
        os.close(fd)
        temp_path = Path(temp_name)
        template.save(temp_path)
        inject_cached_values(str(temp_path), cached_values)
        _validate_native_pivot(temp_path, source_ref)
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        generated.close()
        template.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
