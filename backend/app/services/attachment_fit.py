"""Fit the report workbooks of one mail into as few emails as possible, never leaving data out.

The same rule for every application. Gmail's size limit is on the whole email (every attachment together, about
a third bigger once encoded), not on each file: ``scheduled_report_mail.MAX_ATTACHMENT_BYTES`` is what one email
carries in total. When the workbooks do not fit together they are made smaller first, with the same data and
the same values:

  1. the largest ``.xlsx`` files written more compactly (xlsx_compact), then
  2. the largest files as ``.xlsb`` (xlsb_convert; Excel opens it as it opens ``.xlsx``), and only
  3. if a single file is still over the limit on its own, that file is split into the fewest parts that fit
     (each part compacted, then ``.xlsb`` if needed). The application says how its workbook is cut
     (``splitters``): every row has to land in exactly one part. Then
  4. whatever has to be several mails is packed into as few as possible.
"""

import math
from collections.abc import Callable
from pathlib import Path

from app.jobs import JobUserError
from app.services import scheduled_report_mail as mail
from app.services import xlsb_convert, xlsx_compact

# splitter(parts) -> the workbook written as that many (or more) complete parts, in order, covering all the data
Splitter = Callable[[int], list[Path]]
_MAX_SPLIT_ROUNDS = 8
_TARGET_FILL = 0.85          # aim a little under the limit so the first split attempt usually fits


class CannotSplit(Exception):
    """A splitter cannot cut the workbook any finer (for example a single day that is still too big)."""


def row_splitter(rows: int, write_slice: Callable[[int, int, Path], None], work_dir: Path, stem: str) -> Splitter:
    """A splitter that cuts a workbook of ``rows`` rows into consecutive row slices: ``write_slice(first, last,
    path)`` writes the rows ``first`` up to (not including) ``last`` as a complete workbook of its own. Every row
    lands in exactly one part, in order."""
    def split(parts_wanted: int) -> list[Path]:
        if rows < 2:
            raise CannotSplit("it has a single row, which cannot be split any further.")
        count = min(parts_wanted, rows)
        per_part = math.ceil(rows / count)
        count = math.ceil(rows / per_part)
        parts = []
        for index in range(count):
            path = work_dir / f"{stem}_Part_{index + 1}_of_{count}.xlsx"
            write_slice(index * per_part, min((index + 1) * per_part, rows), path)
            parts.append(path)
        return parts
    return split


def _size(path: Path) -> int:
    return Path(path).stat().st_size


def _shrink(path: Path, limit: int) -> Path:
    """The file as small as it needs to be to be within the limit: compacted, then .xlsb if still over."""
    if path.suffix == ".xlsx" and _size(path) > limit:
        xlsx_compact.compact_xlsx_in_place(path)
    if path.suffix == ".xlsx" and _size(path) > limit:
        binary = path.with_suffix(".xlsb")
        if xlsb_convert.convert_to_xlsb(path, binary) and _size(binary) < _size(path):
            return binary
        binary.unlink(missing_ok=True)
    return path


def shrink_to_fit(path: Path) -> Path:
    """The file made as small as needed for one email (same data and values): compacted, then .xlsb if still
    over the limit. Returns the file to attach (a new path when it was converted)."""
    return _shrink(Path(path), mail.MAX_ATTACHMENT_BYTES)


def _split_to_fit(splitter: Splitter, whole: Path, limit: int, progress_cb=None) -> list[Path]:
    """The fewest parts, each within the limit. Starts from an estimate of how many are needed."""
    parts_wanted = max(2, math.ceil(_size(whole) / (limit * _TARGET_FILL)))
    for _ in range(_MAX_SPLIT_ROUNDS):
        if progress_cb:
            progress_cb(0.94, f"Splitting {whole.name} into {parts_wanted} parts to fit emails...")
        try:
            parts = [Path(p) for p in splitter(parts_wanted)]
        except CannotSplit as exc:
            raise JobUserError(f"{whole.name} is over what an email can carry and {exc} Nothing was sent.") from exc
        parts = [_shrink(part, limit) for part in parts]
        if all(_size(part) <= limit for part in parts):
            return parts
        for part in parts:
            part.unlink(missing_ok=True)
            part.with_suffix(".xlsx").unlink(missing_ok=True)
        parts_wanted = max(parts_wanted + 1, math.ceil(parts_wanted * 1.5))
    raise JobUserError(f"{whole.name} could not be split into parts small enough to email. Nothing was sent.")


