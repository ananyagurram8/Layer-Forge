"""Standalone SVG editor; no training, database, PSD, AI, or plugin dependencies."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from resize_svg import DEFAULT_SIZES
from svg_native import analyze, render, target_size

app=FastAPI(title='SvgForge')
ROOT=Path(__file__).resolve().parent
SESSIONS={}
LOCK=threading.RLock()


def session(sid):
    with LOCK:
        value=SESSIONS.get(sid)
        if value is None or time.time()-value['last']>7200:
            SESSIONS.pop(sid,None)
            raise HTTPException(404,'Session expired; upload your SVG again.')
        value['last']=time.time()
        return value


def dimensions(size):
    try:
        return target_size(*map(int,size.lower().split('x')))
    except (ValueError,TypeError,AttributeError):
        raise HTTPException(400,'Invalid size; use e.g. 1080x1080.')


def png(image):
    buf=io.BytesIO();image.save(buf,'PNG');return buf.getvalue()


@app.get('/')
def home():
    return RedirectResponse('/editor')


@app.get('/editor')
@app.get('/editor/')
def editor():
    return FileResponse(ROOT/'editor.html',media_type='text/html')


@app.post('/editor/plan')
def plan(file: UploadFile=File(...)):
    if Path(file.filename or '').suffix.lower()!='.svg':
        raise HTTPException(415,'Upload an SVG file.')
    raw=file.file.read(64*1024*1024+1)
    if len(raw)>64*1024*1024:raise HTTPException(413,'SVG exceeds 64 MB.')
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.svg';path.write_bytes(raw)
            result=analyze(path,[dimensions(s) for s in DEFAULT_SIZES])
    except Exception as exc:
        raise HTTPException(422,f'Could not analyze SVG: {exc}')
    doc=result['_svg_document']
    result={k:v for k,v in result.items() if not k.startswith('_')}
    sid=uuid.uuid4().hex
    filename=Path(file.filename).name
    for layer in result['layers']:
        i=layer['index']
        layer.update(bbox_src=list(doc.elements[i]['bbox']),locked=False,has_image=True,
                     url=f'/editor/layer/{sid}/{i}.png',svg_url=f'/editor/layer/{sid}/{i}.svg')
        layer.pop('preview_png_b64',None)
    result.update(sid=sid,file=filename,file_hash=hashlib.sha256(raw).hexdigest(),kind='svg',
                  force_smart=False,bg_color=None,sizes=list(DEFAULT_SIZES),
                  bg_urls={s:f'/editor/bg/{sid}/{s}.png' for s in DEFAULT_SIZES})
    with LOCK:
        now=time.time()
        for key in list(SESSIONS):
            if now-SESSIONS[key]['last']>7200:del SESSIONS[key]
        while len(SESSIONS)>=8:
            del SESSIONS[min(SESSIONS,key=lambda k:SESSIONS[k]['last'])]
        SESSIONS[sid]=dict(doc=doc,manifest=copy.deepcopy(result),sizes=list(DEFAULT_SIZES),last=now)
    return result


@app.get('/editor/bg/{sid}/{size}.png')
def background(sid: str,size: str):
    doc=session(sid)['doc'];w,h=dimensions(size)
    return Response(png(render(ET.tostring(doc.background_svg(w,h)))),media_type='image/png')


@app.get('/editor/layer/{sid}/{index}.{format}')
def layer(sid: str,index: int,format: str):
    doc=session(sid)['doc']
    if not 0<=index<len(doc.elements):raise HTTPException(404,'Layer not found.')
    if format=='svg':
        return Response(doc.layer_svg(index),media_type='image/svg+xml',
                        headers={'Content-Disposition':f'attachment; filename="layer_{index:03d}.svg"'})
    if format=='png':return Response(png(doc.elements[index]['image']),media_type='image/png')
    raise HTTPException(404,'Unsupported layer format.')


@app.post('/editor/size')
def size(payload: dict):
    value=session(str(payload.get('sid','')));doc=value['doc']
    try:
        w,h=target_size(payload.get('width'),payload.get('height'))
    except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc))
    key=f'{w}x{h}'
    if key not in value['sizes']:value['sizes'].append(key)
    return dict(size=key,output=dict(placements=doc.plan(w,h),strategy=doc.strategy(w,h),requires_review=False),
                bg_url=f"/editor/bg/{payload['sid']}/{key}.png")


def svg_output(doc,size,overrides):
    w,h=dimensions(size)
    try:return doc.svg(w,h,overrides)
    except (ValueError,TypeError,OverflowError) as exc:raise HTTPException(400,str(exc))


@app.post('/editor/render')
def preview(payload: dict):
    doc=session(str(payload.get('sid','')))['doc']
    data=svg_output(doc,payload.get('size',''),payload.get('overrides'))
    return Response(png(render(data)),media_type='image/png',headers={'Cache-Control':'no-store'})


@app.post('/editor/export')
def export(payload: dict):
    value=session(str(payload.get('sid','')));doc=value['doc']
    overrides=payload.get('overrides') or {}
    if not isinstance(overrides,dict):raise HTTPException(400,'Overrides must be an object.')
    manifest=copy.deepcopy(value['manifest']);manifest['outputs']={}
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as archive:
        for key in list(value['sizes']):
            data=svg_output(doc,key,overrides.get(key));w,h=dimensions(key)
            archive.writestr(key+'.svg',data);archive.writestr(key+'.png',png(render(data)))
            placed=doc.plan(w,h)
            changed=doc.validate_overrides(overrides.get(key))
            for p in placed:
                if p['index'] in changed:p.update(bbox=changed[p['index']],how='user_override')
            manifest['outputs'][key]=dict(svg=key+'.svg',png=key+'.png',placements=placed,
                                          strategy=doc.strategy(w,h))
        for item in manifest['layers']:
            i=item['index'];item['svg']=f'layers/{i:03d}.svg';item['png']=f'layers/{i:03d}.png'
            archive.writestr(item['svg'],doc.layer_svg(i));archive.writestr(item['png'],png(doc.elements[i]['image']))
            item.pop('url',None);item.pop('svg_url',None)
        for key in ('sid','bg_urls'):manifest.pop(key,None)
        archive.writestr('manifest.json',json.dumps(manifest,indent=2))
    return Response(buf.getvalue(),media_type='application/zip',
                    headers={'Content-Disposition':'attachment; filename="svgforge-results.zip"'})
