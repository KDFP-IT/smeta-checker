"""Exact prepared-cell addresses; invalid boxes never become plausible highlights."""
import math


def _apply_matrix(box, matrix):
    """Apply a PyMuPDF 6-tuple matrix to an axis-aligned box via its corners."""
    a, b, c, d, e, f = (float(value) for value in matrix)
    corners = [(box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])]
    mapped = [(a * x + c * y + e, b * x + d * y + f) for x, y in corners]
    xs = [point[0] for point in mapped]
    ys = [point[1] for point in mapped]
    return (min(xs), min(ys), max(xs), max(ys))


def source_link(index, version, page, table, cell):

    anchor = dict(version_id=version, page=page, table_id=table['table_id'],
        cell_id=cell['cell_id'], header_ids=list(cell.get('header_ids') or []),
        literal_text=cell.get('text', ''), bbox=list(cell.get('bbox') or []))
    if (cell.get('geometry_validation') or {}).get('requires_source_review'):
        return dict(anchor, state='source_geometry_requires_review', rects=[])
    geometry = (getattr(index, 'metadata', {}).get('page_geometry') or {}).get(str(page-1))
    box = anchor['bbox']
    if not geometry or len(box) != 4:
        return dict(anchor, state='geometry_unavailable', rects=[])
    if not all(type(v) in {int, float} and math.isfinite(v) for v in box):
        return dict(anchor, state='invalid_geometry', rects=[])
    width, height = geometry['width'], geometry['height']
    if width <= 0 or height <= 0:
        return dict(anchor, state='invalid_geometry', rects=[])
    mapped = _apply_matrix(box, geometry['matrix'])
    rect = [mapped[0]/width, mapped[1]/height, mapped[2]/width, mapped[3]/height]
    if (not all(math.isfinite(v) and 0 <= v <= 1 for v in rect)
            or rect[0] >= rect[2] or rect[1] >= rect[3]):
        return dict(anchor, state='invalid_geometry', rects=[])
    return dict(anchor, state='located', rects=[rect])
