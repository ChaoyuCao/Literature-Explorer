"""Small live checks of both metadata APIs; no article files are fetched."""
import json
from pathlib import Path
from literature.collector import Collector
from literature.http import HTTP
from literature.reports import discover_metrics, enrich_report, write_report
from literature.sources import Sources
from literature.store import Store


def main():
    store = Store(Path('.literature/smoke-search/catalog.sqlite3'))
    http = HTTP(timeout=25, retries=1, cache_dir='.literature/smoke-search/cache')
    try:
        collector = Collector(store, Sources(http))
        for source in ('pubmed', 'europepmc'):
            result = collector.search(source, 'ion channels', 20)
            report = enrich_report(store.search_export(result['job_id']), discover_metrics(Path.cwd()))
            assert report['papers'] and all(p.get('title') for p in report['papers'])
            assert [p['relevance_rank'] for p in report['papers']] == list(range(1, len(report['papers']) + 1))
            output = write_report(report, f'outputs/example-{source}.json', f'outputs/example-{source}.html')
            print(json.dumps({'source': source, 'status': result['status'], **output,
                              'abstracts': report['statistics']['abstract_count'],
                              'journals': report['statistics']['journal_count']}, ensure_ascii=False), flush=True)
    finally:
        http.close()
        store.close()


if __name__ == '__main__':
    main()
