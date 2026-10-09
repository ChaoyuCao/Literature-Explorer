"""Small real API checks, isolated from the user's research catalogue."""
import json
from pathlib import Path
from literature.collector import Collector
from literature.http import HTTP
from literature.reports import discover_metrics, enrich_report, write_report
from literature.sources import Sources
from literature.store import Store


def main():
    state = Path('.literature/practical-live')
    store = Store(state / 'catalog.sqlite3')
    http = HTTP(timeout=20, retries=1, cache_dir=state / 'cache')
    results = []
    try:
        sources = Sources(http)
        collector = Collector(store, sources, lambda value: print(value, flush=True))
        for provider in ('pubmed', 'europepmc', 'crossref'):
            result = collector.search(provider, 'ion channels', limit=5, filters={'date_from': '2023-01-01'})
            report = enrich_report(store.search_export(result['job_id']), discover_metrics(Path.cwd()))
            assert len(report['papers']) == 5 and all(p['title'] for p in report['papers'])
            assert [p['relevance_rank'] for p in report['papers']] == [1,2,3,4,5]
            write_report(report, f'outputs/validation/{provider}.json', f'outputs/validation/{provider}.html')
            results.append({'source': provider, 'search_id': result['job_id'], 'papers': len(report['papers'])})
        doi = next(p['doi'] for p in report['papers'] if p.get('doi'))
        looked, provider = sources.lookup(doi)
        assert looked['doi'] == doi
        print(json.dumps({'sources': results, 'doi_lookup': doi, 'http': http.stats}), flush=True)
    finally:
        http.close()
        store.close()


if __name__ == '__main__':
    main()
