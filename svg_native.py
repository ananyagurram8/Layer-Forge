"""Native SVG layers, proportional layout and vector export.

Pixels are used only to measure visible bounds and generate previews. The SVG
tree remains the authority for exports (including masks, filters and images).
"""
from __future__ import annotations

import base64
import copy
import io
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image
import resvg_py

NS = "http://www.w3.org/2000/svg"
XLINK = "{http://www.w3.org/1999/xlink}href"
INK = "{http://www.inkscape.org/namespaces/inkscape}"
ET.register_namespace("", NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
NON_PAINT = {"defs", "style", "title", "desc", "metadata", "symbol",
             "clipPath", "mask", "filter", "linearGradient", "radialGradient",
             "pattern", "marker"}
URL_REF = re.compile(r"url\(\s*['\"]?#([^)'\"\s]+)['\"]?\s*\)")


def tag(node):
    return node.tag.rsplit("}", 1)[-1]


def dimensions(root):
    vb = root.get("viewBox")
    values = [float(v) for v in re.split(r"[\s,]+", vb.strip())] if vb else None
    if values and (len(values) != 4 or not all(map(math.isfinite, values))
                   or min(values[2:]) <= 0):
        raise ValueError("SVG viewBox must contain four finite values with positive dimensions")
    def length(value, fallback):
        if value is None or value.endswith("%"):
            return fallback
        m = re.fullmatch(r"\s*([+\-\d.eE]+)\s*(px|pt|pc|in|cm|mm|q)?\s*", value)
        if not m:
            raise ValueError(f"Unsupported SVG dimension: {value}")
        return float(m[1]) * {None: 1, "px": 1, "pt": 96/72, "pc": 16,
                              "in": 96, "cm": 96/2.54, "mm": 96/25.4,
                              "q": 96/101.6}[m[2]]
    w = length(root.get("width"), values[2] if values else 300)
    h = length(root.get("height"), values[3] if values else 150)
    if not all(math.isfinite(v) and v > 0 for v in (w, h)):
        raise ValueError("SVG dimensions must be positive and finite")
    return w, h


def target_size(width, height):
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (width, height)):
        raise ValueError("Width and height must be whole pixel numbers")
    if not (1 <= width <= 8192 and 1 <= height <= 8192) or width * height > 32_000_000:
        raise ValueError("Size must be 1–8192 pixels per side, at most 32 megapixels")
    return width, height


def render(data, width=None, height=None):
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return Image.open(io.BytesIO(resvg_py.svg_to_bytes(
        svg_string=data, width=width, height=height))).convert("RGBA")


