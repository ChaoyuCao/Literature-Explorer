"""Export an allowlisted public source tree and its latest data-free release."""
import argparse
import hashlib
import io
import json
import marshal
import re
import shutil
import tarfile
import tempfile
import types
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from literature import __version__

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_FILES = ['README.md', 'DISTRIBUTION.md', 'VALIDATION.md', 'LICENSE', '.gitignore',
                'MANIFEST.in', 'pyproject.toml', 'requirements-release.txt', 'literature-config.example.json',
                'examples/query.example.txt', 'examples/batch.example.json', 'examples/journal-metrics.example.csv']
PUBLIC_FOLDERS = {'literature': {'.py', '.html', '.js'}, 'scripts': {'.py'}, 'tests': {'.py'}}
BLOCKED_PARTS = {'.git', '.literature', '.venv', '.venv-build', '__pycache__', 'outputs',
                 'downloads', 'dataset', 'queries', 'skills', 'build', 'tmp', 'dist'}
TEXT_SUFFIXES = {'.py', '.js', '.html', '.md', '.txt', '.json', '.csv', '.toml', '.in', '.tcl'}


def check_member(name, compiled=False):
    parts = PurePosixPath(name.replace('\\', '/')).parts
    if '..' in parts or set(parts) & BLOCKED_PARTS or name.startswith(('/', '\\')) or re.match(r'^[a-zA-Z]:', name):
        raise ValueError(f'Private or unsafe archive member: {name}')
    if Path(name).name in {'journal-metrics.csv', 'journal-metrics-provenance.json', 'desktop-settings.json', 'literature-config.json'} or re.search(r'\.(?:sqlite3?|db|pdf)(?:-|$)', name) or (not compiled and name.endswith('.pyc')):
        raise ValueError(f'Private data in public export: {name}')


def check_text(name, data, generic_paths=True):
    text = data.decode('utf-8', errors='replace').replace('\\\\', '\\').replace('\\', '/').casefold()
    local_roots = [ROOT.as_posix().casefold(), Path.home().as_posix().casefold()]
    if any(root in text for root in local_roots) or (generic_paths and re.search(r'[a-z]:/(?:users|projects)/', text)):
        raise ValueError(f'Local machine path in public file: {name}')


def audit_executable(path):
    """Inspect executable payloads, including startup bytecode and embedded modules."""
    from PyInstaller.archive.readers import CArchiveReader

    def check_code(name, code):
        pending = [code]
        while pending:
            item = pending.pop()
            if Path(item.co_filename).is_absolute() or PureWindowsPath(item.co_filename).is_absolute():
                raise ValueError(f'Absolute build filename in executable module: {name}')
            check_text(name, repr([v for v in item.co_consts if isinstance(v, (str, bytes))]).encode(), generic_paths=False)
            pending.extend(v for v in item.co_consts if isinstance(v, types.CodeType))

    reader = CArchiveReader(path)
    for name, metadata in reader.toc.items():
        if metadata[-1] == 'z':
            embedded = reader.open_embedded_archive(name)
            for module in embedded.toc:
                code = embedded.extract(module)
                if isinstance(code, types.CodeType):
                    check_code(module, code)
        else:
            data = reader.extract(name)
            if metadata[-1] in ('m', 's'):
                check_code(name, marshal.loads(data))
            elif name.endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for member in archive.namelist():
                        check_member(member, compiled=True)
                        if member.endswith('.pyc'):
                            check_code(member, marshal.loads(archive.read(member)[16:]))
            else:
                check_member(name)
                check_text(name, data, generic_paths=False)
                check_text(name, data.decode('utf-16le', errors='ignore').encode(), generic_paths=False)


def audit_archive(path):
    if path.name.endswith('.tar.gz'):
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                check_member(member.name)
                if member.issym() or member.islnk():
                    raise ValueError(f'Unexpected source archive link: {member.name}')
                if member.isfile() and Path(member.name).suffix in TEXT_SUFFIXES:
                    check_text(member.name, archive.extractfile(member).read())
    else:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                check_member(member.filename)
                if Path(member.filename).suffix in TEXT_SUFFIXES or Path(member.filename).name in {'METADATA', '.gitignore'}:
                    check_text(member.filename, archive.read(member))
                elif member.filename.endswith('.exe'):
                    with tempfile.TemporaryDirectory(prefix='public-executable-audit-') as temporary:
                        executable = Path(temporary)/Path(member.filename).name
                        executable.write_bytes(archive.read(member))
                        audit_executable(executable)


