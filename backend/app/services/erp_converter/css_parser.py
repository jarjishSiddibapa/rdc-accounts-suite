"""Parses the tiny inline <style> dialect emitted by Oracle BI Publisher /
Oracle Reports HTML exports. It is a flat set of rules like:

    .c12 {font-family: 'Calibri';font-size: 10.0pt;color: #000080;}

No selector combinators, no nesting, no media queries - just class name to
a flat property:value list. A regex-based parser is sufficient and much
faster than pulling in a full CSS engine.
"""
import re

_RULE_RE = re.compile(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}")
_DECL_RE = re.compile(r"([A-Za-z-]+)\s*:\s*([^;]+)")


def parse_style_block(style_text):
    """Parse CSS text into {class_name: {property: value}}."""
    classes = {}
    for m in _RULE_RE.finditer(style_text):
        name = m.group(1)
        body = m.group(2)
        props = {}
        for dm in _DECL_RE.finditer(body):
            prop = dm.group(1).strip().lower()
            val = dm.group(2).strip().strip("'\"")
            props[prop] = val
        if name in classes:
            classes[name].update(props)
        else:
            classes[name] = props
    return classes


def resolve_classes(classes, class_names):
    """Merge declarations for a (possibly multi-token) class attribute value.
    Later classes win on conflicting properties, matching CSS cascade order
    for equal-specificity class selectors.
    """
    merged = {}
    for name in class_names.split():
        props = classes.get(name)
        if props:
            merged.update(props)
    return merged
