"""Verify packaged installations outside the repository with synthetic offline data."""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import venv
import zipfile
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent


def run(command, cwd, expected=0):
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'LITERATURE_CONFIG', 'LITERATURE_EMAIL', 'NCBI_API_KEY'):
        env.pop(key, None)
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
    if result.returncode != expected:
        raise AssertionError(f'{command}: exit {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result.stdout


def cache_fixture(state):
    cache = state / 'cache'
    cache.mkdir(parents=True)
    db = sqlite3.connect(cache / 'metadata-cache.sqlite3')
    db.execute('CREATE TABLE responses(key TEXT PRIMARY KEY, fetched REAL, body BLOB)')
    def response(url, params, body):
        target = url + '?' + urlencode(sorted(params.items()))
        value = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        db.execute('INSERT INTO responses VALUES(?,?,?)', (hashlib.sha256(target.encode()).hexdigest(), time.time(), value))
    ncbi = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils'
    response(ncbi + '/esearch.fcgi', {'db': 'pubmed', 'term': 'RELEASE_TEST_FIXTURE', 'retmode': 'json', 'retmax': 1, 'sort': 'relevance', 'tool': 'mechanosensor_literature'},
             {'esearchresult': {'count': '1', 'idlist': ['90000001']}})
    response(ncbi + '/efetch.fcgi', {'db': 'pubmed', 'id': '90000001', 'retmode': 'xml', 'tool': 'mechanosensor_literature'},
             '<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>90000001</PMID><Article><ArticleTitle>Synthetic release fixture</ArticleTitle><Journal><Title>Synthetic Journal</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.0000/distribution.fixture</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>')
    response('https://www.ebi.ac.uk/europepmc/webservices/rest/search', {'query': 'RELEASE_TEST_FIXTURE', 'format': 'json', 'resultType': 'core', 'pageSize': 1, 'cursorMark': '*'},
             {'hitCount': 1, 'resultList': {'result': [{'id': '90000001', 'source': 'MED', 'pmid': '90000001', 'doi': '10.0000/distribution.fixture', 'title': 'Synthetic release fixture', 'abstractText': 'Synthetic response data for release verification only.', 'citedByCount': 3}]}})
    response('https://api.crossref.org/works', {'query.bibliographic': 'RELEASE_TEST_FIXTURE', 'sort': 'score', 'order': 'desc', 'filter': 'type:journal-article', 'rows': 1, 'cursor': '*'},
             {'message': {'total-results': 1, 'items': [{'DOI': '10.0000/distribution.fixture', 'title': ['Synthetic release fixture'], 'container-title': ['Synthetic Journal'], 'type': 'journal-article', 'is-referenced-by-count': 77}]}})
    db.commit()
    db.close()


def exercise(command, cwd, version):
    assert run([*command, '--version'], cwd).strip() == 'literature ' + version
    assert '--sources' in run([*command, 'search', '--help'], cwd)
    assert 'download' not in run([*command, '--help'], cwd)
    state = cwd / 'state'
    cache_fixture(state)
    metrics = cwd / 'synthetic-metrics.csv'
    metrics.write_text('journal,impact_factor,year,source\nSynthetic Journal,7.5,2025,Release test fixture\n', encoding='utf-8')
    output = cwd / 'report.json'
    base = [*command, '--state', state, '--offline', '--quiet']
    result = json.loads(run([*base, 'search', '--sources', 'pubmed', 'europepmc', 'crossref', '--query', 'RELEASE_TEST_FIXTURE', '--limit', 1, '--journal-metrics', metrics, '--output', output], cwd))
    assert result['status'] == 'complete'
    data = json.loads(output.read_text(encoding='utf-8'))
    assert data['merge']['input_paper_count'] == 3
    assert data['merge']['unique_paper_count'] == 1
    assert data['merge']['duplicates_removed'] == 2
    paper = data['papers'][0]
    assert paper['abstract'].startswith('Synthetic response')
    assert paper['metadata_provenance']['abstract']['provider'] == 'europepmc'
    assert paper['journal_metric']['impact_factor'] == 7.5
    assert [c['count'] for c in paper['citation_counts']] == [3, 77]
    assert [s['source'] for s in paper['search_memberships']] == ['pubmed', 'europepmc', 'crossref']
    assert len(paper['source_records']) == 3 and paper['relevance_rank'] is None
    html = output.with_suffix('.html').read_text(encoding='utf-8')
    assert 'Merged database searches' in html and '__PRACTICAL_SCRIPT__' not in html and '__REPORT_DATA__' not in html
    run([*base, 'annotate', str(paper['id']), '--note', 'Release verification', '--tag', 'synthetic', '--favorite'], cwd)
    run([*base, 'collections', '--name', 'Verification', '--add', str(paper['id'])], cwd)
    library = cwd / 'library.json'
    run([*base, 'export', '--library', '--collection', 'Verification', '--no-journal-metrics', '--output', library], cwd)
    assert json.loads(library.read_text(encoding='utf-8'))['papers'][0]['library']['favorite']
    for format, suffix in [('csv', 'csv'), ('ris', 'ris'), ('bibtex', 'bib')]:
        path = cwd / ('export.' + suffix)
        run([*base, 'export', '--library', '--format', format, '--output', path], cwd)
        assert path.stat().st_size > 0
        run([*base, 'import', path, '--format', format], cwd)
    merged = cwd / 'rebuilt.json'
    run([*base, 'merge', '--search-ids', '1', '2', '3', '--output', merged], cwd)
    assert json.loads(merged.read_text(encoding='utf-8'))['merge']['duplicates_removed'] == 2
    run([*base, 'config', '--write', cwd / 'config.json'], cwd)
    run([*base, 'search', '--source', 'europepmc', '--query', 'UNCACHED_RELEASE_FIXTURE', '--limit', 1, '--output', cwd / 'failed.json'], cwd, expected=1)
    assert json.loads((cwd / 'failed.json').read_text(encoding='utf-8'))['search']['status'] == 'failed'
    return {'multi_source_input_records': 3, 'unique_papers': 1, 'duplicates_removed': 2,
            'checks': ['version/help', 'offline search', 'deduplication', 'metadata/provenance', 'journal metric', 'HTML assets', 'annotations/collections', 'CSV/RIS/BibTeX exchange', 'saved search merge', 'failure exit/report']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', default='dist')
    args = parser.parse_args()
    dist = Path(args.dist).resolve()
    manifest = json.loads((dist / 'release-manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['artifacts']:
        path = dist / entry['file']
        assert path.stat().st_size == entry['bytes']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
    for line in (dist / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        assert hashlib.sha256((dist / name).read_bytes()).hexdigest() == digest
    wheel = next(dist.glob('mechanosensor_literature-' + manifest['version'] + '-*.whl'))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert 'literature/dashboard.html' in names and 'literature/explorer.js' in names
        assert any(n.endswith('/licenses/LICENSE') for n in names)
    source = dist / ('mechanosensor_literature-' + manifest['version'] + '.tar.gz')
    with tarfile.open(source) as archive:
        names += archive.getnames()
    for name in names:
        parts = Path(name).parts
        assert not set(parts) & {'downloads', 'outputs', 'dataset', '.literature', '.venv', '.venv-build', 'dist', '__pycache__'}
        assert not name.endswith(('journal-metrics.csv', '.sqlite3', '.pyc'))
    temporary_root = ROOT / 'tmp'
    temporary_root.mkdir(exist_ok=True)
    result = {'version': manifest['version'], 'archive_contents_and_checksums': 'passed'}
    with tempfile.TemporaryDirectory(prefix='release-verification-', dir=temporary_root) as temporary:
        root = Path(temporary)
        environment = root / 'clean-environment'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        work = root / 'wheel-working-folder'
        work.mkdir()
        run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', wheel], work)
        module = run([python, '-c', 'import literature; print(literature.__file__)'], work).strip()
        assert Path(module).is_relative_to(environment)
        result['wheel'] = exercise([python, '-m', 'literature'], work, manifest['version'])
        launcher = environment / ('Scripts/literature.exe' if os.name == 'nt' else 'bin/literature')
        assert 'literature ' + manifest['version'] in run([launcher, '--version'], work)
        portable = dist / ('mechanosensor-literature-' + manifest['version'] + '-windows-x64.zip')
        if portable.exists() and os.name == 'nt':
            unpacked = root / 'portable'
            with zipfile.ZipFile(portable) as archive:
                assert {'literature.exe', 'LICENSE', 'LICENSE-PYTHON.txt', 'LICENSE-PYINSTALLER.txt', 'DISTRIBUTION.md'} <= set(archive.namelist())
                archive.extractall(unpacked)
            exe_work = root / 'executable-working-folder'
            exe_work.mkdir()
            result['windows_executable'] = exercise([unpacked / 'literature.exe'], exe_work, manifest['version'])
            if (unpacked / 'journal-metrics.csv').is_file():
                from literature.reports import load_metrics
                table = unpacked / 'journal-metrics.csv'
                names, _, year = load_metrics(table)
                assert hashlib.sha256(table.read_bytes()).hexdigest() == hashlib.sha256((dist/'journal-metrics.csv').read_bytes()).hexdigest()
                automatic = exe_work / 'automatic-metrics.json'
                run([unpacked/'literature.exe', '--state', exe_work/'state', '--offline', '--quiet',
                    'visualize', '--search-id', '1', '--output', automatic], exe_work)
                report = json.loads(automatic.read_text(encoding='utf-8'))
                assert report['journal_metrics']['file'] == str(table.resolve())
                assert report['journal_metrics']['year'] == year
                result['bundled_metrics'] = {'automatic_discovery': 'passed', 'metric_year': year,
                    'journal_count': len({entry['journal'] for entry in names.values()})}
    (dist / 'verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