def prepare(output, release):
    output, release = Path(output).resolve(), Path(release).resolve()
    if output.exists():
        raise ValueError('Export destination already exists. Choose a new --output-dir to preserve existing files.')
    archive_path = output.with_suffix('.zip')
    if archive_path.exists():
        raise ValueError('The matching snapshot ZIP already exists. Choose a new --output-dir.')
    manifest = json.loads((release/'release-manifest.json').read_text(encoding='utf-8'))
    if manifest['version'] != __version__:
        raise ValueError('The selected release must match the current source version.')
    artifact_names = [f'mechanosensor_literature-{__version__}-py3-none-any.whl',
                      f'mechanosensor_literature-{__version__}.tar.gz',
                      f'mechanosensor-literature-{__version__}-windows-x64.zip']
    entries = {entry['file']: entry for entry in manifest['artifacts']}
    for name in artifact_names:
        file = release/name
        if hashlib.sha256(file.read_bytes()).hexdigest() != entries[name]['sha256']:
            raise ValueError(f'Release checksum mismatch: {name}')
        audit_archive(file)
    config = json.loads((ROOT/'literature-config.example.json').read_text(encoding='utf-8'))
    if config.get('email') or config.get('ncbi_api_key') or config.get('journal_metrics'):
        raise ValueError('Public example configuration must contain no credentials or local metric path.')
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = ROOT/'build'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='public-export-', dir=scratch) as temporary:
        stage = Path(temporary)
        for name in PUBLIC_FILES:
            (stage/name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT/name, stage/name)
        for folder, suffixes in PUBLIC_FOLDERS.items():
            for file in (ROOT/folder).rglob('*'):
                if file.is_file() and '__pycache__' not in file.parts and file.suffix in suffixes:
                    target = stage/file.relative_to(ROOT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, target)
        selected = stage/'releases'/__version__
        selected.mkdir(parents=True)
        public_entries = []
        for name in artifact_names:
            shutil.copy2(release/name, selected/name)
            public_entries.append(entries[name])
        public_manifest = selected/'release-manifest.json'
        public_manifest.write_text(json.dumps({'name': manifest['name'], 'version': __version__,
            'license': 'MIT', 'artifacts': public_entries}, indent=2), encoding='utf-8')
        checksums = [dict(file=entry['file'], sha256=entry['sha256']) for entry in public_entries]
        checksums.append(dict(file=public_manifest.name, sha256=hashlib.sha256(public_manifest.read_bytes()).hexdigest()))
        if (release/'verification.json').exists():
            shutil.copy2(release/'verification.json', selected/'verification.json')
            checksums.append(dict(file='verification.json', sha256=hashlib.sha256((selected/'verification.json').read_bytes()).hexdigest()))
        (selected/'SHA256SUMS.txt').write_text(''.join(f"{entry['sha256']}  {entry['file']}\n" for entry in checksums), encoding='utf-8')
        inventory = []
        for file in sorted(stage.rglob('*')):
            if file.is_file():
                name = file.relative_to(stage).as_posix()
                check_member(name)
                if file.suffix in TEXT_SUFFIXES or file.name == '.gitignore':
                    check_text(name, file.read_bytes())
                inventory.append(dict(file=name, bytes=file.stat().st_size, sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
        (stage/'PUBLIC-EXPORT.json').write_text(json.dumps({'version': __version__,
            'policy': 'Allowlisted source, generic examples, and the latest data-free distribution. No runtime data or Git history.',
            'files': inventory}, indent=2), encoding='utf-8')
        shutil.copytree(stage, output)
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(output.rglob('*')):
            if file.is_file():
                archive.write(file, (Path(output.name)/file.relative_to(output)).as_posix())
    return {'directory': str(output), 'zip': str(archive_path), 'version': __version__, 'files': len(inventory)+1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', default=f'dist/{__version__}')
    parser.add_argument('--output-dir', default='github-ready')
    args = parser.parse_args()
    print(json.dumps(prepare(args.output_dir, args.release_dir), indent=2))


if __name__ == '__main__':
    main()
