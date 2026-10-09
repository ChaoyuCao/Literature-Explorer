import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature.analysis import merge_searches
from literature.cli import main, parser, specification
from literature.collector import Collector
from literature.store import Store


class FixtureSources:
    def __init__(self, records):
        self.records, self.calls = records, []

    def pages(self, provider, query, limit, sort, filters=None):
        self.calls.append((provider, query, limit, sort, filters))
        rows = self.records[provider]
        if isinstance(rows, BaseException):
            raise rows
        if rows:
            yield {'records': rows[:limit] if limit else rows, 'total': len(rows), 'raw': {'fixture': provider}}
        else:
            yield {'records': [], 'total': 0, 'raw': {'fixture': provider}}

    def search_pubmed(self, query, limit, sort):
        return self.pages('pubmed', query, limit, sort)

    def search_epmc(self, query, limit, sort):
        return self.pages('europepmc', query, limit, sort)

    def search_crossref(self, query, limit, sort, checkpoint, filters):
        return self.pages('crossref', query, limit, sort, filters)


class MultiSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'catalog.sqlite3')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_searches(self, records, **kwargs):
        sources = FixtureSources(records)
        collector = Collector(self.store, sources)
        result = collector.search_many(list(records), 'TEST_CHANNEL', **kwargs)
        return merge_searches(self.store, collector.multi_search_ids), sources, result

    def test_identifier_merge_gap_filling_citations_and_snapshot_provenance(self):
        report, _, _ = self.run_searches({
            'pubmed': [{'pmid': '42', 'doi': '10.1/ABC', 'title': 'Original', 'abstract': '', 'journal': 'Journal A'}],
            'europepmc': [{'pmid': '42', 'doi': '10.1/abc', 'title': 'Alternative', 'abstract': 'Quantitative response', 'citation_count': 0, 'citation_source': 'Europe PMC'}],
            'crossref': [{'doi': 'https://doi.org/10.1/ABC', 'title': 'Crossref title', 'abstract': 'Other abstract', 'citation_count': 12, 'citation_source': 'Crossref'}]})
        self.assertEqual(report['merge']['duplicates_removed'], 2)
        self.assertEqual(len(report['papers']), 1)
        paper = report['papers'][0]
        self.assertEqual(paper['title'], 'Original')
        self.assertEqual(paper['abstract'], 'Quantitative response')
        self.assertEqual(paper['metadata_provenance']['abstract']['provider'], 'europepmc')
        self.assertEqual(paper['metadata_provenance']['title']['provider'], 'pubmed')
        self.assertEqual(paper['citation_count'], 0)
        self.assertEqual(paper['citation_source'], 'Europe PMC')
        self.assertEqual([c['count'] for c in paper['citation_counts']], [0, 12])
        self.assertEqual([r['record']['title'] for r in paper['source_records']], ['Original', 'Alternative', 'Crossref title'])
        self.assertEqual(paper['sources'], ['pubmed', 'europepmc', 'crossref'])
        self.assertIsNone(paper['relevance_rank'])
        self.assertEqual([m['rank'] for m in paper['search_memberships']], [1, 1, 1])
        self.assertEqual(self.store.search_export(1)['papers'][0]['abstract'], '')
        self.assertEqual(report['comparison']['shared_by_all'], 1)

    def test_bridge_reconciles_previous_search_ids(self):
        report, _, result = self.run_searches({'pubmed': [{'pmid': '42'}],
            'crossref': [{'doi': '10.1/a'}], 'europepmc': [{'doi': '10.1/a', 'pmid': '42', 'pmcid': 'PMC42'}]})
        self.assertEqual(len(report['papers']), 1)
        self.assertEqual(report['papers'][0]['pmcid'], 'PMC42')
        self.assertEqual(report['merge']['duplicates_removed'], 2)
        self.assertTrue(all(s['paper_ids'] == [report['papers'][0]['id']] for s in result['searches']))
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_same_title_different_identities_not_merged(self):
        report, _, _ = self.run_searches({'pubmed': [{'doi': '10.1/a', 'title': 'Same title'}],
            'crossref': [{'doi': '10.1/b', 'title': 'Same title'}]})
        self.assertEqual(report['merge']['unique_paper_count'], 2)
        self.assertEqual(report['merge']['duplicates_removed'], 0)

    def test_failure_keeps_successful_sources_and_empty_failed_job(self):
        report, sources, result = self.run_searches({'pubmed': [{'pmid': '42'}],
            'europepmc': RuntimeError('Provider unavailable'), 'crossref': [{'doi': '10.1/a'}]})
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(report['search']['status'], 'partial')
        self.assertEqual(report['comparison']['searches'][1]['error'], 'Provider unavailable')
        self.assertEqual(len(report['papers']), 2)
        self.assertEqual(len(sources.calls), 3)

    def test_limit_filters_and_duplicate_provider_selection(self):
        sources = FixtureSources({'pubmed': [{'pmid': '1'}, {'pmid': '2'}], 'crossref': [{'doi': '10.1/a'}, {'doi': '10.1/b'}]})
        collector = Collector(self.store, sources)
        collector.search_many(['pubmed', 'crossref', 'pubmed'], 'TEST_CHANNEL', limit=1, filters={'date_from': '2020-01-01'})
        report = merge_searches(self.store, collector.multi_search_ids)
        self.assertEqual(report['search']['status'], 'limited')
        self.assertEqual(report['search']['query'], 'TEST_CHANNEL')
        self.assertEqual(report['merge']['unique_paper_count'], 2)
        self.assertEqual([c[2] for c in sources.calls], [1, 1])
        self.assertIn('Date - Publication', sources.calls[0][1])
        self.assertEqual(sources.calls[1][1], 'TEST_CHANNEL')
        self.assertEqual(sources.calls[1][4], {'date_from': '2020-01-01', 'article_type': 'journal-article'})

    def test_prevalidation_no_jobs_for_invalid_shared_filters(self):
        collector = Collector(self.store, FixtureSources({}))
        with self.assertRaises(ValueError):
            collector.search_many(['pubmed', 'crossref'], 'TEST_CHANNEL', filters={'date_from': 'invalid'})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)

    def test_saved_multi_query_and_single_source_override(self):
        args = parser().parse_args(['search', '--sources', 'pubmed', 'europepmc', '--query', 'TEST_CHANNEL', '--limit', '3'])
        spec = specification(args, {'source': 'pubmed'}, self.store)
        self.store.save_query('multi', spec)
        saved = specification(parser().parse_args(['search', '--saved', 'multi']), {'source': 'pubmed'}, self.store)
        self.assertEqual(saved, spec)
        override = specification(parser().parse_args(['search', '--saved', 'multi', '--source', 'crossref']), {'source': 'pubmed'}, self.store)
        self.assertNotIn('sources', override)
        self.assertEqual(override['source'], 'crossref')

    def test_cli_writes_one_combined_json_html_and_merge_can_rebuild(self):
        output = self.root / 'merged.json'
        state = self.root / 'cli'
        sources = FixtureSources({'pubmed': [{'doi': '10.1/a', 'title': 'TEST_CHANNEL'}], 'europepmc': [{'doi': '10.1/a', 'abstract': 'Response'}]})
        with patch('literature.cli.Sources', return_value=sources), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state', str(state), 'search', '--sources', 'pubmed', 'europepmc', '--query', 'TEST_CHANNEL', '--no-journal-metrics', '--output', str(output)])
        self.assertEqual(code, 0)
        report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(report['statistics']['paper_count'], 1)
        self.assertEqual(report['papers'][0]['abstract'], 'Response')
        self.assertEqual(report['merge']['requested_sources'], ['pubmed', 'europepmc'])
        self.assertIn('Merged database searches', output.with_suffix('.html').read_text(encoding='utf-8'))
        rebuilt = self.root / 'rebuilt.json'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--state', str(state), 'merge', '--search-ids', '1', '2', '--no-journal-metrics', '--output', str(rebuilt)]), 0)
        self.assertEqual(json.loads(rebuilt.read_text(encoding='utf-8'))['merge']['duplicates_removed'], 1)

    def test_cli_partial_failure_nonzero_and_partial_report(self):
        output = self.root / 'partial.json'
        sources = FixtureSources({'pubmed': RuntimeError('Offline'), 'crossref': [{'doi': '10.1/a'}]})
        with patch('literature.cli.Sources', return_value=sources), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state', str(self.root / 'cli'), 'search', '--sources', 'pubmed', 'crossref', '--query', 'TEST_CHANNEL', '--no-journal-metrics', '--output', str(output)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['search']['status'], 'partial')
        self.assertTrue(output.with_suffix('.html').exists())

    def test_cli_interrupted_multi_search_preserves_merged_results(self):
        output = self.root / 'interrupted.json'
        sources = FixtureSources({'pubmed': [{'pmid': '1'}], 'europepmc': KeyboardInterrupt(), 'crossref': []})
        with patch('literature.cli.Sources', return_value=sources), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state', str(self.root / 'cli'), 'search', '--sources', 'pubmed', 'europepmc', 'crossref', '--query', 'TEST_CHANNEL', '--no-journal-metrics', '--output', str(output)])
        self.assertEqual(code, 130)
        report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(report['search']['status'], 'interrupted')
        self.assertEqual(report['merge']['unique_paper_count'], 1)
        self.assertEqual(report['merge']['requested_sources'], ['pubmed', 'europepmc', 'crossref'])
        self.assertEqual([c[0] for c in sources.calls], ['pubmed', 'europepmc'])


if __name__ == '__main__':
    unittest.main()
