"""Offline browser checks for reference browsing, review evidence, and exports."""
import json
import tempfile
from pathlib import Path
from literature.publications import canonicalize
from literature.references import reference_record
from literature.reference_report import write_reference_report


def main():
    from playwright.sync_api import sync_playwright
    report = {'library_id': 'fixture', 'search': {'query': 'Fixture', 'limit': 10, 'status': 'limited'},
              'papers': [{'id': 1, 'doi': '10.0000/seed', 'title': 'Seed'},
                         {'id': 2, 'doi': '10.0000/target', 'pmid': '12', 'title': 'Target'}]}
    raw = [{'DOI': '10.0000/target'}, {'DOI': '10.0000/target'}, {'DOI': '10.0000/seed'},
           {'DOI': '10.0000/external', 'article-title': '</script><img src=x onerror=alert(1)>'},
           {'unstructured': 'Unresolved reference'}]
    doc = {'kind': 'references', 'scope': {'library_id': 'fixture', 'papers': [{'id': 1}]}, 'status': 'complete',
           'papers': [{'paper_id': 1, 'title': 'Seed', 'status': 'retrieved', 'providers': [
               {'provider': 'crossref', 'status': 'retrieved', 'next_page': None, 'records': [reference_record(r,'crossref',i+1) for i,r in enumerate(raw)]}]}]}
    canonicalize(doc, report)
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        path = Path(directory)/'references.html'
        write_reference_report(doc, path)
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(accept_downloads=True)
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.url))
        page.goto(path.as_uri())
        assert 'Raw observations 5' in page.locator('#coverage').inner_text()
        assert 'flagged self-links 1' in page.locator('#coverage').inner_text()
        assert page.locator('#publications tr').count() == 4  # Three identities plus header.
        assert page.locator('img').count() == 0, 'Raw citation data must not inject HTML'
        page.locator('#membership').select_option('external')
        assert page.locator('#publications tr').count() == 2
        with page.expect_download() as downloaded:
            page.locator('#export-publications').click()
        packet = json.loads(Path(downloaded.value.path()).read_text(encoding='utf-8'))
        assert len(packet['publications']) == 1
        assert packet['search_context']['status'] == 'limited'
        with page.expect_download() as downloaded:
            page.locator('#export-evidence').click()
        evidence = json.loads(Path(downloaded.value.path()).read_text(encoding='utf-8'))
        assert len(evidence['observations']) == 5
        assert evidence['observations'][-1]['resolution_status'] == 'unresolved'
        page.locator('#membership').select_option('')
        page.locator('#query').fill('12')
        assert page.locator('#publications tr').count() == 2
        assert 'Target' in page.locator('#publications').inner_text()
        page.locator('#review details summary').click()
        assert 'Unresolved reference' in page.locator('#review pre').inner_text()
        assert not errors, errors
        assert not [r for r in requests if r.startswith(('http:', 'https:'))], requests
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        browser.close()
    print('Reference browser passed: consolidation, library filters, unresolved review, evidence/publication exports, safe text, mobile layout, and offline operation.')


if __name__ == '__main__':
    main()
