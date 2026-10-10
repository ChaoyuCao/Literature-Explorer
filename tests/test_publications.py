import contextlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from literature.cli import main
from literature.publications import canonicalize, identifier, publication_export
from literature.references import reference_record, ReferenceRetriever
from literature.research import citation_network, discover
from literature.resolution import resolve_references
from literature.store import Store
from test_research import MetadataHTTP


def references(report, groups):
    return {'schema_version': 1, 'kind': 'references', 'scope': {'library_id': report['library_id'],
            'papers': [{'id': p['id']} for p in report['papers']]}, 'status': 'complete',
            'search_id': report['search']['id'], 'papers': [
                {'paper_id': p['id'], 'title': p['title'], 'identifiers': {k: p[k] for k in ('doi', 'pmid', 'pmcid') if p.get(k)},
                 'status': 'retrieved', 'providers': [{'provider': provider, 'retrieved_at': '2026-01-01T00:00:00Z',
                    'status': 'retrieved', 'next_page': None, 'records': [reference_record(r, provider, i+1) for i,r in enumerate(rows)]}
                    for provider, rows in groups.get(p['id'], {}).items()], 'references': []} for p in report['papers']]}


class FixtureSources:
    def __init__(self, http=None, bridge=(), candidates=(), lookup=None):
        self.http = http or MetadataHTTP()
        self.bridge, self.candidates, self.records = bridge, candidates, lookup or {}
        self.calls = []

    def stamp(self, record):
        return record

    def identifier_metadata(self, value):
        self.calls.append(('bridge', value))
        if isinstance(self.bridge, Exception):
            raise self.bridge
        return deepcopy(self.bridge)

    def bibliography_candidates(self, text):
        self.calls.append(('bibliography', text))
        if isinstance(self.candidates, Exception):
            raise self.candidates
        return deepcopy(self.candidates)

    def lookup(self, identifier):
        self.calls.append(('lookup', identifier))
        return self.records[identifier], 'fixture'


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'state/catalog.sqlite3')
        self.a = self.store.upsert({'doi': '10.0000/a', 'pmid': '11', 'title': 'Seed'}, 'fixture')
        self.b = self.store.upsert({'doi': '10.0000/b', 'pmid': '12', 'title': 'Earlier publication'}, 'fixture')
        self.report = self.store.library_export()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_normalization_and_pubmed_article_id_alias(self):
        self.assertEqual(identifier('doi', ' DOI: 10.0000/ABC '), '10.0000/abc')
        self.assertEqual(identifier('pmid', 'https://pubmed.ncbi.nlm.nih.gov/00012/'), '12')
        self.assertEqual(identifier('pmcid', 'https://pmc.ncbi.nlm.nih.gov/articles/pmc0012/'), 'PMC12')
        row = reference_record({'pubmed': '00012', 'pmc': 'pmc0012'}, 'pubmed', 1)
        self.assertEqual((row['pmid'], row['pmcid']), ('12', 'PMC12'))

    def test_cross_provider_bridging_and_repeated_citations_preserve_evidence(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/b'}, {'DOI': '10.0000/b'}],
            'pubmed': [{'pubmed': '12', 'citation_text': 'Original citation'}], 'europepmc': [{'source': 'MED', 'id': '12'}]}})
        original = deepcopy(data['papers'])
        canonicalize(data, self.report)
        self.assertEqual(data['coverage']['raw_observations'], 4)
        self.assertEqual(data['coverage']['resolved_distinct_publications'], 1)
        self.assertEqual([p['records'] for p in data['papers'][0]['providers']],
                         [p['records'] for p in original[0]['providers']])
        self.assertEqual(len({o['publication_id'] for o in data['observations']}), 1)
        self.assertEqual(data['observations'][2]['raw'], {'pubmed': '12', 'citation_text': 'Original citation'})
        self.assertEqual(data['observations'][2]['retrieved_at'], '2026-01-01T00:00:00Z')
        network = citation_network(self.report, data)
        self.assertEqual(len(network['edges']), 1)
        self.assertEqual(len(network['edges'][0]['observation_ids']), 4)
        self.assertEqual(set(network['edges'][0]['providers']), {'crossref', 'pubmed', 'europepmc'})

    def test_one_identity_cited_by_multiple_papers_keeps_two_edges(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c', 'article-title': 'External'}]},
                                       self.b: {'crossref': [{'DOI': '10.0000/c'}]}})
        canonicalize(data, self.report)
        self.assertEqual(data['coverage']['resolved_distinct_publications'], 1)
        self.assertEqual(data['coverage']['distinct_citation_edges'], 2)
        network = citation_network(self.report, data)
        self.assertEqual(network['resolved_outside_network_count'], 2)
        self.assertEqual(network['unresolved_observations'], 0)
        self.assertEqual(network['edges'], [])

    def test_identifier_conflict_quarantines_publication_evidence(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/b', 'pmid': '99'}]}})
        canonicalize(data, self.report)
        self.assertEqual(data['observations'][0]['resolution_status'], 'conflict')
        self.assertTrue(data['identifier_conflicts'])
        self.assertEqual(data['coverage']['resolved_distinct_publications'], 0)
        self.assertEqual(citation_network(self.report, data)['edges'], [])

    def test_conflicting_aliases_in_one_observation_require_review(self):
        data = references(self.report, {self.a: {'pubmed': [{'pmid': '12', 'pubmed': '99'}]}})
        canonicalize(data, self.report)
        self.assertEqual(data['observations'][0]['resolution_status'], 'conflict')
        self.assertEqual(data['observations'][0]['identifier_conflicts'], {'pmid': ['12', '99']})
        self.assertEqual(citation_network(self.report, data)['edges'], [])

    def test_self_links_flagged_but_same_author_earlier_article_valid(self):
        data = references(self.report, {self.a: {'europepmc': [{'source': 'MED', 'id': '11'}, {'source': 'MED', 'id': '12'}]}})
        canonicalize(data, self.report)
        network = citation_network(self.report, data)
        self.assertEqual([(e['citing'], e['cited']) for e in network['edges']], [(self.a, self.b)])
        self.assertEqual(len(network['flagged_self_links']), 1)
        self.assertEqual(network['nodes'][0]['incoming'], 0)
        self.assertEqual(network['nodes'][0]['outgoing'], 1)
        self.assertEqual(network['nodes'][0]['degree_scope'], 'displayed_network')

    def test_list_position_is_never_publication_identity(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c'}], 'pubmed': [{'pubmed': '99'}]}})
        canonicalize(data, self.report)
        self.assertEqual(data['coverage']['resolved_distinct_publications'], 2)

    def test_missing_identifiers_remain_unresolved_not_distinct_publications(self):
        data = references(self.report, {self.a: {'crossref': [{'unstructured': 'Same text'}, {'unstructured': 'Same text'}]}})
        canonicalize(data, self.report)
        self.assertEqual(data['coverage']['raw_observations'], 2)
        self.assertEqual(data['coverage']['unresolved_observations'], 2)
        self.assertEqual(data['coverage']['resolved_distinct_publications'], 0)
        self.assertEqual(len(publication_export(data)['unresolved_observations']), 2)

    def test_missing_metadata_explicit_nulls_in_publication_export(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c'}]}})
        canonicalize(data, self.report)
        row = publication_export(data)['publications'][0]
        self.assertIsNone(row['title'])
        self.assertIsNone(row['abstract'])
        self.assertIsNone(row['pmid'])
        self.assertEqual(row['links']['doi'], 'https://doi.org/10.0000/c')

    def test_optional_metadata_bridge_merges_doi_and_pmid_without_positions(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c'}], 'pubmed': [{'pubmed': '99'}]}})
        xml = b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>99</PMID><Article><ArticleTitle>Resolved title</ArticleTitle><Journal><Title>Journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.0000/c</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'
        sources = FixtureSources(MetadataHTTP(xml=xml))
        path = self.root/'resolved.json'
        resolve_references(data, self.report, self.store, sources, path)
        self.assertEqual(data['coverage']['resolved_distinct_publications'], 1)
        self.assertEqual(len(sources.http.calls), 1)
        self.assertEqual(sources.calls, [])
        self.assertEqual(publication_export(data)['publications'][0]['title'], 'Resolved title')
        resolve_references(data, self.report, self.store, sources, path)
        self.assertEqual(len(sources.http.calls), 1)

    def test_ambiguous_citation_only_candidates_remain_reviewable(self):
        data = references(self.report, {self.a: {'pubmed': [{'citation_text': 'Smith J. Journal. 2020;1:2.'}]}})
        sources = FixtureSources(candidates=[{'doi': '10.0000/c', 'title': 'Candidate one', 'year': '2020'},
                                             {'doi': '10.0000/d', 'title': 'Candidate two', 'year': '2020'}])
        resolve_references(data, self.report, self.store, sources, self.root/'resolved.json')
        observation = data['observations'][0]
        self.assertEqual(observation['resolution_status'], 'ambiguous')
        self.assertEqual(len(observation['candidates']), 2)
        self.assertIsNone(observation['publication_id'])
        self.assertEqual(data['coverage']['ambiguous_matches'], 1)
        self.assertFalse(observation['matching_evidence'][0]['auto_accepted'])

    def test_failed_enrichment_preserves_retrieval_and_retries_on_resume(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c'}]}})
        sources = FixtureSources(bridge=RuntimeError('Provider unavailable'))
        sources.lookup = lambda value: (_ for _ in ()).throw(RuntimeError('Fallback unavailable'))
        path = self.root/'resolved.json'
        resolve_references(data, self.report, self.store, sources, path)
        self.assertEqual(data['status'], 'complete')
        self.assertEqual(data['resolution_status'], 'partial')
        self.assertEqual(data['coverage']['raw_observations'], 1)
        sources.bridge = [{'doi': '10.0000/c', 'pmid': '99', 'title': 'Now available'}]
        resolve_references(data, self.report, self.store, sources, path)
        self.assertEqual(data['resolution_status'], 'complete')
        self.assertEqual(publication_export(data)['publications'][0]['title'], 'Now available')

    def test_enrichment_interrupt_keeps_original_evidence(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c'}]}})
        def stop(message):
            if message.startswith('Resolution:'):
                raise KeyboardInterrupt
        path = self.root/'resolved.json'
        with self.assertRaises(KeyboardInterrupt):
            resolve_references(data, self.report, self.store, FixtureSources(), path, stop)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(saved['resolution_status'], 'interrupted')
        self.assertEqual(saved['observations'][0]['raw'], {'DOI': '10.0000/c'})

    def test_discovery_reuses_library_and_excludes_self_link(self):
        data = references(self.report, {self.a: {'europepmc': [{'source': 'MED', 'id': '11'}, {'source': 'MED', 'id': '12'}]}})
        canonicalize(data, self.report)
        path = self.root/'refs.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        seed = {**self.report, 'papers': [self.report['papers'][0]]}
        sources = FixtureSources()
        result = discover(self.store, sources, sources.http, seed, self.root/'discovery.json', references_file=path)
        self.assertEqual(result['discovery']['new_publications'], 0)
        self.assertEqual(result['discovery']['already_in_library'], 1)
        self.assertEqual(len(result['discovery']['flagged_self_links']), 1)
        self.assertEqual(sources.calls, [])
        self.assertEqual(result['discovery']['links'][0]['providers'], ['europepmc'])

    def test_cli_upgrade_preserves_inputs_and_search_context(self):
        data = references(self.report, {self.a: {'pubmed': [{'pubmed': '12'}]}})
        report_path, reference_path = self.root/'input.json', self.root/'input.references.json'
        report_path.write_text(json.dumps(self.report), encoding='utf-8')
        reference_path.write_text(json.dumps(data), encoding='utf-8')
        original = report_path.read_bytes(), reference_path.read_bytes()
        output = self.root/'analyzed.json'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state', str(self.root/'state'), 'references', '--report', str(report_path),
                         '--references-file', str(reference_path), '--citation-network', '--no-journal-metrics', '--output', str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(original, (report_path.read_bytes(), reference_path.read_bytes()))
        analyzed = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(analyzed['search'], self.report['search'])
        self.assertTrue(output.with_suffix('.references.html').is_file())
        self.assertEqual(json.loads(output.with_suffix('.publications.json').read_text(encoding='utf-8'))['schema_version'], 2)

    def test_cli_selected_report_keeps_only_requested_seed_observations(self):
        data = references(self.report, {self.a: {'pubmed': [{'pubmed': '12'}]},
                                       self.b: {'pubmed': [{'pubmed': '11'}]}})
        report_path, reference_path = self.root/'input.json', self.root/'input.references.json'
        report_path.write_text(json.dumps(self.report), encoding='utf-8')
        reference_path.write_text(json.dumps(data), encoding='utf-8')
        output = self.root/'selected.json'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state', str(self.root/'state'), 'references', '--report', str(report_path),
                         '--ids', str(self.a), '--references-file', str(reference_path),
                         '--no-journal-metrics', '--output', str(output)])
        self.assertEqual(code, 0)
        saved = json.loads(output.with_suffix('.references.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['coverage']['papers_requested'], 1)
        self.assertEqual({o['seed_paper_id'] for o in saved['observations']}, {self.a})

    def test_new_resolve_command_separates_enrichment_from_retrieval(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c'}]}})
        report_path, reference_path = self.root/'input.json', self.root/'input.references.json'
        report_path.write_text(json.dumps(self.report), encoding='utf-8')
        reference_path.write_text(json.dumps(data), encoding='utf-8')
        sources = FixtureSources(bridge=[{'doi': '10.0000/c', 'pmid': '99', 'title': 'Enriched title'}])
        sources.http.close = lambda: None
        output = self.root/'enriched.json'
        with patch('literature.cli.Sources', return_value=sources), patch('literature.cli.HTTP', return_value=sources.http), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['--state', str(self.root/'state'), 'resolve-references', '--report', str(report_path),
                         '--references-file', str(reference_path), '--no-journal-metrics', '--output', str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(sources.http.calls, [])
        saved = json.loads(output.with_suffix('.publications.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['publications'][0]['title'], 'Enriched title')
        self.assertEqual(saved['coverage']['resolution_status'], 'complete')

    def test_library_metadata_is_reused_without_service_requests(self):
        self.store.upsert({'doi': '10.0000/c', 'pmid': '99', 'title': 'Existing', 'year': '2020', 'journal': 'Journal'}, 'fixture')
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c'}], 'pubmed': [{'pubmed': '99'}]}})
        sources = FixtureSources()
        resolve_references(data, self.report, self.store, sources, self.root/'resolved.json')
        self.assertEqual(sources.http.calls, [])
        self.assertEqual(sources.calls, [])
        self.assertEqual(data['coverage']['resolved_distinct_publications'], 1)

    def test_single_weak_citation_candidate_requires_review(self):
        data = references(self.report, {self.a: {'pubmed': [{'citation_text': 'Incomplete citation'}]}})
        resolve_references(data, self.report, self.store, FixtureSources(candidates=[{'doi': '10.0000/c', 'title': 'Possible'}]), self.root/'r.json')
        self.assertEqual(data['observations'][0]['resolution_status'], 'review_required')
        self.assertIsNone(data['observations'][0]['publication_id'])

    def test_service_identifier_conflict_is_recorded_without_merge(self):
        data = references(self.report, {self.a: {'crossref': [{'DOI': '10.0000/c', 'pmcid': 'PMC99'}]}})
        resolve_references(data, self.report, self.store, FixtureSources(bridge=[{'doi': '10.0000/c', 'pmcid': 'PMC98'}]), self.root/'r.json')
        self.assertEqual(data['resolution_status'], 'partial')
        self.assertTrue(data['resolution_conflicts'])
        self.assertEqual(publication_export(data)['publications'][0]['pmcid'], 'PMC99')


if __name__ == '__main__':
    unittest.main()
