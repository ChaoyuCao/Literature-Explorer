import contextlib
import io
import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature.cli import main
from literature.gui import metric_summary, rebuild_existing, search_arguments
from literature.reports import discover_metrics, enrich_report, journal_key, load_metrics
from literature.sources import Sources
from literature.store import Store
from test_literature import FakeHTTP


class DesktopTests(unittest.TestCase):
    def test_metric_discovery_prefers_workspace_and_falls_back_to_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, bundle = root/'workspace', root/'bundle'
            workspace.mkdir()
            bundle.mkdir()
            self.assertIsNone(discover_metrics(workspace, bundle))
            (bundle/'journal-metrics.csv').touch()
            self.assertEqual(discover_metrics(workspace, bundle), bundle/'journal-metrics.csv')
            (workspace/'journal-metrics.csv').touch()
            self.assertEqual(discover_metrics(workspace, bundle), workspace/'journal-metrics.csv')
            (workspace/'.literature').mkdir()
            (workspace/'.literature/journal-metrics.csv').touch()
            self.assertEqual(discover_metrics(workspace, bundle), workspace/'.literature/journal-metrics.csv')

    def test_csv_aliases_and_conjunctions_preserve_journal_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'metrics.csv'
            path.write_text('journal,impact_factor,year,source,aliases\nScience Education,4,2025,A,Education journal\nScience & Education,2.5,2025,B,\n', encoding='utf-8-sig')
            names,_,year = load_metrics(path)
            self.assertEqual(year,2025)
            self.assertEqual(names[journal_key('Education journal')]['impact_factor'],4)
            self.assertEqual(names[journal_key('Science & Education')]['impact_factor'],2.5)
            self.assertEqual(names[journal_key('Science Education')]['impact_factor'],4)
            self.assertIn('Loaded 2 journals',metric_summary(path))

    def test_invalid_or_empty_table_rejected_before_search(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'metrics.csv'
            for content in ('journal,IF\nA,5\n', 'journal,impact_factor,year,source\n'):
                path.write_text(content,encoding='utf-8')
                with self.assertRaises(ValueError):
                    search_arguments('TEST_CHANNEL',['pubmed'],'1','relevance',{},'out.json',str(path))

    def test_apply_metrics_rebuilds_json_and_html_without_search_or_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            csv=root/'metrics.csv'
            csv.write_text('journal,issn,impact_factor,year,source\nFull Journal,1234-5678,7.5,2025,Fixture\n',encoding='utf-8')
            original=enrich_report({'papers':[{'id':1,'journal':'Abbreviated','issns':['1234-5678']},
                {'id':2,'journal':'Missing','issns':[]}], 'search':{'query':'saved query'}})
            path=root/'original.json'
            path.write_text(json.dumps(original),encoding='utf-8')
            events=queue.Queue()
            with patch('literature.gui.run_engine',side_effect=AssertionError('Should not search')):
                rebuild_existing(path,root/'updated.json',events,csv)
            result=events.get_nowait()
            self.assertEqual(result[:2],('done',0))
            updated=json.loads((root/'updated.json').read_text(encoding='utf-8'))
            self.assertEqual(updated['search'],original['search'])
            self.assertEqual(updated['papers'][0]['journal_metric']['impact_factor'],7.5)
            self.assertIsNone(updated['papers'][1]['journal_metric'])
            self.assertEqual(updated['journal_metrics']['matched_papers'],1)
            self.assertEqual(json.loads(path.read_text()),original)
            self.assertIn('"impact_factor": 7.5',(root/'updated.html').read_text(encoding='utf-8'))
            rebuild_existing(root/'updated.json',root/'preserved.json',events)
            self.assertEqual(events.get_nowait()[:2],('done',0))
            self.assertEqual(json.loads((root/'preserved.json').read_text()),updated)

    def test_query_phrases_filters_and_per_database_limit(self):
        args = search_arguments('TEST_CHANNEL AND "patch clamp"', ['pubmed', 'europepmc'], '25', 'date', {'author': 'Smith', 'date_from': '2020-01-01'}, 'report.json')
        self.assertEqual(args[args.index('--query')+1], 'TEST_CHANNEL AND "patch clamp"')
        self.assertEqual(args[args.index('--limit')+1], '25')
        self.assertEqual(args[args.index('--sources')+1:args.index('--query')], ['pubmed', 'europepmc'])
        self.assertIn('--date-from', args)
        self.assertNotIn('--limit', search_arguments('x', ['pubmed'], '', 'relevance', {}, 'out.json'))

    def test_invalid_query_databases_limit_date_or_metric(self):
        for query,sources,limit,filters,metrics in [('', ['pubmed'], '1', {}, ''), ('x', [], '1', {}, ''),
            ('x', ['pubmed'], '0', {}, ''), ('x', ['pubmed'], '1.5', {}, ''),
            ('x', ['pubmed'], '1', {'date_from': 'invalid'}, ''), ('x', ['pubmed'], '1', {}, 'does-not-exist.csv')]:
            with self.subTest(query=query,limit=limit,filters=filters,metrics=metrics), self.assertRaises(ValueError):
                search_arguments(query, sources, limit, 'relevance', filters, 'out.json', metrics)

    def test_cancel_writes_interrupted_job_and_partial_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cancel = root / 'cancel'
            cancel.touch()
            fake = FakeHTTP([])
            with patch('literature.cli.Sources', return_value=Sources(fake)), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(['--state', str(root/'state'), '--cancel-file', str(cancel), 'search', '--sources', 'europepmc', 'crossref', '--query', 'fixture', '--no-journal-metrics', '--output', str(root/'report.json')])
            self.assertEqual(code, 130)
            self.assertEqual(fake.calls, [])
            data = json.loads((root/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(data['search']['status'], 'interrupted')
            store = Store(root/'state/catalog.sqlite3')
            try:
                self.assertEqual(store.search_job(1)['status'], 'interrupted')
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
