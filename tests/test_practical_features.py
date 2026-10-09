import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from literature.analysis import compare_searches
from literature.cli import main
from literature.collector import Collector
from literature.config import load_config, public_config
from literature.formats import import_records, normalize_record, read_records, write_records
from literature.http import APIError, HTTP
from literature.reports import enrich_report
from literature.sources import Sources, search_query
from literature.store import Store
from test_literature import FakeHTTP, article, page


class PracticalFeatures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'catalog.sqlite3')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_annotations_collections_filters_and_reopen(self):
        ident = self.store.upsert({'doi': '10.1/a', 'title': 'Mechanics', 'authors': 'Jane Smith', 'journal': 'Journal A', 'year': '2024', 'publication_types': ['Review']}, 'fixture')
        self.store.annotate(ident, note='Check Figure 2', favorite=True, status='included', tags=['mechanics'])
        self.store.collection('Reading', add=[ident])
        result = self.store.list_papers(collection='Reading', tag='mechanics', favorite=True, status='included', author='Smith', year_from=2024, article_type='review')
        self.assertEqual([p['id'] for p in result], [ident])
        self.assertEqual(self.store.list_papers(year_to=2023), [])
        reopened = Store(self.root / 'catalog.sqlite3')
        try:
            self.assertEqual(reopened.library_id, self.store.library_id)
            self.assertEqual(reopened.annotation(ident)['note'], 'Check Figure 2')
        finally:
            reopened.close()
        self.store.collection('Reading', rename='Reviewed')
        self.assertEqual(self.store.annotation(ident)['collections'], ['Reviewed'])
        self.store.collection('Reviewed', delete=True)
        self.assertEqual(len(self.store.list_papers()), 1)

    def test_merge_preserves_notes_tags_collections_and_review_conflict(self):
        left = self.store.upsert({'pmid': '42'}, 'pubmed')
        right = self.store.upsert({'doi': '10.1/a'}, 'crossref')
        self.store.annotate(left, note='First', status='included', tags=['A'])
        self.store.annotate(right, note='Second', status='excluded', favorite=True, tags=['A', 'B'])
        self.store.collection('Reading', add=[left, right])
        self.store.upsert({'doi': '10.1/a', 'pmid': '42'}, 'bridge')
        annotation = self.store.annotation(left)
        self.assertEqual(annotation['status'], 'uncertain')
        self.assertEqual(annotation['tags'], ['A', 'B'])
        self.assertTrue(annotation['favorite'])
        self.assertIn('Second', annotation['note'])
        self.assertEqual(self.store.collection('Reading')['paper_ids'], [left])
        self.assertEqual(self.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_import_is_atomic_on_conflicting_identifiers_and_annotations(self):
        self.store.upsert({'doi': '10.1/a', 'pmid': '1'}, 'fixture')
        before = len(self.store.list_papers())
        with self.assertRaises(ValueError):
            import_records(self.store, [{'doi': '10.1/new', 'title': 'New'}, {'doi': '10.1/a', 'pmid': '2'}], collection='Rollback')
        self.assertEqual(len(self.store.list_papers()), before)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM collections WHERE name='Rollback'").fetchone()[0], 0)

    def test_import_bridge_returns_canonical_ids(self):
        self.store.upsert({'pmid': '42'}, 'fixture')
        self.store.upsert({'doi': '10.1/a'}, 'fixture')
        result = import_records(self.store, [{'doi': '10.1/a'}, {'pmid': '42', 'doi': '10.1/a'}], collection='Union')
        self.assertEqual(result['paper_ids'], [1])
        self.assertEqual(self.store.collection('Union')['paper_ids'], [1])

    def test_browser_annotation_import_checks_catalogue_and_stale_edits(self):
        ident = self.store.upsert({'title': 'A'}, 'fixture')
        self.store.annotate(ident, tags=['old', 'keep'])
        base = self.store.annotation(ident)['updated']
        packet = {'library_id': self.store.library_id, 'annotations': [{'id': ident, 'base_updated': base, 'library': {'note': 'Browser', 'favorite': True, 'status': 'included', 'tags': ['keep', 'new']}}]}
        self.store.import_annotations(packet)
        self.assertEqual(self.store.annotation(ident)['tags'], ['keep', 'new'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.store.import_annotations(packet)
        with self.assertRaises(ValueError):
            self.store.import_annotations({**packet, 'library_id': 'another-library'}, overwrite=True)

    def test_json_csv_ris_bibtex_roundtrip(self):
        paper = normalize_record({'title': 'Force {TEST_CHANNEL} & flow', 'doi': 'https://doi.org/10.1/ABC', 'abstract': 'Line one\nLine two',
                                  'journal': 'Journal', 'year': '2025', 'author_list': ['Smith, Jane', 'Research and Development Team'],
                                  'keywords': ['mechanics', 'Ca2+'], 'issns': ['1234-5678'], 'pmid': '42', 'pages': '10-20'})
        for format, suffix in [('json', 'json'), ('csv', 'csv'), ('ris', 'ris'), ('bibtex', 'bib')]:
            with self.subTest(format=format):
                path = self.root / ('papers.' + suffix)
                write_records([paper], path, format)
                loaded = read_records(path)[0]
                for key in ('title', 'doi', 'abstract', 'journal', 'year', 'author_list', 'keywords', 'pmid'):
                    self.assertEqual(loaded[key], paper[key], (format, key))

    def test_bibtex_nested_values_macros_comments_and_malformed_input(self):
        path = self.root / 'external.bib'
        path.write_text('% comment\n@string{jn = "Journal"}\n@article(key, title={A {TEST_CHANNEL} study},journal=jn # " A",year=2024,author={Smith, Jane and {Team and Co}})')
        row = read_records(path)[0]
        self.assertEqual(row['journal'], 'Journal A')
        self.assertEqual(row['author_list'], ['Smith, Jane', 'Team and Co'])
        path.write_text('@article{x,title={unclosed}')
        with self.assertRaises(ValueError):
            read_records(path)

    def test_book_exports_preserve_type_and_publisher(self):
        paper = normalize_record({'title': 'Book', 'publication_types': ['book'], 'publisher': 'Publisher'})
        for format, suffix, expected in [('bibtex', 'bib', 'book'), ('ris', 'ris', 'BOOK')]:
            path = self.root / ('book.' + suffix)
            write_records([paper], path, format)
            row = read_records(path)[0]
            self.assertEqual(row['publication_types'], [expected])
            self.assertEqual(row['publisher'], 'Publisher')

    def test_ris_multiline_unicode_and_missing_terminator(self):
        path = self.root / 'external.ris'
        path.write_text('TY  - JOUR\nTI  - 蛋白质\nAB  - First\n      Second\nPY  - 2024/01/02\nER  - \n', encoding='utf-8')
        row = read_records(path)[0]
        self.assertEqual(row['title'], '蛋白质')
        self.assertEqual(row['abstract'], 'First\nSecond')
        path.write_text('TY  - JOUR\nTI  - Incomplete\n')
        with self.assertRaises(ValueError):
            read_records(path)

    def test_source_filters_validate_dates_and_preserve_original_query(self):
        filters = {'author': 'Smith', 'journal': 'Nature', 'date_from': '2024-01-01', 'date_to': '2025-12-31', 'article_type': 'Review'}
        self.assertIn('"Smith"[Author]', search_query('pubmed', 'A OR B', filters))
        self.assertIn('FIRST_PDATE:[2024-01-01 TO 2025-12-31]', search_query('europepmc', 'A', filters))
        with self.assertRaises(ValueError):
            search_query('pubmed', 'A', {'date_from': '2025-02-31'})
        with self.assertRaises(ValueError):
            search_query('pubmed', 'A', {'date_from': '2025-01-01', 'date_to': '2024-01-01'})
        http = FakeHTTP([page([], 0)])
        result = Collector(self.store, Sources(http)).search('europepmc', 'A', filters=filters)
        parameters = json.loads(self.store.search_job(result['job_id'])['parameters'])
        self.assertEqual(parameters['original_query'], 'A')
        self.assertEqual(parameters['filters'], filters)

    def test_search_resumes_from_last_committed_cursor_without_duplicates(self):
        collector = Collector(self.store, Sources(FakeHTTP([page([article('1')], 2, 'next'), APIError('Disconnected')])))
        with self.assertRaises(APIError):
            collector.search('europepmc', 'TEST_CHANNEL')
        job_id = collector.last_job_id
        http = FakeHTTP([page([article('2')], 2)])
        result = Collector(self.store, Sources(http)).resume(job_id)
        self.assertEqual(http.calls[0][1]['cursorMark'], 'next')
        self.assertEqual(result['job_id'], job_id)
        self.assertEqual(result['status'], 'complete')
        report = self.store.search_export(job_id)
        self.assertEqual([p['pmid'] for p in report['papers']], ['1', '2'])
        self.assertEqual([p['relevance_rank'] for p in report['papers']], [1, 2])
        self.assertEqual(report['search']['attempts'], 2)

    def test_pubmed_resume_preserves_identifier_order_without_new_search_request(self):
        class PubmedHTTP(FakeHTTP):
            def get(self, url, params):
                self.calls.append((url, params))
                value = next(self.pages)
                if isinstance(value, Exception):
                    raise value
                return value
        def xml(ids):
            return ('<PubmedArticleSet>' + ''.join('<PubmedArticle><MedlineCitation><PMID>'+ident+'</PMID><Article><ArticleTitle>Paper '+ident+'</ArticleTitle></Article></MedlineCitation></PubmedArticle>' for ident in ids) + '</PubmedArticleSet>').encode()
        ids = [str(i) for i in range(1,102)]
        original = PubmedHTTP([{'esearchresult': {'count': '101', 'idlist': ids}}, xml(ids[:100]), APIError('Disconnected')])
        collector = Collector(self.store, Sources(original))
        with self.assertRaises(APIError):
            collector.search('pubmed', 'TEST_CHANNEL')
        resumed = PubmedHTTP([xml(ids[100:])])
        Collector(self.store, Sources(resumed)).resume(collector.last_job_id)
        self.assertEqual(len(resumed.calls), 1)
        self.assertTrue(resumed.calls[0][0].endswith('/efetch.fcgi'))
        self.assertEqual(resumed.calls[0][1]['id'], '101')
        self.assertEqual([p['pmid'] for p in self.store.search_export()['papers']], ids)

    def test_failed_page_rolls_back_records_and_checkpoint_together(self):
        self.store.upsert({'pmid': '2', 'doi': '10.1/a'}, 'existing')
        rows = [article('1'), article('2', '10.1/conflict')]
        collector = Collector(self.store, Sources(FakeHTTP([page(rows, 2)])))
        with self.assertRaises(ValueError):
            collector.search('europepmc', 'A')
        report = self.store.search_export()
        self.assertEqual(report['papers'], [])
        self.assertEqual(report['search']['retrieved'], 0)
        self.assertIsNone(report['search']['checkpoint'])
        self.assertEqual(len(self.store.list_papers()), 1)

    def test_snapshot_recovers_authors_from_original_raw_response_without_rewriting(self):
        record = {'pmid': '42', 'title': 'Original', 'authors': 'Smith J'}
        ident = self.store.upsert(record, 'pubmed')
        job = self.store.job('search', 'pubmed', 'TEST_CHANNEL', 'relevance')
        xml = '<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>42</PMID><Article><ArticleTitle>Original</ArticleTitle><AuthorList><Author><LastName>Smith</LastName><Initials>J</Initials></Author></AuthorList></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'
        self.store.page(job, xml)
        self.store.discover(ident, job, 'search', rank=1)
        paper = self.store.search_export(job)['papers'][0]
        self.assertEqual(paper['author_list'], ['Smith J'])
        self.assertEqual(json.loads(self.store.db.execute('SELECT record FROM observations').fetchone()[0]), record)

    def test_crossref_metadata_search_filters_and_doi_lookup(self):
        raw = {'DOI': '10.1/A', 'title': ['A title'], 'container-title': ['Journal'], 'ISSN': ['1234-5678'],
               'author': [{'given': 'Jane', 'family': 'Smith'}], 'published': {'date-parts': [[2025, 3, 1]]}, 'is-referenced-by-count': 0}
        http = FakeHTTP([{'message': {'items': [raw], 'total-results': 100, 'next-cursor': 'next'}}])
        result = Collector(self.store, Sources(http)).search('crossref', 'mechanics', 1, filters={'date_from': '2025-01-01', 'article_type': 'journal-article'})
        self.assertEqual(result['status'], 'limited')
        self.assertIn('from-pub-date:2025-01-01', http.calls[0][1]['filter'])
        paper = self.store.search_export()['papers'][0]
        self.assertEqual(paper['citation_count'], 0)
        self.assertEqual(paper['author_list'], ['Jane Smith'])
        record, provider = Sources(FakeHTTP([{'message': raw}])).lookup('https://doi.org/10.1/A')
        self.assertEqual((record['doi'], provider), ('10.1/a', 'crossref'))
        with self.assertRaisesRegex(APIError, 'different DOI'):
            Sources(FakeHTTP([{'message': raw}])).lookup('10.1/other')

    def test_pmcid_lookup_uses_documented_field_and_checks_identity(self):
        http = FakeHTTP([page([{**article('42'), 'pmcid': 'PMC123'}], 1)])
        record, provider = Sources(http).lookup('PMC123')
        self.assertEqual(http.calls[0][1]['query'], 'PMCID:PMC123')
        self.assertEqual(record['pmcid'], 'PMC123')
        self.assertEqual(provider, 'europepmc')

    def test_crossref_repeated_page_fails_before_committing_duplicate_records(self):
        raw = {'DOI': '10.1/a', 'title': ['A']}
        result = {'message': {'items': [raw], 'total-results': 3, 'next-cursor': 'same'}}
        collector = Collector(self.store, Sources(FakeHTTP([result, result])))
        with self.assertRaises(APIError):
            collector.search('crossref', 'A')
        self.assertEqual(len(self.store.search_export()['papers']), 1)
        self.assertEqual(self.store.search_job(collector.last_job_id)['retrieved'], 1)

    def test_metadata_enrichment_fills_gaps_preserves_existing_provenance(self):
        ident = self.store.upsert({'doi': '10.1/a', 'title': 'Original'}, 'original')
        provenance = self.store.get(ident)['metadata_provenance']['title']
        raw = {'DOI': '10.1/a', 'title': ['Other title'], 'abstract': '<p>Findings</p>', 'container-title': ['Journal']}
        results = Collector(self.store, Sources(FakeHTTP([{'message': raw}]))).enrich([ident])
        self.assertEqual(results[0]['status'], 'enriched')
        paper = self.store.get(ident)
        self.assertEqual(paper['title'], 'Original')
        self.assertEqual(paper['abstract'], 'Findings')
        self.assertEqual(paper['metadata_provenance']['title'], provenance)
        self.assertEqual(paper['metadata_provenance']['abstract']['provider'], 'crossref')

    def test_search_comparison_and_statistics_use_unique_papers(self):
        first = Collector(self.store, Sources(FakeHTTP([page([article('1'), article('2')], 2)]))).search('europepmc', 'A')
        second = Collector(self.store, Sources(FakeHTTP([page([article('2'), article('3')], 3)]))).search('europepmc', 'B', 2)
        report = enrich_report(compare_searches(self.store, [first['job_id'], second['job_id']]))
        self.assertEqual(report['comparison']['union_count'], 3)
        self.assertEqual(report['comparison']['shared_by_all'], 1)
        self.assertAlmostEqual(report['comparison']['pairwise'][0]['jaccard'], 1/3)
        self.assertEqual(report['statistics']['paper_count'], 3)
        self.assertTrue(all(p['relevance_rank'] is None for p in report['papers']))
        report['papers'][0].update(author_list=['Smith', 'Smith'], keywords=['Force', 'force'], citation_count=0, citation_source='Fixture')
        enrich_report(report)
        self.assertEqual(report['statistics']['authors']['Smith'], 1)
        self.assertEqual(report['statistics']['keywords']['force'], 1)
        self.assertEqual(report['statistics']['citation_summary']['Fixture']['median'], 0)

    def test_configuration_relative_paths_validation_and_redaction(self):
        path = self.root / 'config.json'
        path.write_text(json.dumps({'state': 'state', 'output_dir': 'reports', 'ncbi_api_key': 'secret'}))
        with patch.dict('os.environ', {}, clear=True):
            config = load_config(path)
        self.assertEqual(Path(config['state']), self.root / 'state')
        self.assertEqual(public_config(config)['ncbi_api_key'], '***')
        path.write_text('{"retries": -1}')
        with self.assertRaises(ValueError):
            load_config(path)

    def cli(self, arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            status = main(['--state', str(self.root / 'cli-state'), '--quiet', *arguments])
        return status, json.loads(output.getvalue()) if output.getvalue() else None

    def test_cli_import_annotation_collection_and_four_format_exports(self):
        source = self.root / 'input.json'
        source.write_text(json.dumps([{'doi': '10.1/a', 'title': 'Paper', 'journal': 'Journal'}]))
        self.assertEqual(self.cli(['import', str(source), '--collection', 'Reading'])[0], 0)
        self.assertEqual(self.cli(['annotate', '1', '--tag', 'useful', '--favorite', '--status', 'included'])[0], 0)
        status, rows = self.cli(['papers', '--collection', 'Reading', '--tag', 'useful'])
        self.assertEqual((status, len(rows)), (0, 1))
        for format, suffix in [('json', 'json'), ('csv', 'csv'), ('bibtex', 'bib'), ('ris', 'ris')]:
            path = self.root / ('export.' + suffix)
            self.assertEqual(self.cli(['export', '--library', '--format', format, '--output', str(path)])[0], 0)
            self.assertEqual(read_records(path)[0]['doi'], '10.1/a')
        self.assertTrue((self.root / 'export.html').exists())

    def test_cli_saved_query_batch_and_failure_exit_status(self):
        self.assertEqual(self.cli(['queries', '--name', 'mechanics', '--query', 'TEST_CHANNEL', '--source', 'europepmc', '--limit', '1'])[0], 0)
        fake = FakeHTTP([page([article('1')], 1)])
        with patch('literature.cli.Sources', return_value=Sources(fake)):
            self.assertEqual(self.cli(['search', '--saved', 'mechanics', '--output', str(self.root / 'saved.json')])[0], 0)
        specs = self.root / 'batch.json'
        specs.write_text(json.dumps([{'query': 'A', 'source': 'europepmc', 'limit': 1}, {'query': 'B', 'source': 'europepmc', 'limit': 1}]))
        config = self.root / 'batch-config.json'
        config.write_text(json.dumps({'output_dir': 'batch-output'}))
        fake = FakeHTTP([page([article('2')], 1), APIError('Unavailable')])
        with patch('literature.cli.Sources', return_value=Sources(fake)):
            status, results = self.cli(['--config', str(config), 'batch', '--file', str(specs)])
        self.assertEqual(status, 1)
        self.assertEqual([r['status'] for r in results], ['complete', 'failed'])
        self.assertTrue((self.root / 'batch-output/search-3.json').exists())


class HTTPReliability(unittest.TestCase):
    def response(self, body=b'{"ok": true}'):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return body
        return Response()

    def test_cache_offline_reuse_expiration_and_no_stored_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            http = HTTP(cache_dir=directory, ncbi_api_key='secret')
            url = 'https://eutils.ncbi.nlm.nih.gov/test'
            with patch('literature.http.urlopen', return_value=self.response()) as opened:
                http.json(url)
                http.json(url)
                self.assertEqual(opened.call_count, 1)
            self.assertEqual(http.stats['cache_hits'], 1)
            self.assertEqual(http.cache_info()['entries'], 1)
            http.offline = True
            with patch('literature.http.urlopen', side_effect=AssertionError('Unexpected network')):
                self.assertTrue(http.json(url)['ok'])
                with self.assertRaisesRegex(APIError, 'not in the cache'):
                    http.json('https://example.org/missing')
            http.offline = False
            http.cache_ttl = -1
            with patch('literature.http.urlopen', return_value=self.response()) as opened:
                http.json(url)
                self.assertEqual(opened.call_count, 1)
            http.close()
            self.assertNotIn(b'secret', Path(directory, 'metadata-cache.sqlite3').read_bytes())

    def test_retry_rate_limit_and_connection_then_nonretryable_error(self):
        http = HTTP(retries=2)
        error = HTTPError('https://example.org', 429, 'Limit', {'Retry-After': '0'}, None)
        with patch('literature.http.urlopen', side_effect=[error, URLError('Offline'), self.response()]), patch('literature.http.time.sleep'):
            self.assertTrue(http.json('https://example.org')['ok'])
        self.assertEqual(http.stats['retries'], 2)
        with patch('literature.http.urlopen', side_effect=HTTPError('https://example.org', 404, 'Missing', {}, None)) as opened, patch('literature.http.time.sleep'):
            with self.assertRaisesRegex(APIError, '404'):
                http.get('https://example.org')
            self.assertEqual(opened.call_count, 1)
