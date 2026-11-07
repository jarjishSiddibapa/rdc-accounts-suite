"""Fit the report workbooks of one mail into as few emails as possible, never leaving data out.

Gmail's size limit is on the whole email (every attachment together, about a third bigger once encoded), not on
each file: ``scheduled_report_mail.MAX_ATTACHMENT_BYTES`` is what one email can carry in total. So several
workbooks only share one mail if they fit that together. When they do not, they are made smaller first, with
the same data and the same values:

  1. the largest ``.xlsx`` files written more compactly (xlsx_compact), then
  2. the largest files as ``.xlsb`` (xlsb_convert; Excel opens it as it opens ``.xlsx``), and only
  3. if that is still too much, several mails, packed so there are as few as possible.
"""

from pathlib import Path

from app.services import scheduled_report_mail as mail
from app.services import xlsb_convert, xlsx_compact


def fit_together(paths: list[Path], progress_cb=None) -> tuple[list[list[str]], str, list[Path]]:
    """``(groups, note, final_paths)``: the files grouped into mails (one group is one mail, in the original file
    order), a short note on what was done, and the files as they will be attached (a converted file has a new
    path). Nothing is ever left out."""
    limit = mail.MAX_ATTACHMENT_BYTES

    def total(items):
        return sum(Path(p).stat().st_size for p in items)

    paths = [Path(p) for p in paths]
    if total(paths) <= limit:
        return [[str(p) for p in paths]], "complete workbooks", paths

    if progress_cb:
        progress_cb(0.91, "Compacting the workbooks to fit one email...")
    for path in sorted(paths, key=lambda p: p.stat().st_size, reverse=True):
        if total(paths) <= limit:
            break
        if path.suffix == ".xlsx" and path.stat().st_size > 1_000_000:
            xlsx_compact.compact_xlsx_in_place(path)
    if total(paths) <= limit:
        return [[str(p) for p in paths]], "complete workbooks (compacted to fit the email limit)", paths

    current = list(paths)
    for path in sorted(paths, key=lambda p: p.stat().st_size, reverse=True):
        if total(current) <= limit:
            break
        if path.suffix != ".xlsx":
            continue                      # already .xlsb (or another form): nothing smaller to make of it
        if progress_cb:
            progress_cb(0.93, f"Converting {path.name} to .xlsb to fit one email...")
        binary = path.with_suffix(".xlsb")
        if xlsb_convert.convert_to_xlsb(path, binary) and binary.stat().st_size < path.stat().st_size:
            current[current.index(path)] = binary
        elif binary != path:
            binary.unlink(missing_ok=True)
    if total(current) <= limit:
        return [[str(p) for p in current]], "complete workbooks (as .xlsb to fit the email limit)", current

    groups: list[list[Path]] = []
    for path in sorted(current, key=lambda p: p.stat().st_size, reverse=True):
        for group in groups:
            if total(group) + path.stat().st_size <= limit:
                group.append(path)
                break
        else:
            groups.append([path])
    order = {p: i for i, p in enumerate(current)}
    return (
        [[str(p) for p in sorted(group, key=order.get)] for group in groups],
        f"complete workbooks in {len(groups)} mails (size limit)",
        current,
    )