class SVGDocument:
    def __init__(self, path=None, *, data=None):
        raw = Path(path).read_bytes() if data is None else data
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("SVG upload exceeds 64 MB")
        if b"<!ENTITY" in raw.upper() or b"<!DOCTYPE" in raw.upper():
            raise ValueError("SVG DTDs and entities are not supported")
        self.root = ET.fromstring(raw)
        if self.root.tag not in (f"{{{NS}}}svg", "svg"):
            raise ValueError("The document root must be an SVG element")
        # Unnamespaced SVGs are common in hand-authored assets.
        for n in self.root.iter():
            if not n.tag.startswith("{"):
                n.tag = f"{{{NS}}}{n.tag}"
        self.native_width, self.native_height = dimensions(self.root)
        scale = min(1, 2048 / max(self.native_width, self.native_height))
        self.width = max(1, round(self.native_width * scale))
        self.height = max(1, round(self.native_height * scale))
        self.root.set("width", str(self.width))
        self.root.set("height", str(self.height))
        if not self.root.get("viewBox"):
            self.root.set("viewBox", f"0 0 {self.native_width} {self.native_height}")
        self._validate()
        self._resolve_css()
        # Effects on the root apply to the whole composition, not to each
        # independently composited child. Retain that case as one atomic layer.
        self.atomic_root = (any(k in self.root.attrib for k in ('opacity','mask','filter')) or
                            bool(re.search(r'(?:^|;)\s*(?:opacity|mask|filter)\s*:', self.root.get('style',''))))
        self.ids = {n.get("id"): n for n in self.root.iter() if n.get("id")}
        self.styles = [n for n in self.root.iter() if tag(n) == "style"]
        self.parents = {c: n for n in self.root.iter() for c in n}
        self.layers = []
        self.elements = []
        self._photo_profile = None
        self._profile_checked = False

    def _resolve_css(self):
        """Freeze stylesheet selectors before isolation changes sibling context.

        Keep inherited properties inherited; only declarations matching each
        original element are materialized, with the normal CSS cascade.
        """
        import cssselect2
        import tinycss2
        normal, important = cssselect2.Matcher(), cssselect2.Matcher()
        styles = [n for n in self.root.iter() if tag(n) == 'style']
        if not styles:
            return
        def declarations(content):
            return [d for d in tinycss2.parse_declaration_list(content)
                    if d.type == 'declaration']
        for style in styles:
            for rule in tinycss2.parse_stylesheet(style.text or '', skip_comments=True, skip_whitespace=True):
                if rule.type == 'at-rule':
                    raise ValueError('SVG CSS at-rules are not supported; use static styles and installed fonts')
                if rule.type != 'qualified-rule':
                    continue
                decls = declarations(rule.content)
                for selector in cssselect2.compile_selector_list(rule.prelude):
                    for matcher, flag in ((normal,False),(important,True)):
                        matcher.add_selector(selector,[d for d in decls if d.important == flag])
        for wrapped in cssselect2.ElementWrapper.from_xml_root(self.root).iter_subtree():
            n = wrapped.etree_element
            inline = declarations(n.get('style',''))
            computed = {}
            for matcher, flag in ((normal,False),(important,True)):
                for _,_,pseudo,decls in matcher.match(wrapped):
                    if pseudo is None:
                        for d in decls:
                            computed[d.lower_name] = tinycss2.serialize(d.value).strip()
                for d in inline:
                    if d.important == flag:
                        computed[d.lower_name] = tinycss2.serialize(d.value).strip()
            if computed:
                n.set('style',';'.join(f'{k}:{v}' for k,v in computed.items()))
        parents = {c:n for n in self.root.iter() for c in n}
        for style in styles:
            parents[style].remove(style)

    def _validate(self):
        for n in self.root.iter():
            if tag(n) in {"script", "foreignObject", "animate", "animateTransform", "set"}:
                raise ValueError(f"Unsupported active SVG element: {tag(n)}")
            for key, value in n.attrib.items():
                if key.lower().startswith("on"):
                    raise ValueError("SVG event handlers are not supported")
                if key in ("href", XLINK) and not (value.startswith("#") or
                    (tag(n) == "image" and value.startswith(("data:image/png;", "data:image/jpeg;", "data:image/webp;", "data:image/gif;")))):
                    raise ValueError("Embed image assets in the SVG; external references are not supported")
            css = " ".join(n.attrib.values()) + (n.text or "" if tag(n) == "style" else "")
            if "@import" in css.lower() or re.search(r"url\(\s*['\"]?(?!#)[^\s'\"]", css, re.I):
                raise ValueError("External SVG/CSS resources are not supported")

    def _shell(self, selected):
        """Keep source ancestry and transitive definition dependencies."""
        selected = set(selected)
        ancestors = set(selected)
        for n in selected:
            while n in self.parents:
                n = self.parents[n]
                ancestors.add(n)
        def prune(n):
            if n in selected:
                return copy.deepcopy(n)
            if n not in ancestors:
                return None
            out = ET.Element(n.tag, n.attrib)
            out.text, out.tail = n.text, n.tail
            for ch in n:
                p = prune(ch)
                if p is not None:
                    out.append(p)
            return out
        shell = prune(self.root)
        if shell is None:
            shell = ET.Element(self.root.tag, self.root.attrib)
        for style in self.styles:
            shell.append(copy.deepcopy(style))
        present = {n.get("id") for n in shell.iter() if n.get("id")}
        definitions = ET.Element(f"{{{NS}}}defs")
        pending = [shell]
        while pending:
            refs = set()
            for n in pending.pop().iter():
                for value in n.attrib.values():
                    refs.update(URL_REF.findall(value))
                href = n.get("href") or n.get(XLINK) or ""
                if href.startswith("#"):
                    refs.add(href[1:])
                if tag(n) == "style":
                    refs.update(URL_REF.findall(n.text or ""))
            for ref in refs - present:
                present.add(ref)
                if ref in self.ids:
                    d = copy.deepcopy(self.ids[ref])
                    definitions.append(d)
                    present.update(n.get("id") for n in d.iter() if n.get("id"))
                    pending.append(d)
        shell.insert(0, definitions)
        return shell

    def _candidates(self):
        if self.atomic_root:
            yield self.root
            return
        def walk(parent):
            children = [c for c in parent if tag(c) not in NON_PAINT]
            for n in children:
                # Unwrap a document-wide wrapper, but retain authored groups,
                # nested SVG viewports and compositing groups as atomic units.
                explicit = n.get(INK + "label") or n.get("data-name") or n.get("id")
                effect = (any(k in n.attrib for k in ("opacity", "mask", "filter")) or
                          bool(re.search(r'(?:^|;)\s*(?:opacity|mask|filter|mix-blend-mode)\s*:', n.get('style',''))))
                if tag(n) == "g" and len(children) == 1 and not explicit and not effect:
                    yield from walk(n)
                else:
                    yield n
        yield from walk(self.root)

    def extract(self):
        if self.elements:
            return self.elements
        items = []
        for order, node in enumerate(self._candidates()):
            shell = self._shell([node])
            im = render(ET.tostring(shell))
            bounds = im.getchannel("A").getbbox()
            if bounds is None:
                continue
            x, y, r, b = bounds
            label = node.get(INK + "label") or node.get("data-name") or node.get("id")
            if not node.get('id'):
                generated = f'svgforge-node-{order}'
                while generated in self.ids:
                    generated += '-auto'
                node.set('id',generated)
                self.ids[generated] = node
            texts = ["".join(n.itertext()).strip() for n in node.iter() if tag(n) == "text"]
            has_image = any(tag(n) == "image" for n in node.iter())
            items.append(dict(nodes=[node], order=order, bbox=(x, y, r-x, b-y),
                              image=im.crop(bounds), label=label, text=" ".join(texts),
                              has_image=has_image))
        # Exported outlined letters often arrive as sibling groups. Join only
        # consecutive nearby fragments; never reorder the paint stack or absorb
        # a photo, explicit designer layer, or full-canvas background.
        groups = []
        for item in items:
            prev = groups[-1] if groups else None
            join = False
            if prev and not (prev['label'] or item['label'] or prev['has_image'] or item['has_image']):
                x,y,w,h = prev['bbox']; a,b,c,d = item['bbox']
                gap = max(0, a-(x+w), x-(a+c))
                vertical = max(0, min(y+h,b+d)-max(y,b))
                join = (max(w,c) < .65*self.width and max(h,d) < .12*self.height
                        and vertical > .35*min(h,d) and gap <= .6*max(h,d))
            if join:
                x0,y0 = min(x,a),min(y,b); x1,y1 = max(x+w,a+c),max(y+h,b+d)
                image = Image.new("RGBA", (x1-x0,y1-y0))
                image.alpha_composite(prev['image'], (x-x0,y-y0))
                image.alpha_composite(item['image'], (a-x0,b-y0))
                prev.update(bbox=(x0,y0,x1-x0,y1-y0), image=image,
                            nodes=prev['nodes']+item['nodes'],
                            text=" ".join(filter(None,[prev['text'],item['text']])))
            else:
                groups.append(item)
        for i, item in enumerate(groups):
            self.layers.append(item['nodes'])
            name = item['label'] or item['text'] or f"{'Image' if item['has_image'] else 'Artwork'} {i+1}"
            e = dict(name=name, type="text" if item['text'] else "raster" if item['has_image'] else "shape",
                     bbox=tuple(map(float,item['bbox'])), image=item['image'], text=item['text'] or None,
                     role="text" if item['text'] else "raster" if item['has_image'] else "shape",
                     role_hint=None, z=i, group_id=i, native_equivalent=True,
                     source_layer_ids=[n.get("id") or f"svg-{item['order']}-{j}" for j,n in enumerate(item['nodes'])],
                     _svg_document=self, _svg_layer=i)
            self.elements.append(e)
        return self.elements

    def photo_profile(self):
        if not self._profile_checked:
            from svg_layout import photo_profile
            self._photo_profile = photo_profile(self)
            self._profile_checked = True
        return self._photo_profile

    def strategy(self, width, height):
        changed_aspect = abs(width/self.width-height/self.height) > 1e-6
        if changed_aspect and self.photo_profile():
            return 'svg_full_bleed'
        if self.is_flat_photo():
            return 'svg_cover'
        return 'svg_proportional'

    def is_flat_photo(self):
        """Only auto-crop an opaque, full-canvas image with no other artwork.

        A raster-containing group may also contain vector lettering, so the
        layer's raster label alone is not enough to identify a flat photo.
        """
        if len(self.elements) != 1:
            return False
        e = self.elements[0]
        if (e['type'] != 'raster' or e['bbox'] != (0.,0.,float(self.width),float(self.height))
                or e['image'].getchannel('A').getextrema() != (255,255)):
            return False
        def paint_nodes(node):
            if tag(node) in NON_PAINT:
                return []
            if tag(node) in {'g','svg'}:
                return [p for child in node for p in paint_nodes(child)]
            return [node]
        painted = [p for node in self.layers[0] for p in paint_nodes(node)]
        return len(painted) == 1 and tag(painted[0]) == 'image'

    def plan(self, width, height):
        target_size(width,height)
        from layout_quality import contain
        strategy = self.strategy(width,height)
        if strategy == 'svg_full_bleed':
            from svg_layout import full_frame_plan
            placements = full_frame_plan(self,self.photo_profile(),width,height)
        elif strategy == 'svg_cover':
            scale = max(width/self.width,height/self.height)
            placements = [dict(index=0,name=self.elements[0]['name'],group=0,
                bbox=[(width-self.width*scale)/2,(height-self.height*scale)/2,
                      self.width*scale,self.height*scale],visible=True)]
        else:
            placements = contain(self.elements, self.width, self.height, width, height)
        for p in placements:
            if strategy != 'svg_full_bleed':
                p['how'] = strategy
            e = self.elements[p['index']]
            e['_placed'] = p['bbox']
            e['_layout_strategy'] = strategy
            e['_layout_source_size'] = (self.width,self.height)
        return placements

    def validate_overrides(self, overrides):
        result = {}
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("Layer overrides must be an object")
        for key, box in (overrides or {}).items():
            i = int(key)
            if not 0 <= i < len(self.elements):
                raise ValueError(f"Unknown SVG layer: {key}")
            if not isinstance(box, (list,tuple)) or len(box) != 4:
                raise ValueError("Layer bounds must be [x, y, width, height]")
            box = list(map(float, box))
            if not all(map(math.isfinite, box)) or min(box[2:]) <= 0:
                raise ValueError("Layer bounds must be finite with positive dimensions")
            src = self.elements[i]['bbox']
            if abs((box[2]/src[2]) / (box[3]/src[3]) - 1) > .01:
                raise ValueError("SVG layers must be resized proportionally")
            result[i] = box
        return result

    def background_svg(self, width, height):
        out = ET.Element(f'{{{NS}}}svg',width=str(width),height=str(height),viewBox=f'0 0 {width} {height}')
        if self.strategy(width,height) != 'svg_full_bleed':
            return out
        profile = self.photo_profile()
        if profile.get('kind') == 'photo_overlay':
            # Fit the complete photo at bottom right, then extend only its
            # sampled edge colors into the remaining space. Never stretch a person.
            import numpy as np
            scale = min(width/self.width,height/self.height)
            pw,ph = self.width*scale,self.height*scale
            dx,dy = width-pw,height-ph
            def patch(x,y,w,h,view,prefix):
                viewport = ET.SubElement(out,f'{{{NS}}}svg',x=str(x),y=str(y),
                    width=str(w),height=str(h),viewBox=' '.join(map(str,view)),
                    preserveAspectRatio='none',overflow='hidden')
                shell = self._shell(self.layers[profile['backdrop']])
                self._prefix_ids(shell,prefix)
                viewport.append(shell)
            def extend(w,h,vertical):
                pixels = np.asarray(self.elements[profile['backdrop']]['image'])[...,:3]
                colors = pixels[:,:12].mean(axis=1) if vertical else pixels[:12].mean(axis=0)
                # Smooth compression noise, then express the wall/floor color
                # continuation as an opaque SVG gradient, without streaks.
                kernel = np.exp(-np.arange(-12,13,dtype=float)**2/32)
                kernel /= kernel.sum()
                colors = np.stack([np.convolve(np.pad(colors[:,c],12,mode='edge'),
                                               kernel,mode='valid') for c in range(3)],axis=1)
                defs = ET.SubElement(out,f'{{{NS}}}defs')
                gradient = ET.SubElement(defs,f'{{{NS}}}linearGradient',id='photo-edge-fill',
                    x1='0',y1='0',x2='0' if vertical else '1',y2='1' if vertical else '0')
                for position in np.linspace(0,len(colors)-1,128):
                    rgb = [np.interp(position,np.arange(len(colors)),colors[:,c]) for c in range(3)]
                    ET.SubElement(gradient,f'{{{NS}}}stop',offset=str(position/(len(colors)-1)),
                                  **{'stop-color':'rgb('+','.join(str(round(v)) for v in rgb)+')'})
                ET.SubElement(out,f'{{{NS}}}rect',width=str(w),height=str(h),fill='url(#photo-edge-fill)')
            if dx > 0:
                extend(dx+scale,height,True)
            if dy > 0:
                extend(width,dy+scale,False)
            patch(dx,dy,pw,ph,(0,0,self.width,self.height),'photo-')
            return out
        x,y,r,b = profile['crop']
        crop = ET.SubElement(out,f'{{{NS}}}svg',width=str(width),height=str(height),
                             viewBox=f'{x} {y} {r-x} {b-y}',preserveAspectRatio='none',overflow='hidden')
        shell = self._shell(self.layers[profile['backdrop']])
        self._prefix_ids(shell,'background-')
        crop.append(shell)
        return out

    def svg(self, width, height, overrides=None):
        target_size(width,height)
        overrides = self.validate_overrides(overrides)
        out = ET.Element(f"{{{NS}}}svg", width=str(width), height=str(height),
                         viewBox=f"0 0 {width} {height}")
        strategy = self.strategy(width,height)
        scale = (max if strategy == 'svg_cover' else min)(width/self.width, height/self.height)
        dx,dy = (width-self.width*scale)/2,(height-self.height*scale)/2
        full_bleed = strategy == 'svg_full_bleed'
        if full_bleed:
            out = self.background_svg(width,height)
        if not overrides and not full_bleed:
            wrapper = ET.SubElement(out, f"{{{NS}}}g", transform=f"translate({dx} {dy}) scale({scale})")
            wrapper.append(copy.deepcopy(self.root))
        else:
            # Isolated documents get unique IDs so masks/gradients/use remain
            # unambiguous even when a referenced body object is copied to defs.
            for p in self.plan(width,height):
                i = p['index']; e = self.elements[i]
                if p.get('visible') is False:
                    continue
                x,y,w,h = e['bbox']
                a,b,c,d = overrides.get(i,p['bbox'])
                sx,sy = c/w,d/h
                wrapper = ET.SubElement(out, f"{{{NS}}}g", {"data-layer":str(i),
                    "transform":f"translate({a-x*sx} {b-y*sy}) scale({sx} {sy})"})
                shell = self._shell(self.layers[i])
                self._prefix_ids(shell, f"layer{i}-")
                wrapper.append(shell)
        return ET.tostring(out, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _prefix_ids(root, prefix):
        ids = {n.get('id') for n in root.iter() if n.get('id')}
        for n in root.iter():
            if n.get('id'):
                n.set('id',prefix+n.get('id'))
            for key,value in list(n.attrib.items()):
                value = URL_REF.sub(lambda m: f'url(#{prefix}{m[1]})', value)
                if key in ('href',XLINK) and value.startswith('#'):
                    value = '#'+prefix+value[1:]
                n.set(key,value)
            if tag(n) == 'style' and n.text:
                n.text = re.sub(r'#([\w-]+)',lambda m: '#'+prefix+m[1] if m[1] in ids else m[0],n.text)

    def layer_svg(self, index):
        x,y,w,h = self.elements[index]['bbox']
        out = ET.Element(f"{{{NS}}}svg", width=str(w),height=str(h),viewBox=f"{x} {y} {w} {h}")
        out.append(self._shell(self.layers[index]))
        return ET.tostring(out, encoding="utf-8", xml_declaration=True)


def analyze(path, sizes):
    doc = SVGDocument(path)
    elements = doc.extract()
    layers = []
    for i,e in enumerate(elements):
        x,y,w,h = e['bbox']
        preview = e['image'].copy(); preview.thumbnail((160,160))
        buf=io.BytesIO(); preview.save(buf,'PNG')
        layers.append(dict(index=i,name=e['name'],type=e['type'],role=e['role'],
            group_id=i,z=i,source_layer_ids=e['source_layer_ids'],native_equivalent=True,
            bbox_native=[x*doc.native_width/doc.width,y*doc.native_height/doc.height,
                         w*doc.native_width/doc.width,h*doc.native_height/doc.height],
            nbox=[x/doc.width,y/doc.height,w/doc.width,h/doc.height],
            preview_png_b64=base64.b64encode(buf.getvalue()).decode()))
    outputs={f'{w}x{h}':dict(placements=doc.plan(w,h),audit=[],strategy=doc.strategy(w,h),
                            requires_review=False,rejected_issues=[]) for w,h in sizes}
    return dict(source=dict(width=doc.width,height=doc.height,mode='svg',
                native_width=doc.native_width,native_height=doc.native_height,
                background=False,bg_color=None,renderer='resvg',
                resize_mode='full_bleed' if doc.photo_profile() else 'cover' if doc.is_flat_photo() else 'contain',
                layer_count=len(elements)),
                layers=layers,outputs=outputs,rules_loaded=0,rules_diagnostic=None,
                _elements=elements,_background=None,_bg_color=None,
                _bg_synth=lambda w,h: render(ET.tostring(doc.background_svg(w,h))),_svg_document=doc)