UNCHANGED = ("complete workbook", "complete workbooks")      # the note when nothing had to be done to the files


def fit_together(paths: list[Path], progress_cb=None,
                 splitters: dict[Path, Splitter] | None = None) -> tuple[list[list[str]], str, list[Path]]:
    groups, note, finals = _fit_together(paths, progress_cb, splitters)
    if len(paths) == 1:
        note = note.replace("complete workbooks", "complete workbook")
    return groups, note, finals


def _fit_together(paths: list[Path], progress_cb=None,
                  splitters: dict[Path, Splitter] | None = None) -> tuple[list[list[str]], str, list[Path]]:
    """``(groups, note, final_paths)``: the files grouped into mails (one group is one mail, in the original file
    order), a short note on what was done, and the files as they will be attached (a converted file has a new
    path, a split file is replaced by its parts). Nothing is ever left out.

    ``splitters`` maps an original file to the function that cuts it into parts; without one, a file that is over
    the limit on its own stops the job with a clear message (it can never be emailed whole)."""
    limit = mail.MAX_ATTACHMENT_BYTES

    def total(items):
        return sum(_size(p) for p in items)

    paths = [Path(p) for p in paths]
    if total(paths) <= limit:
        return [[str(p) for p in paths]], "complete workbooks", paths

    if progress_cb:
        progress_cb(0.91, "Compacting the workbooks to fit one email...")
    for path in sorted(paths, key=_size, reverse=True):
        if total(paths) <= limit:
            break
        if path.suffix == ".xlsx":
            xlsx_compact.compact_xlsx_in_place(path)
    if total(paths) <= limit:
        return [[str(p) for p in paths]], "complete workbooks (compacted to fit the email limit)", paths

    origins = list(paths)                 # the file each entry came from: a converted file keeps its origin
    current = list(paths)
    for path in sorted(paths, key=_size, reverse=True):
        if total(current) <= limit:
            break
        if path.suffix != ".xlsx":
            continue                      # already .xlsb (or another form): nothing smaller to make of it
        if progress_cb:
            progress_cb(0.93, f"Converting {path.name} to .xlsb to fit one email...")
        binary = path.with_suffix(".xlsb")
        if xlsb_convert.convert_to_xlsb(path, binary) and _size(binary) < _size(path):
            current[current.index(path)] = binary
        elif binary != path:
            binary.unlink(missing_ok=True)
    if total(current) <= limit:
        return [[str(p) for p in current]], "complete workbooks (as .xlsb to fit the email limit)", current

    # a file that is over the limit on its own cannot be sent whole: split it, or say so
    split_any = False
    finals: list[Path] = []
    for origin, path in zip(origins, current):
        if _size(path) <= limit:
            finals.append(path)
            continue
        splitter = (splitters or {}).get(origin)
        if splitter is None:
            raise JobUserError(
                f"{path.name} is {_size(path) / 1024 / 1024:.1f} MB even in its smallest form, over the "
                f"{limit / 1024 / 1024:.1f} MB an email can carry, and it cannot be split. Nothing was sent."
            )
        finals.extend(_split_to_fit(splitter, path, limit, progress_cb))
        split_any = True

    groups: list[list[Path]] = []
    for path in sorted(finals, key=_size, reverse=True):
        for group in groups:
            if total(group) + _size(path) <= limit:
                group.append(path)
                break
        else:
            groups.append([path])
    order = {p: i for i, p in enumerate(finals)}
    note = f"complete workbooks in {len(groups)} mails (size limit)"
    if split_any:
        note = f"complete data in {len(finals)} files, {len(groups)} mails (split to fit the email limit)"
    return [[str(p) for p in sorted(group, key=order.get)] for group in groups], note, finals
