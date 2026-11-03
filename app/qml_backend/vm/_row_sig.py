"""app.qml_backend.vm._row_sig - YouTube Hunter (QML).

v1.0.9 perf: a short content signature for the flattened card rows.

Every rowsChanged hands QML a brand-new list of brand-new dicts, so each grid
card used to rebind ~100 properties on every live-stats tick / favourite toggle
even when its own numbers had not moved. The grids (ArchivePage, NichePage)
now compare this signature and leave a card alone when it is unchanged.

Only has to be stable within one process (Python's str hash is salted per
run), and it is: same displayed values -> same signature.
"""


def row_sig(row):
    """Signature of a flattened card row (any values; repr keeps it total)."""
    try:
        return "%x" % (hash(tuple(sorted(row.items()))) & 0xFFFFFFFFFFFF)
    except TypeError:            # an unhashable value (list / dict) somewhere
        return "%x" % (hash(repr(sorted(row.items(), key=lambda kv: kv[0])))
                       & 0xFFFFFFFFFFFF)
