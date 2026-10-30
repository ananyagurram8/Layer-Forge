"""Friend-friendly launcher: locate Python dependencies, then process inputs/."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent


def environment_python(folder):
    return folder/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')


def runtime():
    candidates=[Path(sys.executable)]+[environment_python(ROOT/name) for name in ('venv','.venv','.svgforge-venv')]
    versions=dict(line.strip().split('==') for line in (ROOT/'requirements-svg.txt').read_text().splitlines()
                  if line.strip() and not line.startswith('#'))
    check=('import sys; from importlib.metadata import version; assert sys.version_info >= (3,11); '
           'import PIL, numpy, resvg_py, cssselect2, tinycss2; '
           f'assert all(version(name)==expected for name,expected in {versions!r}.items())')
    for candidate in candidates:
        try:
            if subprocess.run([str(candidate),'-c',check],stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode==0:
                return candidate
        except OSError:
            continue
    if sys.version_info < (3,11):
        raise RuntimeError('Install Python 3.11 or newer, then run again.')
    env=ROOT/'.svgforge-venv'
    print('First run: setting up SVG dependencies (internet required)...',flush=True)
    subprocess.run([sys.executable,'-m','venv',str(env)],check=True)
    python=environment_python(env)
    subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'requirements-svg.txt')],check=True)
    return python


def main():
    try:
        # Help does not need packages or a first-run installation.
        python=Path(sys.executable) if '--help' in sys.argv or '-h' in sys.argv else runtime()
        return subprocess.call([str(python),'-B',str(ROOT/'batch_svg.py'),*sys.argv[1:]])
    except (OSError,RuntimeError,subprocess.CalledProcessError) as exc:
        print(f'Could not start SvgForge: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
