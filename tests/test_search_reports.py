import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature.cli import main, parser
from literature.collector import Collector
from literature.http import APIError
from literature.reports import enrich_report, load_metrics, write_report
from literature.sources import Sources, epmc_record
from literature.store import Store
from test_literature import FakeHTTP, article, page


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'catalog.sqlite3')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def search(self, rows, sort='relevance', total=None):
        return Collector(self.store, Sources(FakeHTTP([page(rows, total or len(rows))]))).search('europepmc', 'TEST_CHANNEL', len(rows), sort)

    def metrics(self, rows):
        path = self.root / 'metrics.csv'
        path.write_text('journal,issn,impact_factor,year,source\n' + rows, encoding='utf-8')
        return path

    def test_snapshot_preserves_metadata_and_query_specific_rank(self):
        first = self.search([article('2'), article('1')])
        second = self.search([{**article('1'), 'title': 'Updated title'}, article('2')])
        old = self.store.search_export(first['job_id'])
        self.assertEqual([p['pmid'] for p in old['papers']], ['2', '1'])
        self.assertEqual(old['papers'][1]['title'], 'Paper 1')
        self.assertEqual([p['relevance_rank'] for p in old['papers']], [1, 2])
        self.assertEqual(self.store.search_export(second['job_id'])['papers'][0]['title'], 'Updated title')

    def test_date_search_has_no_fabricated_relevance(self):
        self.search([article('1')], 'date')
        paper = self.store.search_export()['papers'][0]
        self.assertEqual(paper['search_rank'], 1)
        self.assertIsNone(paper['relevance_rank'])
        self.assertIsNone(paper['relevance_score'])

    def test_unknown_old_order_is_not_labeled_relevance(self):
        ident = self.store.upsert({'pmid': '42'}, 'pubmed')
        job = self.store.job('search', 'pubmed', 'old')
        self.store.discover(ident, job, 'search')
        self.assertIsNone(self.store.search_export()['papers'][0]['relevance_rank'])

    def test_metrics_match_issn_and_use_one_year(self):
        self.search([{**article('1'), 'journalTitle': 'Abbreviated', 'journalInfo': {'journal': {'issn': '1234-5678'}}},
                     {**article('2'), 'journalTitle': 'Unknown'}])
        path = self.metrics('Full journal,1234-5678,4.5,2024,Fixture source\nFull journal,1234-5678,6.0,2025,Fixture source\n')
        report = enrich_report(self.store.search_export(), path)
        self.assertEqual(report['journal_metrics']['year'], 2025)
        self.assertEqual(report['papers'][0]['journal_metric']['impact_factor'], 6.0)
        self.assertEqual(report['papers'][0]['journal_group'], 'Full journal')
        self.assertIsNone(report['papers'][1]['journal_metric'])
        self.assertEqual(report['journal_metrics']['matched_papers'], 1)
        self.assertEqual(enrich_report(self.store.search_export(), path, 2024)['papers'][0]['journal_metric']['impact_factor'], 4.5)

    def test_missing_metrics_and_missing_abstracts_remain_visible(self):
        self.search([article('1'), {**article('2'), 'abstractText': 'Some findings', 'doi': '10.1/test'}])
        report = enrich_report(self.store.search_export())
        self.assertEqual(report['statistics']['abstract_count'], 1)
        self.assertEqual(report['statistics']['doi_count'], 1)
        self.assertEqual(report['journal_metrics']['matched_papers'], 0)

    def test_repeated_identifiers_do_not_inflate_journal_counts(self):
        self.search([{**article('1'), 'journalTitle': 'Journal'}, {**article('1'), 'journalTitle': 'Journal'}])
        report = enrich_report(self.store.search_export())
        self.assertEqual(report['statistics']['paper_count'], 1)
        self.assertEqual(report['statistics']['journals'][0]['paper_count'], 1)

    def test_metrics_reject_nan_negative_missing_provenance_and_conflicts(self):
        for rows in ['J,,NaN,2024,Source\n', 'J,,-1,2024,Source\n', 'J,,2,2024,\n',
                     'J,1234-5678,2,2024,Source\nJ,1234-5678,3,2024,Source\n']:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                load_metrics(self.metrics(rows))

    def test_zero_metric_is_distinct_from_missing_and_name_match(self):
        self.search([{**article('1'), 'journalTitle': 'The Journal!'}])
        report = enrich_report(self.store.search_export(), self.metrics('The Journal,,0,2024,Fixture\n'))
        self.assertEqual(report['papers'][0]['journal_metric']['impact_factor'], 0)

    def test_metric_provenance_and_explicit_unavailability_survive_export(self):
        self.search([{**article('1'), 'journalTitle': 'Journal A'},
                     {**article('2'), 'journalTitle': 'Journal B'}])
        path = self.root / 'provenance.csv'
        path.write_text('journal,impact_factor,year,source,source_kind,retrieved_at,status,notes\n'
                        'Journal A,5.4,2025,https://example.org/a,publisher,2025-01-01,available,Confirmed\n'
                        'Journal B,,2025,https://example.org/b,publisher,2025-01-01,unavailable,No current JIF\n')
        report = enrich_report(self.store.search_export(), path)
        first, second = report['papers']
        self.assertEqual(first['journal_metric']['source_kind'], 'publisher')
        self.assertEqual(first['journal_metric']['retrieved_at'], '2025-01-01')
        self.assertIsNone(second['journal_metric'])
        self.assertEqual(second['journal_metric_status'], 'unavailable')
        self.assertEqual(second['journal_metric_reference']['year'], 2025)
        self.assertEqual(second['journal_metric_note'], 'No current JIF')
        self.assertEqual(report['journal_metrics']['matched_journals'], 1)
        self.assertEqual(report['journal_metrics']['source_kind_counts'], {'publisher': 1})
        write_report(report, self.root / 'out.json')
        saved = json.loads((self.root / 'out.json').read_text())
        self.assertIsNone(saved['statistics']['journals'][1]['metric'])
        # Re-enrichment must not leave a previously assigned value or source behind.
        enrich_report(report)
        self.assertIsNone(first['journal_metric_reference'])

    def test_cli_metric_defaults_allow_override_or_disable(self):
        default = parser().parse_args(['search', '--query', 'TEST_CHANNEL'])
        # The source release omits research metric data; a default is optional.
        if default.journal_metrics is not None:
            self.assertTrue(Path(default.journal_metrics).is_file())
        self.assertEqual(parser().parse_args(['search', '--query', 'x', '--journal-metrics', 'other.csv']).journal_metrics, 'other.csv')
        self.assertIsNone(parser().parse_args(['search', '--query', 'x', '--no-journal-metrics']).journal_metrics)

    def test_report_is_offline_and_embedded_metadata_cannot_close_script(self):
        self.search([article('1')])
        report = enrich_report(self.store.search_export())
        report['papers'][0]['title'] = '</script><script>alert(1)</script> __PRACTICAL_SCRIPT__'
        result = write_report(report, self.root / 'search.json', self.root / 'search.html')
        html = Path(result['dashboard']).read_text(encoding='utf-8')
        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertIn('\\u003c/script', html)
        self.assertNotIn('<script src=', html)
        self.assertEqual(json.loads(Path(result['json']).read_text())['papers'][0]['title'], '</script><script>alert(1)</script> __PRACTICAL_SCRIPT__')

    def test_partial_cli_search_writes_json_and_html(self):
        output = self.root / 'partial.json'
        fake = FakeHTTP([page([article('1')], 2, 'next'), APIError('Network unavailable')])
        with patch('literature.cli.Sources', return_value=Sources(fake)), contextlib.redirect_stderr(io.StringIO()):
            status = main(['--state', str(self.root / 'partial-state'), 'search', '--source', 'europepmc',
                           '--query', 'TEST_CHANNEL', '--output', str(output)])
        self.assertEqual(status, 1)
        report = json.loads(output.read_text())
        self.assertEqual(report['search']['status'], 'failed')
        self.assertEqual(len(report['papers']), 1)
        self.assertTrue(output.with_suffix('.html').exists())

    def test_cli_default_outputs_and_download_commands_removed(self):
        fake = FakeHTTP([page([article('1')], 10)])
        output = self.root / 'result.json'
        with patch('literature.cli.Sources', return_value=Sources(fake)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--state', str(self.root / 'cli-state'), 'search', '--source', 'europepmc',
                                   '--query', 'TEST_CHANNEL', '--limit', '1', '--output', str(output)]), 0)
        self.assertEqual(json.loads(output.read_text())['search']['status'], 'limited')
        self.assertTrue(output.with_suffix('.html').exists())
        for command in ['download', 'download-batch', 'institution', 'locate']:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args([command])

    def test_old_catalogue_migrates_without_losing_download_history(self):
        path = self.root / 'old.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript("CREATE TABLE jobs(id INTEGER PRIMARY KEY,kind TEXT,source TEXT,query TEXT,started TEXT,finished TEXT,status TEXT,total INTEGER,retrieved INTEGER,error TEXT); CREATE TABLE downloads(id INTEGER, note TEXT); INSERT INTO downloads VALUES(1,'preserved');")
        db.close()
        old = Store(path)
        try:
            self.assertEqual(old.db.execute('SELECT note FROM downloads').fetchone()[0], 'preserved')
            self.assertNotIn('downloads', old.export())
        finally:
            old.close()

    def test_new_catalogue_has_no_downloader_tables(self):
        names = {r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('downloads', names)
        self.assertNotIn('institutions', names)

    def test_empty_search_still_produces_valid_statistics(self):
        Collector(self.store, Sources(FakeHTTP([page([], 0)]))).search('europepmc', 'no hits')
        report = enrich_report(self.store.search_export())
        self.assertEqual(report['statistics']['paper_count'], 0)
        self.assertEqual(report['statistics']['journals'], [])


class SourceRankingTests(unittest.TestCase):
    def test_pubmed_fetch_order_cannot_change_database_relevance(self):
        class HTTP(FakeHTTP):
            def get(self, url, params):
                self.calls.append((url, params))
                return b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>One</ArticleTitle></Article></MedlineCitation></PubmedArticle><PubmedArticle><MedlineCitation><PMID>2</PMID><Article><ArticleTitle>Two</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'
        http = HTTP([{'esearchresult': {'count': '2', 'idlist': ['2', '1']}}])
        pages = list(Sources(http).search_pubmed('query'))
        self.assertEqual(http.calls[0][1]['sort'], 'relevance')
        self.assertEqual([r['pmid'] for r in pages[1]['records']], ['2', '1'])

    def test_epmc_keeps_extra_metadata_without_fetching_full_text(self):
        record = epmc_record({**article('1'), 'firstPublicationDate': '2025-01-02', 'citedByCount': 8,
                              'keywordList': {'keyword': ['Mechanics']},
                              'journalInfo': {'journal': {'title': 'Journal', 'issn': '1234-5678', 'essn': '2345-6789'}}})
        self.assertEqual(record['citation_count'], 8)
        self.assertEqual(record['issns'], ['1234-5678', '2345-6789'])
        self.assertEqual(record['publication_date'], '2025-01-02')


if __name__ == '__main__':
    unittest.main()
