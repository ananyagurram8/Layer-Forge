"""Resize every SVG in inputs/, with independent results and a run report."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent


def run_batch(source_dir, output_dir, sizes):
    from resize_svg import resize
    source_dir, output_dir = Path(source_dir).resolve(), Path(output_dir).resolve()
    if not source_dir.is_dir():
        raise ValueError(f'Input folder does not exist: {source_dir}')
    if output_dir == source_dir or source_dir in output_dir.parents:
        raise ValueError('Keep the output folder outside the input folder.')
    files = sorted((p for p in source_dir.rglob('*') if p.is_file() and p.suffix.lower()=='.svg'),
                   key=lambda p:p.relative_to(source_dir).as_posix().casefold())
    if not files:
        print(f'No SVGs found. Add .svg files to {source_dir} and run again.')
        return None
    run_dir = output_dir / ('run-'+datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f'))
    run_dir.mkdir(parents=True)
    keys = Counter(p.relative_to(source_dir).with_suffix('').as_posix().casefold() for p in files)
    report = dict(input_folder=str(source_dir),output_folder=str(run_dir),succeeded=0,failed=0,files=[])
    for number,source in enumerate(files,1):
        relative = source.relative_to(source_dir)
        folder = relative.with_suffix('')
        if keys[folder.as_posix().casefold()] > 1:
            folder = folder.with_name(folder.name+'-'+hashlib.sha256(relative.as_posix().encode()).hexdigest()[:10])
        destination = run_dir/folder
        print(f'\n[{number}/{len(files)}] {relative}',flush=True)
        staging = Path(tempfile.mkdtemp(prefix='.processing-',dir=run_dir))
        entry = dict(input=relative.as_posix())
        try:
            manifest = resize(source,staging,sizes)
            destination.parent.mkdir(parents=True,exist_ok=True)
            staging.rename(destination)
            modes = sorted({o['strategy'] for o in manifest['outputs'].values()})
            entry.update(status='ok',output=folder.as_posix(),layers=len(manifest['layers']),strategies=modes)
            if 'svg_cover' in modes:
                entry['note']='The flattened photo is cropped proportionally to fill each size.'
            elif 'svg_proportional' in modes and manifest['source']['resize_mode']=='contain':
                entry['note']='This artwork uses proportional fit; different aspect ratios may have transparent margins.'
            report['succeeded'] += 1
            print(f"OK: {entry['layers']} layers -> {destination}",flush=True)
            if entry.get('note'):print(entry['note'],flush=True)
        except Exception as exc:
            entry.update(status='failed',error=str(exc))
            report['failed'] += 1
            print(f'FAILED: {exc}',flush=True)
        finally:
            if staging.exists():shutil.rmtree(staging)
        report['files'].append(entry)
        (run_dir/'batch-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f"\nFinished: {report['succeeded']} succeeded, {report['failed']} failed.\nResults: {run_dir}")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'inputs',help='SVG folder (includes subfolders)')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs',help='Parent folder for each dated run')
    parser.add_argument('--sizes',nargs='+',help='Optional sizes, e.g. 1080x1080 1080x1920')
    args=parser.parse_args(argv)
    from resize_svg import DEFAULT_SIZES
    from svg_native import target_size
    try:
        sizes=[target_size(*map(int,s.lower().split('x'))) for s in (args.sizes or DEFAULT_SIZES)]
        report=run_batch(args.input,args.output,sizes)
    except (ValueError,TypeError,OSError) as exc:
        parser.error(str(exc))
    return 1 if report and report['failed'] else 0


if __name__=='__main__':
    raise SystemExit(main())
