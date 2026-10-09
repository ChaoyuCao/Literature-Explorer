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
python -m scripts.verify_release --dist dist/0.2.3
```

Verification checks artifact hashes and archive contents, installs the wheel into a clean environment outside the source tree, and exercises the extracted Windows portable CLI. Synthetic fixtures verify multi-source search, deduplication, provenance, journal CSV enrichment, reports, library/interchange operations, saved-search merging, and error status with retained results. Results are stored in the selected release's `verification.json`.

The public-export tool additionally validates its allowlist, excludes private files and real metric tables, checks text/archive contents for machine-specific paths, and writes checksums for the prepared snapshot. The Windows executables' embedded Python code paths are relative module paths.

## Limits

Database coverage, result caps, field syntax, missing abstracts/identifiers, and cursor expiry can affect results. Limited searches and overlap statistics describe retrieved records rather than an entire database. Citation counts are provider-specific. Author names are not automatically disambiguated. Journal IF values are supplied by the selected external CSV; the application does not independently verify or estimate them. Browser annotations update a catalogue only after explicit export/import.
