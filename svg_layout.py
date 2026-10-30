"""Full-frame layout for layered photo ads with a separate product cutout.

The clean side of the original photo supplies the background texture. Only that
texture can stretch. Product, lettering, logos and related plates scale uniformly.
This is deliberately gated by source structure and background texture evidence.
"""
import numpy as np


def bounds(elements, ids):
    boxes = [elements[i]['bbox'] for i in ids]
    x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return x, y, max(b[0]+b[2] for b in boxes)-x, max(b[1]+b[3] for b in boxes)-y


def photo_profile(doc):
    es, sw, sh = doc.elements, doc.width, doc.height
    area = sw*sh
    backgrounds = [i for i,e in enumerate(es)
                   if e['bbox'][2] >= .98*sw and e['bbox'][3] >= .98*sh
                   and np.asarray(e['image'])[...,3].mean() > 245]
    photos = [i for i in backgrounds if es[i]['type'] == 'raster']
    if not photos:
        return None
    backdrop = photos[-1]
    # Do not pull a later overlay/background behind foreground content.
    if backgrounds != list(range(max(backgrounds)+1)):
        return None
    candidates = [i for i,e in enumerate(es) if i not in backgrounds
                  and e['type'] == 'raster'
                  and .18*area < e['bbox'][2]*e['bbox'][3] < .8*area
                  and np.asarray(e['image'])[...,3].mean() > 90]
    if not candidates:
        return overlay_photo_profile(doc, backgrounds, backdrop)
    if len(candidates) != 1:
        return None
    hero = candidates[0]
    hx,hy,hw,hh = es[hero]['bbox']
    if hx < .25*sw or hx+hw < .94*sw or hy+hh < .94*sh:
        return None  # this planner handles a right/bottom anchored photo scene
    # Avoid the source clip's antialiased edge and the product's footprint.
    crop = (2, 2, int(hx-.03*sw), sh-2)
    if crop[2]-crop[0] < .22*sw:
        return None
    bx,by,_,_ = es[backdrop]['bbox']
    pixels = np.asarray(es[backdrop]['image'])
    patch = pixels[int(crop[1]-by):int(crop[3]-by), int(crop[0]-bx):int(crop[2]-bx)]
    if patch.size == 0 or patch[...,3].min() < 250:
        return None
    # A smooth wall/sky can be extended; lettering or another subject cannot.
    rgb = patch[...,:3].astype(float)
    if max(np.quantile(np.abs(np.diff(rgb,axis=0)),.99),
           np.quantile(np.abs(np.diff(rgb,axis=1)),.99)) > 18:
        return None
    decor = [i for i,e in enumerate(es) if i not in backgrounds+[hero]
             and e['bbox'][2]*e['bbox'][3] > .12*area
             and np.asarray(e['image'])[...,3].mean() < 45]
    content = [i for i in range(len(es)) if i not in backgrounds+[hero]+decor]
    # Bind overlapping plates/lettering and adjacent lines into rigid blocks.
    parent = {i:i for i in content}
    def find(i):
        while parent[i] != i:
            i = parent[i]
        return i
    for k,i in enumerate(content):
        x,y,w,h = es[i]['bbox']
        for j in content[:k]:
            a,b,c,d = es[j]['bbox']
            ix,iy = min(x+w,a+c)-max(x,a), min(y+h,b+d)-max(y,b)
            near = ((ix > .4*min(w,c) and -iy < .25*min(h,d)) or
                    (iy > .35*min(h,d) and -ix < .025*sw))
            if (ix > 0 and iy > 0) or near:
                parent[find(i)] = find(j)
    grouped = {}
    for i in content:
        grouped.setdefault(find(i),[]).append(i)
    blocks = [dict(ids=ids,bbox=bounds(es,ids)) for ids in grouped.values()]
    logos = [b for b in blocks if b['bbox'][0] > .65*sw and
             b['bbox'][1]+b['bbox'][3] < .25*sh]
    footers = sorted([b for b in blocks if b['bbox'][1] > .72*sh],key=lambda b:b['bbox'][1])
    flow = sorted([b for b in blocks if b not in logos+footers],key=lambda b:b['bbox'][1])
    if not flow or len(flow) < 2 or len(footers) > 2:
        return None
    headline = max(flow,key=lambda b:b['bbox'][3])
    body = [b for b in flow if b is not headline]
    return dict(backgrounds=backgrounds,backdrop=backdrop,hero=hero,decor=decor,
                crop=crop,blocks=blocks,headline=headline,body=body,logos=logos,footers=footers)


