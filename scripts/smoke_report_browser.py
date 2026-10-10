"""Optional offline browser verification (requires Playwright and Microsoft Edge)."""
import json
import tempfile
from pathlib import Path

from literature.reports import enrich_report, write_report
from literature.research import citation_network


def main():
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        metrics = root / 'metrics.csv'
        metrics.write_text('journal,impact_factor,year,source\nFixture A,9,2025,Test fixture\nFixture B,0,2025,Test fixture\n', encoding='utf-8')
        papers = []
        for i in range(1, 31):
            journal = ['Fixture A', 'Fixture B', 'Fixture C'][i % 3]
            papers.append({'id': i, 'title': f'TEST_CHANNEL paper {i}', 'journal': journal,
                           'authors': 'Example author', 'abstract': f'Mechanosensor findings {i}',
                           'year': 2025 if i <= 10 else 2024, 'publication_types': ['Research' if i % 2 else 'Review'],
                           'search_rank': i, 'relevance_rank': i, 'relevance_score': None,
                           'doi': f'10.0000/fixture-{i}', 'citation_count': None,
                           'keywords': ['Tension', 'channel gating', 'tension']})
        report = enrich_report({'schema_version': 2, 'library_id': 'browser-fixture', 'search': {'id': 1, 'query': 'TEST_CHANNEL', 'source': 'pubmed',
                                 'sort_order': 'relevance', 'started': '2025-01-01', 'status': 'complete',
                                 'total': 30, 'error': None}, 'papers': papers}, metrics)
        report['citation_network'] = citation_network(report, {'scope': {'library_id': 'browser-fixture'}, 'papers': [
            {'paper_id': 1, 'identifiers': {'doi': '10.0000/fixture-1'}, 'status': 'retrieved', 'references': [
                {'doi': '10.0000/fixture-2', 'observations': [{'provider': 'crossref'}]}]},
            {'paper_id': 2, 'identifiers': {'doi': '10.0000/fixture-2'}, 'status': 'retrieved', 'references': [
                {'doi': '10.0000/fixture-3', 'observations': [{'provider': 'europepmc'}]}]}]})
        write_report(report, root / 'fixture.json', root / 'fixture.html')
        errors, requests = [], []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            page.goto((root / 'fixture.html').as_uri())
            assert page.locator('#citation-graph svg').count() == 1
            assert page.locator('#network-edges tbody tr').count() == 2
            assert '30/30 papers' in page.locator('#trend-note').inner_text()
            page.locator('#trend-term').select_option('tension')
            assert page.locator('#trend-table tbody tr').count() == 2
            assert '100.0%' in page.locator('#trend-table').inner_text()
            assert page.locator('#term-pairs tbody tr').count() == 1
            page.locator('#network-node').select_option('1')
            assert page.locator('#network-edges tbody tr').count() == 1
            page.locator('#network-node').select_option('')
            page.get_by_text('Journal impact factors and sources', exact=True).click()
            assert page.locator('#metric-journals tr').count() == 3
            assert page.locator('#metric-journals tr').first.locator('td').first.inner_text() == 'Fixture A'
            assert page.locator('#papers tr').count() == 25
            assert page.locator('#papers tr').first.locator('td').first.inner_text() == '1'
            page.locator('#next').click()
            assert page.locator('#papers tr').count() == 5
            assert page.locator('#papers tr').first.locator('td').first.inner_text() == '26'
            page.locator('#paper-sort').select_option('impact')
            assert 'Fixture A' in page.locator('#papers tr').first.inner_text()
            page.locator('#next').click()
            assert 'Fixture C' in page.locator('#papers tr').last.inner_text()
            page.locator('#journal-sort').select_option('impact')
            assert page.locator('#journals .bar-label').all_text_contents() == ['Fixture A', 'Fixture B', 'Fixture C']
            page.locator('#paper-sort').select_option('relevance')
            page.locator('#year-filter').select_option('2025')
            assert page.locator('#papers tr').count() == 10
            page.locator('#type-filter').select_option('Review')
            assert page.locator('#papers tr').count() == 5
            page.locator('#text-filter').fill('findings 2')
            assert page.locator('#papers tr').count() == 1
            assert page.locator('#citation-graph svg').count() == 0
            with page.expect_download() as network_info:
                page.locator('#export-network').click()
            network_packet = json.loads(Path(network_info.value.path()).read_text(encoding='utf-8'))
            assert [p['id'] for p in network_packet['nodes']] == [2]
            assert network_packet['edges'] == []
            with page.expect_download() as trends_info:
                page.locator('#export-trends').click()
            trends_packet = json.loads(Path(trends_info.value.path()).read_text(encoding='utf-8'))
            assert trends_packet['sources']['keywords']['terms'][0]['paper_count'] == 1
            page.locator('#papers td.title summary').click()
            assert page.locator('#papers td.title details').get_attribute('open') is not None
            page.get_by_text('Edit note and tags', exact=True).click()
            page.get_by_label('Paper note', exact=True).fill('Check figure 2 · 蛋白质')
            page.get_by_label('Paper tags', exact=True).fill('useful, mechanics')
            page.get_by_label('Paper review status', exact=True).select_option('included')
            page.get_by_label('Favorite paper', exact=True).check()
            page.get_by_text('Save annotation', exact=True).click()
            page.locator('#review-filter').select_option('included')
            page.locator('#favorite-filter').select_option('yes')
            page.locator('#tag-filter').select_option('useful')
            assert page.locator('#papers tr').count() == 1
            with page.expect_download() as annotation_info:
                page.locator('#export-annotations').click()
            annotation_packet = json.loads(Path(annotation_info.value.path()).read_text(encoding='utf-8'))
            assert annotation_packet['library_id'] == 'browser-fixture'
            assert annotation_packet['annotations'][0]['id'] == 2
            assert annotation_packet['annotations'][0]['library']['status'] == 'included'
            with page.expect_download() as csv_info:
                page.locator('#export-csv').click()
            assert 'Check figure 2' in Path(csv_info.value.path()).read_text(encoding='utf-8-sig')
            with page.expect_download() as svg_info:
                page.locator('#export-journals').click()
            assert '<svg' in Path(svg_info.value.path()).read_text(encoding='utf-8')
            with page.expect_download() as download_info:
                page.locator('#export').click()
            subset = json.loads(Path(download_info.value.path()).read_text(encoding='utf-8'))
            assert [p['id'] for p in subset['papers']] == [2]
            assert subset['statistics']['abstract_count'] == 1
            assert subset['statistics']['journal_count'] == 1
            assert subset['statistics']['journals'][0]['paper_count'] == 1
            assert subset['journal_metrics']['matched_papers'] == 0
            assert subset['statistics']['review_status'] == {'included': 1}
            assert subset['keyword_trends']['paper_count'] == 1
            assert subset['citation_network']['edges'] == []
            page.reload()
            page.locator('#review-filter').select_option('included')
            assert page.locator('#papers tr').count() == 1
            assert page.locator('#papers tr').first.locator('td').first.inner_text() == '2'
            page.locator('#text-filter').fill('no matching title or abstract')
            assert page.locator('#page-label').inner_text() == '0 results'
            page.locator('#reset').click()
            page.locator('#journal-filter').select_option('Fixture B')
            assert page.locator('#papers tr').count() == 10
            assert 'IF 0' in page.locator('#papers tr').first.inner_text()
            page.locator('#reset').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            # A saved date-ordered search cannot masquerade as relevance ordering.
            report['search']['sort_order'] = 'date'
            for paper in report['papers']:
                paper['relevance_rank'] = None
            write_report(report, root / 'date.json', root / 'date.html')
            page.goto((root / 'date.html').as_uri())
            assert page.locator('#paper-sort option[value="relevance"]').evaluate('(option) => option.disabled'), (page.locator('#ranking-note').inner_text(), errors)
            assert page.locator('#paper-sort').input_value() == 'search'
            assert not errors, errors
            assert not [url for url in requests if url.startswith(('http:', 'https:'))], requests
            browser.close()
    print('Browser checks passed: sorting, filters, abstracts, persistent annotations, JSON/CSV/SVG export, mobile layout, and offline operation.')


if __name__ == '__main__':
    main()
