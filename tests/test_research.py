import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature.cli import main
from literature.gui import search_arguments
from literature.http import APIError
from literature.progress import PREFIX, parse_progress
from literature.references import ReferenceRetriever, merge_references, reference_record
from literature.reports import enrich_report
from literature.research import citation_network, discover, keyword_trends
from literature.sources import Sources
from literature.store import Store


class MetadataHTTP:
    def __init__(self, crossref=None, epmc=None, xml=None):
        self.crossref, self.epmc, self.xml = crossref or {}, epmc or {}, xml
        self.calls = []
        self.response_info = {'cached': True, 'fetched_at': '2026-01-01T00:00:00Z'}

    def json(self, url, params=None):
        self.calls.append((url, params))
        if '/works/' in url:
            value = self.crossref.get(url.split('/works/')[1].replace('%2F', '/'), APIError('Missing DOI fixture'))
            if isinstance(value, BaseException):
                raise value
            return {'message': value}
        if url.endswith('/search'):
            return {'hitCount': 0, 'resultList': {'result': []}}
        value = self.epmc.get((url.rsplit('/', 1)[1], params['page']), {'hitCount': 0})
        if isinstance(value, BaseException):
            raise value
        return value

    def get(self, url, params):
        self.calls.append((url, params))
        return self.xml


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'state/catalog.sqlite3')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def report(self, records):
        ids = [self.store.upsert(record, 'fixture') for record in records]
        return self.store.library_export(ids=ids)

    def test_optional_flags_default_off_and_network_opt_in(self):
        args = search_arguments('channel', ['pubmed'], '1', 'relevance', {}, 'report.json')
        self.assertNotIn('--retrieve-references', args)
        self.assertNotIn('--citation-network', args)
        self.assertIn('--citation-network', search_arguments('channel', ['pubmed'], '1', 'relevance', {}, 'r.json', network=True))

    def test_reference_progress_counts_outcomes_and_restores_checkpoint(self):
        report = self.report([{'doi': '10.0000/a'}, {'doi': '10.0000/b'}, {'title': 'No identifier'},
                              {'doi': '10.0000/failure'}])
        http = MetadataHTTP({'10.0000/a': {'DOI': '10.0000/a', 'reference': [{'DOI': '10.0000/b'}]},
                             '10.0000/b': {'DOI': '10.0000/b', 'reference': [], 'reference-count': 0}})
        messages = []
        path = self.root/'progress.references.json'
        ReferenceRetriever(http, messages.append).retrieve(report, path)
        counts = [data for message in messages if (data := parse_progress(message))]
        self.assertEqual([c['completed'] for c in counts], [0, 1, 2, 3, 4])
        self.assertEqual(counts[-1], {'stage': 'references', 'completed': 4, 'total': 4,
                                     'with_references': 1, 'unavailable': 1, 'empty': 1, 'issues': 1})
        messages.clear()
        ReferenceRetriever(http, messages.append).retrieve(report, path)
        counts = [data for message in messages if (data := parse_progress(message))]
        self.assertEqual(counts[0]['completed'], 3)  # The failed provider is retried.
        self.assertEqual(counts[-1]['completed'], 4)
        self.assertEqual(counts[-1]['with_references'], 1)

    def test_progress_transport_ignores_invalid_and_ordinary_messages(self):
        for value in ('References: paper 1171, crossref, page 1.', PREFIX+'broken',
                      PREFIX+'[]', PREFIX+'{"stage":"network","completed":2,"total":1}',
                      PREFIX+'{"stage":"network","completed":true,"total":1}'):
            self.assertIsNone(parse_progress(value))

    def test_empty_network_progress_finishes_without_division_by_zero(self):
        report = self.report([])
        messages = []
        data = citation_network(report, {'scope': {'library_id': report['library_id']}, 'papers': []}, messages.append)
        self.assertEqual(data['edges'], [])
        self.assertEqual(parse_progress(messages[-1]),
                         {'stage': 'network', 'completed': 0, 'total': 0, 'edges': 0, 'self_links': 0, 'status': 'complete'})

    def test_reference_normalization_conflicts_and_unresolved_text(self):
        rows = [reference_record({'DOI': 'HTTPS://doi.org/10.0000/A', 'unstructured': 'A citation'}, 'crossref', 1),
                reference_record({'doi': '10.0000/a', 'title': 'Full title'}, 'europepmc', 2),
                reference_record({'doi': '10.0000/b', 'unstructured': 'A citation'}, 'crossref', 3)]
        merged = merge_references(rows)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]['title'], 'Full title')
        self.assertEqual(merged[0]['citation_text'], 'A citation')
        self.assertEqual(len(merged[0]['observations']), 2)
        bridged = merge_references([reference_record({'doi': '10.0000/a'}, 'crossref', 1),
                                    reference_record({'pmid': '42'}, 'pubmed', 1),
                                    reference_record({'doi': '10.0000/a', 'pmid': '42'}, 'europepmc', 1)])
        self.assertEqual(len(bridged), 1)
        self.assertEqual(len(bridged[0]['observations']), 3)

    def test_missing_is_unavailable_and_provider_empty_is_explicit(self):
        report = self.report([{'doi': '10.0000/a'}, {'doi': '10.0000/b'}, {'title': 'No IDs'}])
        http = MetadataHTTP({'10.0000/a': {'DOI': '10.0000/a'}, '10.0000/b': {'DOI': '10.0000/b', 'reference-count': 0, 'reference': []}})
        data = ReferenceRetriever(http).retrieve(report, self.root/'refs.json')
        self.assertEqual([p['status'] for p in data['papers']], ['unavailable', 'reported_empty', 'unavailable'])

    def test_pubmed_nested_lists_and_identifier_validation(self):
        report = self.report([{'pmid': '42'}])
        xml = b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>42</PMID></MedlineCitation><PubmedData><ReferenceList><Reference><Citation>Raw reference</Citation><ArticleIdList><ArticleId IdType="doi">10.0000/a</ArticleId></ArticleIdList></Reference><ReferenceList><Reference><Citation>Second reference</Citation></Reference></ReferenceList></ReferenceList></PubmedData></PubmedArticle></PubmedArticleSet>'
        data = ReferenceRetriever(MetadataHTTP(xml=xml)).retrieve(report, self.root/'refs.json')
        self.assertEqual(len(data['papers'][0]['references']), 2)
        self.assertEqual(data['papers'][0]['references'][0]['doi'], '10.0000/a')
        invalid = ReferenceRetriever(MetadataHTTP(xml=xml.replace(b'>42<', b'>43<'))).retrieve(report, self.root/'bad.json')
        self.assertEqual(invalid['status'], 'partial')
        self.assertIn('different PMID', invalid['papers'][0]['providers'][0]['error'])

    def test_pagination_interrupt_resume_and_scope_check(self):
        report = self.report([{'pmcid': 'PMC42'}])
        rows = [{'id': str(i), 'source': 'MED', 'title': 'Ref '+str(i)} for i in range(100)]
        http = MetadataHTTP(epmc={('references', 1): {'hitCount': 101, 'referenceList': {'reference': rows}},
                                  ('references', 2): {'hitCount': 101, 'referenceList': {'reference': [{'id': '100', 'source': 'MED'}]}}})
        def stop(message):
            if 'page 2' in message:
                raise KeyboardInterrupt
        path = self.root/'refs.json'
        with self.assertRaises(KeyboardInterrupt):
            ReferenceRetriever(http, stop).retrieve(report, path)
        checkpoint = json.loads(path.read_text())
        self.assertEqual(checkpoint['status'], 'interrupted')
        self.assertEqual(len(checkpoint['papers'][0]['references']), 100)
        messages = []
        resumed = ReferenceRetriever(http, messages.append).retrieve(report, path)
        counts = [data for message in messages if (data := parse_progress(message))]
        self.assertEqual([c['completed'] for c in counts], [0, 1])
        self.assertEqual(counts[-1]['with_references'], 1)
        self.assertEqual(len(http.calls), 2)
        self.assertEqual(len(resumed['papers'][0]['references']), 101)
        with self.assertRaises(ValueError):
            ReferenceRetriever(http).retrieve(self.store.library_export(ids=[-1]), path)

    def test_incomplete_empty_page_preserves_partial_and_retries(self):
        report = self.report([{'pmcid': 'PMC42'}])
        http = MetadataHTTP(epmc={('references', 1): {'hitCount': 101, 'referenceList': {'reference': [{'id': '1', 'source': 'MED'}]}},
                                  ('references', 2): {'hitCount': 101, 'referenceList': {'reference': []}}})
        path = self.root/'refs.json'
        data = ReferenceRetriever(http).retrieve(report, path)
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(len(data['papers'][0]['references']), 1)
        http.epmc[('references', 2)] = {'hitCount': 101, 'referenceList': {'reference': [{'id': '2', 'source': 'MED'}]}}
        ReferenceRetriever(http).retrieve(report, path)
        self.assertEqual(http.calls[-1][1]['page'], 2)

    def test_network_exact_matches_and_no_external_reference_titles_in_html(self):
        report = self.report([{'doi': '10.0000/a', 'title': 'Seed'}, {'doi': '10.0000/b', 'title': 'Target'}])
        http = MetadataHTTP({'10.0000/a': {'DOI': '10.0000/a', 'reference': [{'DOI': '10.0000/b'}, {'DOI': '10.0000/external', 'article-title': 'PRIVATE_REFERENCE_TITLE'}]},
                             '10.0000/b': {'DOI': '10.0000/b'}})
        reference_file = self.root/'refs.json'
        data = ReferenceRetriever(http).retrieve(report, reference_file)
        messages = []
        network = citation_network(report, data, messages.append)
        counts = [parse_progress(message) for message in messages]
        self.assertEqual([c['completed'] for c in counts], [0, 1, 2, 2])
        self.assertEqual(counts[-1]['edges'], 1)
        self.assertEqual(counts[-1]['status'], 'complete')
        self.assertEqual([(e['citing'], e['cited']) for e in network['edges']], [(report['papers'][0]['id'], report['papers'][1]['id'])])
        self.assertEqual(network['nodes'][1]['incoming'], 1)
        self.assertEqual(network['unmatched_references'], 1)
        output = self.root/'report.json'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state', str(self.root/'state'), 'references', '--library', '--references-file', str(reference_file),
                         '--citation-network', '--no-journal-metrics', '--output', str(output)])
        self.assertEqual(code, 0)
        self.assertNotIn('PRIVATE_REFERENCE_TITLE', output.with_suffix('.html').read_text(encoding='utf-8'))
        self.assertNotIn('PRIVATE_REFERENCE_TITLE', output.read_text(encoding='utf-8'))
        self.assertTrue(output.with_suffix('.network.json').is_file())

    def test_keyword_trends_unique_counts_denominators_and_phrases(self):
        data = keyword_trends([{'title': 'Channel gating by tension', 'keywords': ['Tension', 'tension'], 'year': '2024'},
                               {'title': 'Other channel', 'year': 2024},
                               {'title': 'Channel gating', 'keywords': ['Tension'], 'year': '2025'},
                               {'keywords': ['Tension'], 'year': 'Unknown'}])
        term = data['sources']['keywords']['terms'][0]
        self.assertEqual(term['paper_count'], 3)
        self.assertEqual(term['years']['2024']['share'], .5)
        self.assertEqual(data['unknown_year_papers'], 1)
        self.assertIn('channel gating', [p['term'] for p in data['sources']['title_phrases']['terms']])
        self.assertTrue(data['sources']['title_terms']['cooccurrence'])

    def test_discovery_unique_cap_does_not_modify_seed_snapshot(self):
        seed = self.report([{'doi': '10.0000/a', 'title': 'Seed'}])
        http = MetadataHTTP({'10.0000/a': {'DOI': '10.0000/a', 'reference': [{'DOI': '10.0000/a'}, {'DOI': '10.0000/b'}, {'DOI': '10.0000/c'}]},
                             '10.0000/b': {'DOI': '10.0000/b', 'title': ['Discovered B']},
                             '10.0000/c': {'DOI': '10.0000/c', 'title': ['Discovered C']}})
        result = discover(self.store, Sources(http), http, seed, self.root/'discovery.json', limit=2)
        self.assertEqual(len(result['papers']), 2)
        self.assertEqual(seed['papers'][0]['title'], 'Seed')
        self.assertEqual(result['discovery']['links'][0]['direction'], 'references')
        self.assertTrue((self.root/'discovery.seed.references.json').exists())

    def test_discovery_forward_citations_and_interrupt_resume(self):
        seed = self.report([{'pmid': '42', 'title': 'Seed'}])
        http = MetadataHTTP(epmc={('citations', 1): {'hitCount': 1, 'citationList': {'citation': [{'id': '43', 'source': 'MED', 'doi': '10.0000/b'}]}}},
                            crossref={'10.0000/b': {'DOI': '10.0000/b', 'title': ['Citing paper']}})
        def stop(message):
            if message.startswith('Discovery:'):
                raise KeyboardInterrupt
        output = self.root/'discovery.json'
        with self.assertRaises(KeyboardInterrupt):
            discover(self.store, Sources(http), http, seed, output, direction='citations', progress=stop)
        result = discover(self.store, Sources(http), http, seed, output, direction='citations')
        self.assertEqual(len(result['papers']), 1)
        self.assertEqual(result['discovery']['links'][0]['direction'], 'citations')
        self.assertEqual(len([c for c in http.calls if c[0].endswith('/citations')]), 1)

    def test_cli_search_no_opt_in_makes_no_reference_requests(self):
        from test_multi_search import FixtureSources
        fixture = FixtureSources({'europepmc': [{'doi': '10.0000/a', 'title': 'channel gating', 'year': '2025'}]})
        output = self.root/'search.json'
        with patch('literature.cli.Sources', return_value=fixture), contextlib.redirect_stdout(io.StringIO()):
            code = main(['--quiet', '--state', str(self.root/'state'), 'search', '--source', 'europepmc', '--query', 'fixture', '--output', str(output), '--no-journal-metrics'])
        self.assertEqual(code, 0)
        self.assertFalse(output.with_suffix('.references.json').exists())
        self.assertIn('keyword_trends', json.loads(output.read_text()))

    def test_cli_optional_references_and_interrupt_preserve_main_report(self):
        from test_multi_search import FixtureSources
        fixture = FixtureSources({'crossref': [{'doi': '10.0000/a', 'title': 'Seed'}]})
        output = self.root/'search.json'
        http = MetadataHTTP({'10.0000/a': {'DOI': '10.0000/a', 'reference': [{'DOI': '10.0000/b', 'unstructured': 'SIDE_ONLY'}]}})
        http.close = lambda: None
        with patch('literature.cli.Sources', return_value=fixture), patch('literature.cli.HTTP', return_value=http), contextlib.redirect_stdout(io.StringIO()):
            code = main(['--quiet', '--state', str(self.root/'state'), 'search', '--sources', 'crossref', '--query', 'fixture',
                         '--citation-network', '--output', str(output), '--no-journal-metrics'])
        self.assertEqual(code, 0)
        self.assertIn('SIDE_ONLY', output.with_suffix('.references.json').read_text())
        self.assertNotIn('SIDE_ONLY', output.read_text())
        class InterruptedHTTP(MetadataHTTP):
            def json(self, url, params=None):
                raise KeyboardInterrupt
        interrupted_http = InterruptedHTTP()
        interrupted_http.close = lambda: None
        stopped_output = self.root/'stopped.json'
        with patch('literature.cli.Sources', return_value=fixture), patch('literature.cli.HTTP', return_value=interrupted_http), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--quiet', '--state', str(self.root/'state'), 'search', '--sources', 'crossref', '--query', 'fixture',
                         '--retrieve-references', '--output', str(stopped_output), '--no-journal-metrics'])
        self.assertEqual(code, 130)
        self.assertEqual(json.loads(stopped_output.read_text())['reference_retrieval']['status'], 'interrupted')
        self.assertEqual(json.loads(stopped_output.with_suffix('.references.json').read_text())['status'], 'interrupted')

    def test_discovery_limit_counts_unique_papers_and_separate_seed_files(self):
        seed = self.report([{'doi': '10.0000/a'}])
        http = MetadataHTTP({'10.0000/a': {'DOI': '10.0000/a', 'reference': [{'DOI': '10.0000/a'}, {'DOI': '10.0000/b'}, {'DOI': '10.0000/b'}, {'DOI': '10.0000/c'}]},
                             '10.0000/b': {'DOI': '10.0000/b'}, '10.0000/c': {'DOI': '10.0000/c'}})
        result = discover(self.store, Sources(http), http, seed, self.root/'limited.json', limit=1)
        self.assertEqual(len(result['papers']), 1)
        self.assertEqual(result['search']['status'], 'limited')
        self.assertEqual(result['papers'][0]['doi'], '10.0000/b')

    def test_cli_rejects_output_colliding_with_reference_input(self):
        report = self.report([{'doi': '10.0000/a'}])
        refs = self.root/'refs.json'
        ReferenceRetriever(MetadataHTTP({'10.0000/a': {'DOI': '10.0000/a'}})).retrieve(report, refs)
        original = refs.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state', str(self.root/'state'), 'references', '--library', '--references-file', str(refs),
                         '--output', str(refs), '--no-journal-metrics'])
        self.assertEqual(code, 1)
        self.assertEqual(refs.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