def overlay_photo_profile(doc, backgrounds, backdrop):
    """Keep a flattened scene intact when separate copy occupies its left side.

    Only thin, opaque, smooth photo edges can extend into the added canvas.
    Foreground artwork stays in one rigid composition, including outlined text.
    """
    es, sw, sh = doc.elements, doc.width, doc.height
    if min(sw,sh) < 40:
        return None
    content = [i for i in range(len(es)) if i not in backgrounds]
    if len(content) < 3:
        return None
    x,y,w,h = bounds(es,content)
    if x < 0 or y < 0 or x+w > .65*sw or y+h > .95*sh or h < .4*sh:
        return None
    if any(es[i]['type'] == 'raster' and es[i]['bbox'][2]*es[i]['bbox'][3] > .05*sw*sh
           for i in content):
        return None
    pixels = np.asarray(es[backdrop]['image'])
    if es[backdrop]['bbox'] != (0.,0.,float(sw),float(sh)) or pixels[...,3].min() != 255:
        return None
    for strip in (pixels[:20,:,:3],pixels[:,:20,:3]):
        rgb = strip.astype(float)
        if max(np.quantile(np.abs(np.diff(rgb,axis=0)),.99),
               np.quantile(np.abs(np.diff(rgb,axis=1)),.99)) > 18:
            return None
    return dict(kind='photo_overlay', backgrounds=backgrounds,backdrop=backdrop,
                content=content,content_bbox=(x,y,w,h))


def full_frame_plan(doc, profile, tw, th):
    es, sw, sh = doc.elements, doc.width, doc.height
    if profile.get('kind') == 'photo_overlay':
        x,y,w,h = profile['content_bbox']
        if tw/th <= sw/sh:
            scale = min(.82*tw/w,.56*th/h)
            left,top = .07*tw,.04*th
        else:
            scale = min(.43*tw/w,.9*th/h)
            left,top = .045*tw,.05*th
        return [dict(index=i,name=e['name'],group='copy',
                     bbox=[0,0,tw,th] if i in profile['backgrounds'] else
                     [left+(e['bbox'][0]-x)*scale,top+(e['bbox'][1]-y)*scale,
                      e['bbox'][2]*scale,e['bbox'][3]*scale],
                     how='background_fill' if i in profile['backgrounds'] else 'svg_full_bleed',
                     visible=i not in profile['backgrounds']) for i,e in enumerate(es)]
    out = {}
    def transform(ids, scale, dx, dy):
        for i in ids:
            x,y,w,h = es[i]['bbox']
            out[i] = [dx+x*scale,dy+y*scale,w*scale,h*scale]
    def fit(block, rect):
        x,y,w,h = block['bbox'];a,b,c,d = rect
        s = min(c/w,d/h)
        transform(block['ids'],s,a-x*s,b-y*s)
    hx,hy,hw,hh = es[profile['hero']]['bbox']
    if tw/th <= sw/sh:
        # More vertical room: retain the complete information column, and
        # enlarge/anchor the separate product scene to the bottom right.
        transform([i for b in profile['blocks'] for i in b['ids']],tw/sw,0,0)
        hs = min(.80*tw/hw,.65*th/hh)
    else:
        # Wide ads use title + benefit columns, with trust/delivery below.
        fit(profile['headline'],(.035*tw,.08*th,.21*tw,.49*th))
        ids = [i for b in profile['body'] for i in b['ids']]
        fit(dict(ids=ids,bbox=bounds(es,ids)),(.28*tw,.18*th,.26*tw,.43*th))
        for k,block in enumerate(profile['footers']):
            fit(block,((.035+.27*k)*tw,.78*th,.25*tw,.15*th))
        for k,block in enumerate(profile['logos']):
            x,y,w,h = block['bbox']
            s = min(.17*tw/w,.085*th/h)
            transform(block['ids'],s,tw-.035*tw-w*s-x*s,(.035+.1*k)*th-y*s)
        hs = min(.42*tw/hw,.84*th/hh)
    transform([profile['hero']]+profile['decor'],hs,tw-(hx+hw)*hs,th-(hy+hh)*hs)
    result = []
    group_by_id = {i:k for k,b in enumerate(profile['blocks']) for i in b['ids']}
    for i,e in enumerate(es):
        background = i in profile['backgrounds']
        result.append(dict(index=i,name=e['name'],
                           bbox=[0,0,tw,th] if background else out[i],
                           group=group_by_id.get(i,'photo'),
                           how='background_fill' if background else 'svg_full_bleed',
                           visible=not background))
    return result
