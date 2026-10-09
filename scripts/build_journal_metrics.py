"""Extract the specific public JCR PDF layout into a source-labelled CSV.

Optional preparation dependency: pymupdf. This is not part of the search runtime.
The caller supplies the release and metric years; never infer them from a filename.
"""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from literature.reports import issn_key, journal_key, load_metrics


FIELDS = ['journal', 'issn', 'impact_factor', 'year', 'release_year', 'source',
          'source_kind', 'retrieved_at', 'source_page', 'status', 'notes', 'aliases']


def extract(pdf, source, metric_year, release_year):
    import pymupdf
    entries = []
    with pymupdf.open(pdf) as document:
        for page_number, page in enumerate(document, 1):
            if f'JIF {release_year}' not in page.get_text():
                raise ValueError(f'Unexpected column headings on page {page_number}.')
            rows = {}
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        for char in span['chars']:
                            x, y = char['origin']
                            column = next((i for i, end in enumerate([380, 445, 510, 575, 640, 705, 771]) if x < end), None)
                            if column is not None:
                                cells = rows.setdefault(round(y, 1), [[] for _ in range(7)])
                                cells[column].append((x, char['c']))
            for cells in rows.values():
                text = [''.join(char for _, char in sorted(cell)).strip() for cell in cells]
                value = text[4]
                if not re.fullmatch(r'\d+\.\d+|N/A', value):
                    continue
                name, identifier = text[:2]
                if not name:
                    raise ValueError(f'Missing journal title on page {page_number}.')
                if identifier in {'N/A', '-', ''}:
                    identifier = ''
                elif not re.fullmatch(r'\d{4}-[\dX]{4}', identifier):
                    raise ValueError(f'Unrecognized eISSN {identifier!r} on page {page_number}.')
                available = value != 'N/A'
                entries.append(dict(journal=name, issn=identifier, impact_factor=value if available else '',
                    year=metric_year, release_year=release_year, source=f'{source}#page={page_number}',
                    source_kind='public_jcr_mirror', retrieved_at=date.today().isoformat(), source_page=page_number,
                    status='available' if available else 'unavailable', aliases='',
                    notes=f'Publicly hosted secondary JCR copy; not independently publisher-confirmed. JIF {release_year} is interpreted as release year, corresponding to metric year {metric_year}. Titles follow the PDF and may be truncated; prefer ISSN matching.' if available else 'Current JIF marked N/A in the public table; historical values are not substituted.'))
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--metric-year', type=int, required=True)
    parser.add_argument('--release-year', type=int, required=True)
    parser.add_argument('--release-source', required=True)
    parser.add_argument('--overrides', help='Existing curated CSV; preserves values and provenance for overlapping journals')
    parser.add_argument('--output', default='journal-metrics.csv')
    args = parser.parse_args()
    entries = extract(args.pdf, args.source, args.metric_year, args.release_year)
    extracted_count = len(entries)
    overrides_used = 0
    if args.overrides:
        by_name = {journal_key(row['journal']): row for row in entries}
        by_issn = {issn_key(row['issn']): row for row in entries if row['issn']}
        with Path(args.overrides).open(encoding='utf-8-sig', newline='') as handle:
            for override in csv.DictReader(handle):
                if int(override['year']) != args.metric_year:
                    raise ValueError('Curated overrides must use the selected metric year.')
                identifiers = [x for x in re.split(r'[;,|\s]+', override.get('issn') or '') if x]
                row = next((by_issn[issn_key(i)] for i in identifiers if issn_key(i) in by_issn), None)
                row = row or by_name.get(journal_key(override['journal']))
                if row is None:
                    entries.append({field: override.get(field, '') for field in FIELDS})
                else:
                    old_name, old_issn = row['journal'], row['issn']
                    row.update({field: override.get(field, '') for field in FIELDS})
                    row['issn'] = ';'.join(sorted(set([old_issn, *identifiers]) - {''}))
                    if journal_key(old_name) != journal_key(row['journal']):
                        row['aliases'] = '|'.join(filter(None, [old_name, override.get('aliases', '')]))
                overrides_used += 1
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.pending.csv')
    try:
        with temporary.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(sorted(entries, key=lambda row: row['journal'].casefold()))
        load_metrics(temporary)  # Reject conflicts, invalid years and invalid numbers before replacing the table.
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    summary = dict(metric='Journal Impact Factor', metric_year=args.metric_year, release_year=args.release_year,
        source=args.source, release_source=args.release_source, retrieved_at=date.today().isoformat(),
        pdf_sha256=hashlib.sha256(Path(args.pdf).read_bytes()).hexdigest(),
        csv_sha256=hashlib.sha256(output.read_bytes()).hexdigest(), extracted_rows=extracted_count,
        journal_count=len(entries), available_count=sum(row['status'] == 'available' for row in entries),
        source_kind_counts=dict(Counter(row['source_kind'] for row in entries)), curated_overrides=overrides_used,
        caveat='Secondary public copy; coverage is not guaranteed to equal official JCR. Values are not calculated from article citations. Publisher-labelled overrides retain their existing evidence and retrieval dates.')
    output.with_name(output.stem + '-provenance.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
