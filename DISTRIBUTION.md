# Distribution

## Current release

Release **0.2.3** includes:

| Artifact | Purpose |
|---|---|
| `mechanosensor-literature-0.2.3-windows-x64.zip` | Portable Windows desktop app and CLI; no Python installation required |
| `mechanosensor_literature-0.2.3-py3-none-any.whl` | Python package for Python 3.11+ |
| `mechanosensor_literature-0.2.3.tar.gz` | Source distribution with tests and build scripts |
| `SHA256SUMS.txt` and `release-manifest.json` | Artifact identity, sizes, and integrity checks |
| `verification.json` | Results from isolated package verification |

The GitHub-ready source tree stores only these latest artifacts in `releases/0.2.3/`. It contains no private runtime data or Git history. The Windows ZIP includes the MIT license and notices for bundled Python, Tcl/Tk, and the PyInstaller bootloader.

## Run or install

Extract the Windows ZIP and launch **LiteratureExplorer.exe**. Keep **literature.exe** beside it. To use the CLI from the extracted folder:

```powershell
.\literature.exe --help
.\literature.exe search --sources pubmed europepmc crossref --query "ion channel electrophysiology" --limit 25
```

Install the Python wheel with `python -m pip install PATH_TO_WHEEL`; launch with `python -m literature` or `python -m literature.gui`. Source installation also works with `python -m pip install .`. The pure Python wheel uses the standard library; desktop use requires Tk support. The Windows executable targets x64. Other platform executables require builds on those platforms.

Searches create writable catalogue/cache and report directories outside the executable bundle. Desktop preferences live in the user's home directory. The public distribution starts with an empty query and includes no saved workspace settings, paper library, search history, generated reports, downloaded papers, or real journal-metric CSV. Users can load their own CSV; README.md documents its format and application to existing reports.

## Build locally

Use a Python environment with the release tools installed. On Windows:

```powershell
python -m venv .venv-build
.venv-build\Scripts\python -m pip install -r requirements-release.txt
.venv-build\Scripts\python -m unittest discover -s tests
.venv-build\Scripts\python -m scripts.build_release --output-dir dist/0.2.3
.venv-build\Scripts\python -m scripts.verify_release --dist dist/0.2.3
.venv-build\Scripts\python -m scripts.smoke_desktop --engine dist/0.2.3/literature.exe
```

On non-Windows systems use `--skip-windows` with the build command. The build creates a source archive, builds the wheel from that archive, checks package metadata, bundles the application with PyInstaller, and writes checksums. Build outputs and intermediates stay in ignored `dist/` and `build/` folders.

Real journal data is excluded by default. `--journal-metrics PATH` explicitly adds a selected external CSV to a private portable build. Keep that separate from a public export unless you intend to distribute the reference data. The public exporter rejects release archives containing real journal-metric tables.

## Prepare a GitHub-ready folder

```console
python -m scripts.prepare_github --release-dir dist/0.2.3
```

This produces `github-ready/` and `github-ready.zip`. Upload the **contents of github-ready/** as the repository; the ZIP is an equivalent snapshot for transfer. The export includes source, documentation, synthetic tests, generic examples, and only the selected latest release. It excludes private query files, research skills/data, reports, caches, settings, credentials, environments, old distributions, build intermediates, and existing Git metadata.

Run the exporter in the release-build environment with `requirements-release.txt` installed. PyInstaller is used to inspect executable payloads; use the same Python version that built the executable for bytecode inspection. If the export destination already exists, choose a new `--output-dir` to preserve it.

The exporter scans text files and the contents of wheel/source/Windows archives for local machine paths and blocked private files. It writes a generic export inventory with checksums. `releases/0.2.3/SHA256SUMS.txt` verifies the release artifacts themselves. Existing private working directories are not modified by the export.

No remote repository creation, upload, package-registry publication, or GitHub release publication is performed by these scripts.
