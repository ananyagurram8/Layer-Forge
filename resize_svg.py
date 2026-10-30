"""Resize an SVG directly to files: python resize_svg.py 3.svg --out outputs."""
import argparse
import json
from pathlib import Path

from svg_native import analyze, render, target_size

DEFAULT_SIZES = ['1080x1080','1200x628','1080x1920','1200x1200','1200x1500']

def resize(source, destination, sizes):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = analyze(source, sizes)
    doc = manifest['_svg_document']
    manifest = {k:v for k,v in manifest.items() if not k.startswith('_')}
    manifest['input'] = str(source)
    for width,height in sizes:
        size = f'{width}x{height}'
        svg = doc.svg(width,height)
        image = render(svg)
        (destination/f'{size}.svg').write_bytes(svg)
        image.save(destination/f'{size}.png')
        manifest['outputs'][size].update(svg=f'{size}.svg',png=f'{size}.png',
            alpha_range=list(image.getchannel('A').getextrema()))
        assert image.size == (width,height)
        if doc.strategy(width,height) in ('svg_full_bleed','svg_cover'):
            assert image.getchannel('A').getextrema() == (255,255), 'Unfilled canvas'
        if doc.strategy(width,height) == 'svg_cover':
            scale = max(width/doc.width,height/doc.height)
            manifest['outputs'][size]['crop_source_xywh'] = [
                (doc.width-width/scale)/2,(doc.height-height/scale)/2,width/scale,height/scale]
            manifest['outputs'][size]['crop_note'] = 'Centered proportional crop of one flattened photo; subject and background are not separate layers.'
        print(f'Saved {size}.svg + .png ({doc.strategy(width,height)})',flush=True)
    (destination/'layers').mkdir(exist_ok=True)
    for layer in manifest['layers']:
        layer.pop('preview_png_b64',None)
        layer['svg'] = f"layers/{layer['index']:03d}.svg"
        (destination/layer['svg']).write_bytes(doc.layer_svg(layer['index']))
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--out',type=Path,default=Path('outputs'))
    parser.add_argument('--sizes',nargs='+',default=DEFAULT_SIZES)
    args = parser.parse_args()
    sizes = [target_size(*map(int,size.lower().split('x'))) for size in args.sizes]
    resize(args.source,args.out,sizes)
