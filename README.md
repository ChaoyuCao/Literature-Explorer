# Literature Explorer

Search PubMed, Europe PMC, and Crossref, merge duplicate records, and explore the results in an offline HTML report. Literature Explorer includes a Windows desktop interface and a Python command-line interface.

The application collects bibliographic metadata: titles, journals, identifiers, abstracts, authors, publication dates, and other fields supplied by each database. It does not download article PDFs or use AI services. Python 3.11 or newer is required for the Python package; the portable Windows app includes its runtime. The code is licensed under MIT.

## Features

- Search one or several databases and create a merged JSON file and HTML explorer automatically.
- Remove duplicates using DOI, PMID, and PMCID; retain original source records and database ranks.
- Sort and filter papers by journal, supplied impact factor, relevance, year, author, publication type, and review status.
- View journal/year/type counts, metadata coverage, keywords, authors, citation summaries, and database overlap.
- Import and export JSON, CSV, RIS, and BibTeX bibliographies.
- Organize papers with collections, tags, notes, favorites, and review decisions.
- Save, compare, and merge searches; resume interrupted searches; reuse cached metadata offline.
- Load an external journal-metric CSV and apply it to saved reports without searching again.
- Optionally retrieve reference lists into a separate, resumable JSON file.
- Explore citation links between collected papers and export their network.
- Discover papers one citation step from selected seeds, following references, citing papers, or both.
- Compare keyword/MeSH/title-term trends across years and inspect term co-occurrence.

## Windows desktop app

The latest distributions are in [releases/0.4.0](releases/0.4.0). Extract the complete Windows ZIP and launch **LiteratureExplorer.exe**. Keep **literature.exe** beside it. No separate Python installation is required.

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
python -m pip install releases/0.4.0/mechanosensor_literature-0.4.0-py3-none-any.whl
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

## References, citation networks, and discovery

Reference retrieval is **off by default**. Use **Retrieve reference lists** in Citation analysis with the latest report, selected library papers, or selected searches. **Build citation network** is optional. Retrieval immediately produces a browsable evidence file; it does not wait for remote metadata enrichment. **Resolve / enrich retrieved references** is a separate, resumable action. **Browse references** opens the reference browser.

```powershell
# Retrieve observations for selected saved papers, without enrichment.
python -m literature references --search-id 1 --ids 1 2 --output outputs/references.json
# Resolve a previously retrieved file, retaining its original evidence.
python -m literature resolve-references --report outputs/references.json --references-file outputs/references.references.json --citation-network --output outputs/resolved.json
# Resolve only a subset of a report's seed papers.
python -m literature resolve-references --report outputs/references.json --ids 1 --references-file outputs/references.references.json --output outputs/selected.json
# Explicit one-hop expansion using resolved references.
python -m literature discover --report outputs/resolved.json --references-file outputs/resolved.references.json --direction references --limit 100 --output outputs/discovery.json
```

Use the same `--state` catalogue that created the saved report. Commands also accept `--library --ids`, `--search-ids`, and the existing library filters. `--resolve-references` can opt into enrichment during search/reference analysis; the default keeps the two operations separate. Original input files are preserved; select a new output name. The original search query, sources, dates, reported totals, limits and limited-search status remain in the report and reference context.

For `references.json`, the tool writes:

- `references.references.json`: schema 2 original observations, canonical publications, provider checkpoints, matching evidence, candidates, resolution tasks and citation relationships.
- `references.publications.json`: one metadata row per identified referenced publication, including available title, authors, journal, year, abstract, identifiers, links, library membership and supporting observations. Missing metadata is `null`; unresolved observations are separate.
- `references.references.html`: an offline reference browser with seed/library/text filters, original evidence, candidate review, provider coverage and publication/evidence JSON exports.
- `references.network.json`: optional network between displayed papers; repeated observations support one edge. Article-to-itself links are flagged, with evidence IDs, and excluded from normal edges, degree statistics and discovery. Same-author citations to other publications remain valid.
- `references.html`: the main paper explorer with coverage and a link to the reference browser.

Schema 1 reference files are upgraded in memory and written to new outputs. Legacy `papers.references` groups remain for compatibility and **must not be counted as distinct publications**. Schema 2 uses separate `observations`, `publications`, and `relationships` layers. Canonical IDs can change when identifier bridges consolidate groups; observation IDs preserve evidence continuity. Reference lists remain separate from the main paper JSON/HTML.

Crossref, PubMed XML and Europe PMC supply available reference metadata. PubMed `pubmed` ArticleId values are normalized to PMID. DOI/PMID/PMCID and source-qualified IDs consolidate only when compatible. Existing library metadata is reused; optional enrichment batches PubMed metadata in groups of up to 100 and uses Europe PMC core metadata to bridge identifiers, with Crossref DOI lookup as fallback. Citation-only references get deterministic local and Crossref bibliographic candidates with field-comparison evidence; weak and ambiguous candidates require review and are never automatically merged. Provider list positions never match publications.

The browser reports papers requested/processed, raw observations, distinct identified publications, unresolved/ambiguous/review/conflict observations, distinct citation relationships, flagged self-links, and provider retrieval/pagination outcomes. Counts may consolidate further after enrichment. Missing/unavailable lists are not zero references; completed retrieval or resolution is not a guarantee of a complete bibliography. Network degrees are **local to displayed nodes**, never global citation counts. References identified outside the displayed network are distinguished from unresolved observations. Graphs show up to 60 connected nodes; JSON keeps all edges and evidence IDs.

Discovery follows **one explicit citation step**: references, citing papers, or both. Its limit applies to **new publications**, excluding seeds and publications already in the library. Existing publications are reused without another metadata request and labeled separately from new additions; paths retain parent papers, direction, providers and observation IDs. Candidates beyond the new-paper cap are recorded as deferred. Nothing expands recursively. Select discovered papers explicitly for another expansion. Source search snapshots are preserved.

Stop/resume preserves reference pages and metadata-resolution tasks. Repeat the command with the same output to resume, or use **Resume last analysis / discovery** during the current desktop session. Failed resolution tasks are retried; completed metadata is reused. Failed discovery lookups need `--refresh-references`. `--refresh-references` restarts reference/discovery checkpoints; `--refresh` also bypasses fresh HTTP cache. `--offline` uses cached responses. Progress bars separate retrieval, resolution and network building.

Service conventions follow the [Europe PMC REST documentation](https://europepmc.org/RestfulWebService), [Crossref bibliographic matching guidance](https://github.com/CrossRef/rest-api-doc/blob/master/api_tips.md), and [Crossref request limits](https://community.crossref.org/t/refining-rest-api-limits-for-improved-stability-and-reliability/16137). Requests remain sequential, cached and retryable; Crossref list queries are throttled separately and advertised rate-limit headers are honored. Bibliographic candidates are suggestions, not identity confidence probabilities.

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
releases/0.4.0/             Latest distributable artifacts and checksums
README.md                   Installation and usage
DISTRIBUTION.md              Building and sharing releases
VALIDATION.md               Reproducible verification
LICENSE                     MIT code license
```

Private runtime data, real metric tables, downloaded files, local environments, and build intermediates are excluded by `.gitignore`. To prepare a fresh public snapshot, build a release and run `python -m scripts.prepare_github --release-dir dist/0.4.0`. The exporter uses an explicit allowlist and includes only the newest distribution supplied to it.
