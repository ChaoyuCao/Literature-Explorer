# Literature Explorer

Search PubMed, Europe PMC, and Crossref, merge duplicate records, and explore the results in an offline HTML report. Literature Explorer includes a Windows desktop interface and a Python command-line interface.

The application collects bibliographic metadata: titles, journals, identifiers, abstracts, authors, publication dates, and other fields supplied by each database. It does not download article PDFs or use AI services currently. Python 3.11 or newer is required for the Python package; the portable Windows app includes its runtime. The code is licensed under MIT.

## Features

- Search one or several databases and create a merged JSON file and HTML explorer automatically.
- Remove duplicates using DOI, PMID, and PMCID; retain original source records and database ranks.
- Sort and filter papers by journal, supplied impact factor, relevance, year, author, publication type, and review status.
- View journal/year/type counts, metadata coverage, keywords, authors, citation summaries, and database overlap.
- Import and export JSON, CSV, RIS, and BibTeX bibliographies.
- Organize papers with collections, tags, notes, favorites, and review decisions.
- Save, compare, and merge searches; resume interrupted searches; reuse cached metadata offline.
- Load an external journal-metric CSV and apply it to saved reports without searching again.

## Windows desktop app

The latest distributions are in [releases/0.2.3](releases/0.2.3). Extract the complete Windows ZIP and launch **LiteratureExplorer.exe**. Keep **literature.exe** beside it. No separate Python installation is required.

The query field starts empty. Database choices show a filled dot when selected and allow several selections at once. Enter keywords, choose databases and a per-database limit, and click **Search & build report**. The app includes Search, Paper library, and Search history tabs, with progress, Stop, and Resume controls.

Generated JSON and HTML files are written to the chosen workspace. The default desktop workspace is a `LiteratureExplorer` folder in the user's home directory. Settings are also stored there. The distribution contains no saved settings, paper library, search history, or generated reports. Existing installations may reuse their own local settings.

## Python installation

Install from this source directory:

```console
python -m venv .venv
python -m pip install .
python -m literature --help
python -m literature.gui
```

Activate the virtual environment before installing, or use its Python executable directly. On Windows the executable is `.venv\Scripts\python.exe`; on Linux/macOS it is `.venv/bin/python`. The installed commands are `literature` and `literature-gui`. Minimal Python installations may need platform-specific Tk support for the desktop interface.

To install the packaged wheel instead:

```console
python -m pip install releases/0.2.3/mechanosensor_literature-0.2.3-py3-none-any.whl
```

The application uses Python's standard library and has no third-party runtime package dependencies.

## Search and generate reports

The following commands are examples, not saved searches or preloaded content:

```console
python -m literature search --sources pubmed europepmc crossref --query "ion channel electrophysiology" --limit 25 --output outputs/search.json
python -m literature search --source pubmed --query-file examples/query.example.txt --limit 25 --output outputs/pubmed.json
python -m literature batch --file examples/batch.example.json
```

`--limit` applies **per database**. Omit it to retrieve all accessible results, subject to database caps. JSON reports create an HTML explorer automatically. Database field syntax differs: plain keyword text is appropriate for combined searches; PubMed field expressions should be used with PubMed. A query file preserves quoted phrases across Windows shells.

Filters include `--date-from`, `--date-to` (inclusive YYYY-MM-DD), `--author`, `--journal`, and `--article-type`. Crossref searches default to journal articles; `--article-type all` includes other work types. Relevance is a result's database position for that query, not a universal score. Ranks from separate databases are retained separately.

Sources are searched in the specified order. Later sources fill gaps while keeping original observations and provenance. Citation counts remain separate by provider. Title-only similarities are left for review rather than automatically merged. Partial results survive individual database failures and interruptions.

## Library and saved searches

```console
python -m literature jobs
python -m literature papers --text "ion channel"
python -m literature import references.ris --collection Reading
python -m literature export --library --collection Reading --format csv --output outputs/reading.csv
python -m literature search --source pubmed --query "ion channel" --limit 25 --save-as channels
python -m literature search --saved channels
```

