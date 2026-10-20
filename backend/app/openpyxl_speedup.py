"""Faster cell styling for every openpyxl workbook this suite writes.

Assigning ``cell.font = some_font`` makes openpyxl hash and compare the style
object against everything already registered in the workbook, every time -
even when it is the very same object assigned to thousands of cells, which is
how all the report writers here use them. At a few hundred thousand cells that
bookkeeping is most of the write time.

``install()`` replaces openpyxl's style-assignment step with one that
remembers, per workbook, which index an object got the last time, and reuses
it while the object is the same and unchanged. Anything else (a different
object, an object modified since, a plain string such as a number format)
takes openpyxl's own path, so the written files are identical.
"""

from __future__ import annotations

_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    try:
        from openpyxl.styles import styleable
    except Exception:       # openpyxl missing or restructured: keep its own behaviour
        return

    from openpyxl.styles.proxy import StyleProxy

    original_set = styleable.StyleDescriptor.__set__

    def fast_set(self, instance, value):
        if isinstance(value, StyleProxy):                    # a style read from another cell: a new object each time
            return original_set(self, instance, value)
        try:
            fingerprint = tuple(value.__dict__.values())     # changes when the object is modified
            collection = getattr(instance.parent.parent, self.collection)
            cache = collection.__dict__.setdefault("_speedup_cache", {})
        except Exception:                                    # strings, odd parents: openpyxl's own path
            return original_set(self, instance, value)
        hit = cache.get(id(value))
        if hit is not None and hit[0] is value and hit[2] == fingerprint:
            index = hit[1]
        else:
            index = collection.add(value)
            if len(cache) > 4096:                            # many one-off objects: do not hold on to them
                cache.clear()
            cache[id(value)] = (value, index, fingerprint)
        if not getattr(instance, "_style"):
            instance._style = styleable.StyleArray()
        setattr(instance._style, self.key, index)

    styleable.StyleDescriptor.__set__ = fast_set
    _installed = True
