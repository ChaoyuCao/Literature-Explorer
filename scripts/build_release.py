"""Build local release artifacts without publishing to a registry."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*arguments):
    subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default='dist')
    parser.add_argument('--skip-windows', action='store_true')
    parser.add_argument('--journal-metrics', help='Explicitly include this external CSV and its provenance file beside the portable executables')
    args = parser.parse_args()
    from literature import __version__
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    run('-m', 'build', '--no-isolation', '--outdir', output)
    wheel = output / f'mechanosensor_literature-{__version__}-py3-none-any.whl'
    source = output / f'mechanosensor_literature-{__version__}.tar.gz'
    run('-m', 'twine', 'check', '--strict', wheel, source)
    artifacts = [wheel, source]
    metric_artifacts = []
    if args.journal_metrics:
        from literature.reports import load_metrics
        table = Path(args.journal_metrics).resolve()
        names, _, year = load_metrics(table)
        if not names or year is None:
            raise ValueError('The release metric CSV has no usable journal entries.')
        target = output / 'journal-metrics.csv'
        shutil.copy2(table, target)
        metric_artifacts.append(target)
        provenance = table.with_name(table.stem + '-provenance.json')
        if provenance.exists():
            target = output / 'journal-metrics-provenance.json'
            shutil.copy2(provenance, target)
            metric_artifacts.append(target)
        artifacts.extend(metric_artifacts)
    if not args.skip_windows:
        if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64'):
            raise RuntimeError('The Windows x64 executable requires Windows x64. Use --skip-windows for Python artifacts only.')
        work = ROOT / 'build' / 'release'
        work.mkdir(parents=True, exist_ok=True)
        run('-m', 'PyInstaller', '--noconfirm', '--onefile', '--console', '--noupx',
            '--name', 'literature', '--paths', ROOT, '--distpath', output,
            '--workpath', work / 'pyinstaller', '--specpath', work,
            '--add-data', str(ROOT / 'literature/dashboard.html') + ':literature',
            '--add-data', str(ROOT / 'literature/explorer.js') + ':literature',
            ROOT / 'scripts/release_entry.py')
        executable = output / 'literature.exe'
        run('-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed', '--noupx',
            '--name', 'LiteratureExplorer', '--paths', ROOT, '--distpath', output,
            '--workpath', work / 'pyinstaller-gui', '--specpath', work,
            '--add-data', str(ROOT / 'literature/dashboard.html') + ':literature',
            '--add-data', str(ROOT / 'literature/explorer.js') + ':literature',
            ROOT / 'scripts/gui_entry.py')
        desktop = output / 'LiteratureExplorer.exe'
        stage = work / 'portable'
        stage.mkdir(parents=True, exist_ok=True)
        portable_files = []
        for name in ('README.md', 'DISTRIBUTION.md', 'LICENSE', 'literature-config.example.json'):
            shutil.copy2(ROOT / name, stage / name)
            portable_files.append(stage / name)
        python_license = Path(sys.base_prefix) / 'LICENSE.txt'
        if not python_license.exists():
            raise RuntimeError('The Python runtime license file is required for the portable bundle.')
        shutil.copy2(python_license, stage / 'LICENSE-PYTHON.txt')
        portable_files.append(stage / 'LICENSE-PYTHON.txt')
        tk_license = next((Path(sys.base_prefix) / 'tcl').glob('tk*/license.terms'), None)
        if tk_license:
            shutil.copy2(tk_license, stage / 'LICENSE-TK.txt')
            portable_files.append(stage / 'LICENSE-TK.txt')
        installer = importlib.metadata.distribution('pyinstaller')
        license_file = next(p for p in installer.files if p.name == 'COPYING.txt')
        shutil.copy2(installer.locate_file(license_file), stage / 'LICENSE-PYINSTALLER.txt')
        portable_files.append(stage / 'LICENSE-PYINSTALLER.txt')
        (stage / 'THIRD-PARTY-NOTICES.txt').write_text(
            'This application is licensed under MIT; see LICENSE.\n'
            'The Windows executable includes CPython and its bundled libraries; see LICENSE-PYTHON.txt.\n'
            'The desktop app includes Tcl/Tk; see LICENSE-PYTHON.txt and LICENSE-TK.txt when present.\n'
            'The PyInstaller bootloader is covered by the license and distribution exception in LICENSE-PYINSTALLER.txt.\n'
            f'PyInstaller version: {installer.version}; source: https://github.com/pyinstaller/pyinstaller/tree/v{installer.version}\n'
            + ('Journal metrics are external reference data, separate from the MIT code. Each CSV row identifies its source, metric year, retrieval date and verification status. Most rows come from a secondary public JCR copy; see journal-metrics-provenance.json.\n' if metric_artifacts else ''), encoding='utf-8')
        portable_files.append(stage / 'THIRD-PARTY-NOTICES.txt')
        portable = output / f'mechanosensor-literature-{__version__}-windows-x64.zip'
        with zipfile.ZipFile(portable, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(executable, 'literature.exe')
            archive.write(desktop, 'LiteratureExplorer.exe')
            for path in sorted([*portable_files, *metric_artifacts]):
                if path.is_file():
                    archive.write(path, path.name)
        artifacts.extend([executable, desktop, portable])
    inventory = {d.metadata['Name']: d.version for d in importlib.metadata.distributions()}
    build_versions = output / 'build-environment.txt'
    build_versions.write_text('\n'.join(f'{name}=={version}' for name,version in sorted(inventory.items(), key=lambda pair: pair[0].casefold())) + '\n', encoding='utf-8')
    artifacts.append(build_versions)
    entries = [{'file': p.name, 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in artifacts]
    manifest = output / 'release-manifest.json'
    manifest.write_text(json.dumps({'name': 'mechanosensor-literature', 'version': __version__,
        'license': 'MIT', 'built_at': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(), 'build_platform': platform.platform(),
        'build_tools': inventory, 'artifacts': entries}, indent=2), encoding='utf-8')
    entries.append({'file': manifest.name, 'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()})
    (output / 'SHA256SUMS.txt').write_text('\n'.join(f"{entry['sha256']}  {entry['file']}" for entry in entries) + '\n', encoding='utf-8')
    print(json.dumps({'release_directory': str(output), 'version': __version__, 'artifacts': [p.name for p in artifacts]}, indent=2))


if __name__ == '__main__':
    main()