Use the IDs returned by `jobs` and `papers` with `resume`, `merge --search-ids`, `compare --search-ids`, `visualize --search-id`, `annotate`, and `collections`. Run a command with `--help` for its arguments. `lookup` accepts DOI, PMID, or PMCID identifiers; `enrich` fills missing metadata for a selected library subset.

HTML notes, tags, favorites, and review decisions are stored in browser local storage. Export annotations and import them with `literature annotations --file FILE` to update the catalogue. Rebuild the report to include those edits. Annotation imports check the catalogue identity and detect stale edits.

## Journal impact factors

Real journal-metric tables are **external data** and are not included in this public repository or release. The CSV import feature remains available. [examples/journal-metrics.example.csv](examples/journal-metrics.example.csv) contains fictional values solely to demonstrate the format; do not use it for journal evaluation.

Required CSV columns are `journal`, `impact_factor`, `year`, and `source`. Optional columns include `issn`, `aliases` (separated by `|`), `status`, `source_kind`, `retrieved_at`, `release_year`, `source_page`, and `notes`. Use `status=unavailable` with a blank impact factor to explicitly record an unavailable current metric. Blank values without that status are skipped. A genuine numeric zero is retained.

In the desktop app, use **Browse** to validate and select your table. The app shows its journal count and metric year. It also detects a file named `journal-metrics.csv` in the workspace, its `queries` folder, or beside the executable; an installed `.literature/journal-metrics.csv` takes priority. **Clear** disables enrichment for new searches.

For a saved report, open it and select **Apply CSV to a report** to create a new JSON/HTML pair. Original files are preserved. Opening a JSON report also applies the selected CSV; without a selected table, it preserves existing embedded metrics. Existing HTML retains its embedded values until rebuilt.

```console
python -m literature metrics --file PATH_TO_YOUR_CSV
python -m literature search --query "ion channel" --limit 25 --journal-metrics PATH_TO_YOUR_CSV
python -m literature search --query "ion channel" --limit 25 --no-journal-metrics
```

Matching prefers ISSN and then an exact normalized name or explicit alias. One metric year is selected across journals, using the newest year in the table unless `--metrics-year` is specified. Record metric year and release year separately. Missing or unsupported values remain null; citation counts and CiteScore are not converted into impact factors. Metric sources and dates are displayed in reports. Journal Impact Factor is a journal-level attribute, not an article quality score.

An optional preparation script, `scripts/build_journal_metrics.py`, extracts the specific supported PDF table layout from a supplied reference file. It requires optional `pymupdf` and explicit year/source arguments. It is separate from application runtime and publication search.

## Configuration and data locations

CLI defaults are `.literature/` for the catalogue/cache and `outputs/` for reports, relative to the working directory. Global options precede the command:

```console
python -m literature --state .literature search --query "ion channel" --limit 25
python -m literature config --write literature-config.json
python -m literature --config literature-config.example.json config
python -m literature --offline search --saved channels
```

`literature-config.example.json` contains empty credential fields. `LITERATURE_CONFIG`, `LITERATURE_EMAIL`, and `NCBI_API_KEY` environment variables are supported. Never commit real credentials or local configuration. `--offline` requires previously cached responses; `--refresh` requests fresh metadata. Progress goes to stderr, and stdout contains JSON. Failures return 1; interruptions return 130 and preserve committed records.

## Development and distribution

See [DISTRIBUTION.md](DISTRIBUTION.md) for release builds and [VALIDATION.md](VALIDATION.md) for verification commands and limitations.

```text
literature/                 Application source and HTML/JavaScript assets
examples/                   Generic queries and a fictional CSV format example
scripts/                    Build, verification, and public-export tools
tests/                     Synthetic automated tests
releases/0.2.3/             Latest distributable artifacts and checksums
README.md                   Installation and usage
DISTRIBUTION.md              Building and sharing releases
VALIDATION.md               Reproducible verification
LICENSE                     MIT code license
```

Private runtime data, real metric tables, downloaded files, local environments, and build intermediates are excluded by `.gitignore`. To prepare a fresh public snapshot, build a release and run `python -m scripts.prepare_github --release-dir dist/0.2.3`. The exporter uses an explicit allowlist and includes only the newest distribution supplied to it.
