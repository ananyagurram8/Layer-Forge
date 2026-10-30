"""Geometry invariants for accepting a proposed responsive composition.

Source overlaps (lettering, labels over plates, layered effects) are valid.
Only newly introduced damage is a reason to keep the source composition.
"""
from math import isfinite


def overlap(a, b):
    x = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    y = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    area = min(a[2] * a[3], b[2] * b[3])
    return x * y / area if area > 0 else 0.0


def assess(elements, placements, sw, sh, tw, th):
    issues = []
    by_index = {p['index']: p for p in placements}
    scale = min(tw / sw, th / sh)
    text_roles = {'headline', 'title', 'body', 'text', 'caption', 'cta'}
    text = lambda e: e.get('type') == 'text' or e.get('role') in text_roles
    group_transforms = {}
    for i, e in enumerate(elements):
        p = by_index.get(i)
        if p is None:
            issues.append({'issue': 'missing_element', 'index': i})
            continue
        b, src = p['bbox'], e['bbox']
        if len(b) != 4 or not all(isfinite(v) for v in b) or min(b[2:]) <= 0:
            issues.append({'issue': 'invalid_geometry', 'index': i})
            continue
        if min(src[2:]) <= 0:
            continue
        sx, sy = b[2] / src[2], b[3] / src[3]
        transform = (sx, sy, b[0] - src[0] * sx, b[1] - src[1] * sy)
        group = e.get('group_id', i)
        if group is None:
            group = ('single', i)
        prior = group_transforms.setdefault(group, transform)
        if (abs(prior[0] - sx) > .005 or abs(prior[1] - sy) > .005 or
                abs(prior[2] - transform[2]) > 1 or abs(prior[3] - transform[3]) > 1):
            issues.append({'issue': 'group_separated', 'index': i})
        if abs(sx / sy - 1) > .03:
            issues.append({'issue': 'aspect_distortion', 'index': i})
        if text(e):
            if (b[0] < -1 or b[1] < -1 or b[0] + b[2] > tw + 1
                    or b[1] + b[3] > th + 1):
                issues.append({'issue': 'text_outside_canvas', 'index': i})
        # Tiny logos/plates must not become the dominant object. Modest
        # growth is allowed for readability and legitimate reflow.
        if sx > 3 * scale and src[2] * src[3] < .15 * sw * sh:
            issues.append({'issue': 'excessive_element_growth', 'index': i})
        for j in range(i):
            if j not in by_index:
                continue
            other = elements[j]
            if not (text(e) or text(other)):
                continue
            if 'background' in (e.get('role'), other.get('role')):
                continue
            old = overlap(src, other['bbox'])
            new = overlap(b, by_index[j]['bbox'])
            if new > .18 and new > old + .15:
                issues.append({'issue': 'new_content_overlap', 'index': i, 'with': j})
    return issues


def contain(elements, sw, sh, tw, th):
    scale = min(tw / sw, th / sh)
    dx, dy = (tw - sw * scale) / 2, (th - sh * scale) / 2
    result = []
    for i, e in enumerate(elements):
        x, y, w, h = e['bbox']
        result.append({'index': i, 'name': e.get('name'),
                       'bbox': [dx + x * scale, dy + y * scale, w * scale, h * scale],
                       'group': e.get('group_id', i), 'how': 'source_preserved'})
    return result


def extend_background(image, tw, th):
    """Contain the source plate and extend only its outermost pixels.

    Every source pixel retains the same uniform transform as its layers.
    The added margins contain no rescaled copies of the central artwork.
    """
    from PIL import Image
    sw, sh = image.size
    scale = min(tw / sw, th / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    plate = image.convert('RGBA').resize((nw, nh), Image.Resampling.LANCZOS)
    x, y = (tw - nw) // 2, (th - nh) // 2
    canvas = Image.new('RGBA', (tw, th))
    if x:
        canvas.paste(plate.crop((0, 0, 1, nh)).resize((x, nh)), (0, y))
    if x + nw < tw:
        canvas.paste(plate.crop((nw - 1, 0, nw, nh)).resize((tw - x - nw, nh)), (x + nw, y))
    canvas.paste(plate, (x, y))
    if y:
        canvas.paste(canvas.crop((0, y, tw, y + 1)).resize((tw, y)), (0, 0))
    if y + nh < th:
        canvas.paste(canvas.crop((0, y + nh - 1, tw, y + nh)).resize((tw, th - y - nh)), (0, y + nh))
    return canvas
