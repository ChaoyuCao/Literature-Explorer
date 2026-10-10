# Validation

Tests use synthetic bibliographic records and temporary catalogues. The public tree contains no personal search results, previous report statistics, or machine-specific validation logs.

## Automated tests

```console
python -m unittest discover -s tests -v
```

The suite covers database response parsing, stable search ranks, exact-identifier deduplication, compatible metadata merging, preserved source provenance, immutable search snapshots, library annotations, bibliography interchange, filters, configuration, caching, retries, interruption/resumption, journal-metric validation, and JSON/HTML reports. Fake network responses isolate unit tests from live database changes.

## Desktop checks

```console
python -m scripts.smoke_desktop
```

The desktop checks require a graphical session and Tk. They verify an empty initial query, independent database selections displayed as dots, background searches, duplicate removal, JSON/HTML opening, CSV loading/application, library/history operations, Stop, and Resume. Synthetic responses are cached in a temporary workspace, which is removed after the check. An optional `--engine PATH` exercises the frozen CLI used by the desktop app.

## Browser checks

Install optional `playwright` and provide Microsoft Edge, then run:

```console
python -m scripts.smoke_report_browser
```

The checks create temporary synthetic reports and verify IF/relevance sorting, filters, abstracts, annotations, JSON/CSV/SVG exports, pagination, responsive layout, and browsing without network requests. They do not depend on a pre-existing research output directory.

## Distribution checks

```console
python -m scripts.verify_release --dist dist/0.4.0
```

Verification checks artifact hashes and archive contents, installs the wheel into a clean environment outside the source tree, and exercises the extracted Windows portable CLI. Synthetic fixtures verify multi-source search, deduplication, provenance, journal CSV enrichment, reports, library/interchange operations, saved-search merging, and error status with retained results. Results are stored in the selected release's `verification.json`.

The public-export tool additionally validates its allowlist, excludes private files and real metric tables, checks text/archive contents for machine-specific paths, and writes checksums for the prepared snapshot. The Windows executables' embedded Python code paths are relative module paths.

## Limits

The reference/citation tests additionally cover optional defaults, nested PubMed lists, DOI normalization, identifier conflicts and bridges, paginated checkpoints, provider failures, interruption/resumption, missing versus reported-empty lists, output collisions, separate reference JSON, exact in-corpus edges, unique discovery limits, forward/backward discovery, and annual term-count denominators. Browser checks exercise network selection, graph filtering, yearly trends, co-occurrence, and filtered JSON exports. Desktop and package checks exercise references, network generation, and discovery with synthetic offline fixtures. Progress checks cover reference outcome counts, resumed checkpoints, network edge counts, empty selections, and the desktop bars and labels through the real background engine.

Database coverage, result caps, field syntax, missing abstracts/identifiers, and cursor expiry can affect results. Limited searches and overlap statistics describe retrieved records rather than an entire database. Citation counts are provider-specific. Author names are not automatically disambiguated. Journal IF values are supplied by the selected external CSV; the application does not independently verify or estimate them. Browser annotations update a catalogue only after explicit export/import.

Reference metadata may be absent or incomplete. Citation networks include only exact identifier links between collected papers. Discovery is one hop, and its unique-paper cap excludes seeds; all accessible seed reference metadata may be fetched before this cap is applied. Term trends describe the retrieved corpus and do not establish changes in the entire research field. Reference lists are excluded from HTML and paper JSON, and saved in dedicated JSON files instead.

The publication-identity tests cover PubMed ArticleId aliases, identifier normalization, cross-provider bridges, repeated observations, shared references across seeds, conflicting identity evidence, metadata-service failures, resume, weak/ambiguous citation-only candidates, library reuse, selected-report resolution, missing metadata, self-links and existing-versus-new discovery. `scripts/smoke_reference_browser.py` checks reference filters, safe evidence rendering, JSON exports and offline/mobile behavior.

Audit an existing four-file bundle without modifying it with `python -m scripts.audit_references --report REPORT.json --references REPORT.references.json --network REPORT.network.json --html REPORT.html --output-dir NEW_DIRECTORY`. The audit compares every raw provider observation, input hashes, legacy self-links and new counts. Offline counts describe supported identifier groups, not a presumed true unique-reference total. Live metadata can consolidate additional groups.
