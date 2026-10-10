"""Optional reference metadata, kept outside paper reports and HTML."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from .http import APIError
from .progress import emit_progress
from .publications import publication_identifiers
from .sources import CROSSREF, EPMC, NCBI, doi, plain
from .store import identities, now


def write_json(path, data):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def reference_record(raw, provider, position):
    """Keep unresolved citation text; never invent bibliographic fields."""
    ids = {str(k).lower(): v for k, v in raw.items()}
    source, identifier = raw.get('source'), str(raw.get('id') or '')
    normalized = publication_identifiers(raw)
    return {'doi': normalized.get('doi', ''), 'pmid': normalized.get('pmid', ''),
            'pmcid': normalized.get('pmcid', ''),
            'title': plain(raw.get('title') or raw.get('article-title')),
            'authors': raw.get('authorString') or raw.get('author') or '',
            'journal': raw.get('journalTitle') or raw.get('journalAbbreviation') or raw.get('journal-title') or '',
            'year': str(raw.get('pubYear') or raw.get('year') or ''),
            'volume': raw.get('volume') or '', 'pages': raw.get('pageInfo') or raw.get('first-page') or '',
            'citation_text': plain(raw.get('unstructured') or raw.get('citation_text')),
            'source': source or '', 'source_id': identifier,
            'observations': [{'provider': provider, 'position': raw.get('citedOrder', position), 'raw': raw}]}


def merge_references(rows):
    """Match exact identifiers or identical unresolved text, with conflict checks."""
    merged, index = [], {}
    for row in rows:
        keys = list(identities(row).items())
        text = re.sub(r'\s+', ' ', row.get('citation_text', '')).strip().casefold()
        if text:
            keys.append(('citation_text', text))
        candidates = sorted({index[key] for key in keys if key in index})
        target = next((i for i in candidates if all(not row.get(k) or not merged[i].get(k) or
                      row[k] == merged[i][k] for k in ('doi', 'pmid', 'pmcid'))), None)
        if target is None:
            target = len(merged)
            merged.append({**row, 'observations': list(row['observations'])})
        else:
            existing = merged[target]
            for other in candidates:
                if other == target or merged[other] is None:
                    continue
                additional = merged[other]
                if any(existing.get(k) and additional.get(k) and existing[k] != additional[k] or
                       row.get(k) and additional.get(k) and row[k] != additional[k] for k in ('doi', 'pmid', 'pmcid')):
                    continue
                for key, value in additional.items():
                    if key != 'observations' and not existing.get(key):
                        existing[key] = value
                existing['observations'].extend(additional['observations'])
                merged[other] = None
                index = {key: target if value == other else value for key, value in index.items()}
            for key, value in row.items():
                if key != 'observations' and not existing.get(key):
                    existing[key] = value
            existing['observations'].extend(row['observations'])
        for key in keys:
            index[key] = target
    return [row for row in merged if row is not None]


class ReferenceRetriever:
    def __init__(self, http, progress=None):
        self.http, self.progress = http, progress

    def providers(self, paper, direction):
        if direction == 'citations':
            return ['europepmc'] if paper.get('pmid') or paper.get('pmcid') or paper.get('doi') else []
        return [p for p, usable in [('crossref', paper.get('doi')), ('pubmed', paper.get('pmid')),
                                    ('europepmc', paper.get('pmid') or paper.get('pmcid') or paper.get('doi'))] if usable]

    def page(self, paper, provider, page, direction, size=100):
        if provider == 'crossref':
            raw = self.http.json(CROSSREF + '/works/' + quote(doi(paper['doi']), safe=''))['message']
            if doi(raw.get('DOI')) != doi(paper['doi']):
                raise APIError('Crossref reference response has a different DOI.')
            rows = raw.get('reference', [])
            total = raw.get('reference-count', raw.get('references-count'))
            available = bool(rows) or 'reference' in raw and total == 0
            return rows, total, None, available
        if provider == 'pubmed':
            payload = self.http.get(NCBI + '/efetch.fcgi', {'db': 'pubmed', 'id': paper['pmid'],
                       'retmode': 'xml', 'tool': 'mechanosensor_literature'})
            try:
                root = ET.fromstring(payload)
            except ET.ParseError:
                raise APIError('Invalid PubMed reference XML.') from None
            articles = root.findall('PubmedArticle')
            if len(articles) != 1 or articles[0].findtext('./MedlineCitation/PMID') != str(paper['pmid']):
                raise APIError('PubMed reference response has a different PMID.')
            lists = articles[0].findall('./PubmedData/ReferenceList')
            rows = []
            for listing in lists:
                for ref in listing.findall('.//Reference'):
                    ids = {n.get('IdType'): n.text or '' for n in ref.findall('./ArticleIdList/ArticleId')}
                    rows.append({**ids, 'citation_text': ''.join(ref.find('Citation').itertext())
                                 if ref.find('Citation') is not None else ''})
            return rows, len(rows) if lists else None, None, bool(lists)
        if not paper.get('pmid') and not paper.get('pmcid'):
            found = self.http.json(EPMC + '/search', {'query': 'DOI:' + doi(paper['doi']), 'format': 'json', 'resultType': 'core'})
            rows = [r for r in found.get('resultList', {}).get('result', []) if doi(r.get('doi')) == doi(paper['doi'])]
            if len(rows) != 1:
                return [], None, None, False
            source, identifier = rows[0]['source'], str(rows[0]['id'])
        else:
            source, identifier = ('MED', str(paper['pmid'])) if paper.get('pmid') else ('PMC', paper['pmcid'])
        raw = self.http.json(EPMC + '/' + source + '/' + quote(identifier, safe='') + '/' + direction,
                            {'format': 'json', 'page': page, 'pageSize': size})
        key = 'reference' if direction == 'references' else 'citation'
        rows = raw.get(key + 'List', {}).get(key, [])
        if not isinstance(rows, list) or 'hitCount' not in raw:
            raise APIError('Europe PMC returned an invalid citation response.')
        total = int(raw['hitCount'])
        if not rows and (page - 1) * size < total:
            raise APIError('Europe PMC returned an empty page before all references were retrieved.')
        return rows, total, page + 1 if page * size < total else None, bool(rows) or page > 1

    def retrieve(self, report, path, resume=True, direction='references', limit=None):
        if direction not in ('references', 'citations'):
            raise ValueError('Direction must be references or citations.')
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError('Reference limit must be a positive integer.')
        scope = {'library_id': report.get('library_id'), 'direction': direction, 'limit': limit,
                 'papers': [{'id': p['id'], 'identifiers': identities(p)} for p in report['papers']]}
        path = Path(path).expanduser().resolve()
        if path.exists() and resume:
            document = json.loads(path.read_text(encoding='utf-8-sig'))
            if document.get('scope') != scope:
                raise ValueError('Reference checkpoint belongs to a different paper selection. Choose another output path or refresh references.')
        else:
            document = {'schema_version': 1, 'kind': direction, 'scope': scope, 'generated_at': now(),
                        'search_id': report['search']['id'], 'status': 'running', 'papers': []}
        entries = {p['paper_id']: p for p in document['papers']}
        completed = {pid for pid, entry in entries.items() if entry['status'] != 'running'
                     and all(s['next_page'] is None for s in entry['providers'])}
        def notify():
            finished = [entries[pid] for pid in completed]
            emit_progress(self.progress, direction, len(completed), len(report['papers']),
                          with_references=sum(bool(e['references']) for e in finished),
                          unavailable=sum(e['status'] == 'unavailable' for e in finished),
                          empty=sum(e['status'] == 'reported_empty' for e in finished),
                          issues=sum(e['status'] in ('partial', 'failed') for e in finished))
        try:
            notify()
            for paper in report['papers']:
                entry = entries.get(paper['id'])
                if entry is None:
                    entry = {'paper_id': paper['id'], 'identifiers': identities(paper), 'title': paper.get('title', ''),
                             'status': 'running', 'providers': [], 'references': []}
                    entries[paper['id']] = entry
                    document['papers'].append(entry)
                for provider in self.providers(paper, direction):
                    state = next((s for s in entry['providers'] if s['provider'] == provider), None)
                    if state is None:
                        state = {'provider': provider, 'status': 'running', 'next_page': 1, 'reported_count': None,
                                 'retrieved_at': None, 'records': [], 'error': None}
                        entry['providers'].append(state)
                    if state['next_page'] is None:
                        continue
                    state['error'] = None
                    try:
                        while state['next_page'] is not None:
                            if self.progress:
                                self.progress(f'{direction.title()}: paper {paper["id"]}, {provider}, page {state["next_page"]}.')
                            size = min(100, limit) if limit else 100
                            rows, total, following, available = self.page(paper, provider, state['next_page'], direction, size)
                            if not isinstance(rows, list):
                                raise APIError('Provider returned an invalid reference list.')
                            normalized = [reference_record(r, provider, len(state['records']) + i + 1) for i, r in enumerate(rows)]
                            if state['next_page'] > 1 and rows and rows == [r['observations'][0]['raw'] for r in state['records'][-len(rows):]]:
                                raise APIError('Europe PMC repeated a citation page.')
                            remaining = max(0, limit - len(state['records'])) if limit else len(normalized)
                            state['records'].extend(normalized[:remaining])
                            state['reported_count'] = total
                            state['retrieved_at'] = getattr(self.http, 'response_info', {}).get('fetched_at') or now()
                            for record in normalized:
                                for evidence in record['observations']:
                                    evidence['retrieved_at'] = state['retrieved_at']
                                    evidence['response_info'] = dict(getattr(self.http, 'response_info', {}))
                            state['cached'] = getattr(self.http, 'response_info', {}).get('cached', False)
                            capped = limit is not None and (len(normalized) > remaining or following is not None and len(state['records']) >= limit)
                            state['next_page'] = None if capped else following
                            state['pages_retrieved'] = state.get('pages_retrieved', 0) + 1
                            state['pagination_status'] = 'limited' if capped else 'in_progress' if following else 'complete'
                            state['status'] = 'limited' if capped else 'retrieving' if following else 'retrieved' if available else 'unavailable'
                            if not following and available and total is not None and total > len(state['records']) and not capped:
                                state['status'] = 'partial'
                                state['pagination_status'] = 'partial'
                            self._save(document, entry, path)
                    except (ValueError, RuntimeError, OSError) as exc:
                        state['status'], state['error'] = 'failed', str(exc)
                        state['pagination_status'] = 'failed'
                        self._save(document, entry, path)
                self._save(document, entry, path)
                completed.add(paper['id'])
                notify()
            document['status'] = 'partial' if any(p['status'] in ('partial', 'failed') for p in document['papers']) else 'complete'
        except KeyboardInterrupt:
            document['status'] = 'interrupted'
            raise
        finally:
            document['updated_at'] = now()
            from .publications import canonicalize
            canonicalize(document, report)
            write_json(path, document)
        return document

    def _save(self, document, entry, path):
        states = entry['providers']
        entry['references'] = merge_references([r for s in states for r in s['records']])
        statuses = {s['status'] for s in states}
        entry['status'] = ('partial' if statuses & {'failed', 'partial'} and entry['references'] else
                           'failed' if 'failed' in statuses else 'partial' if 'partial' in statuses else
                           'limited' if 'limited' in statuses else 'retrieved' if entry['references'] else
                           'reported_empty' if 'retrieved' in statuses else 'unavailable')
        write_json(path, document)


def reference_summary(document):
    papers = document['papers']
    return {**document.get('coverage', {}), 'status': document['status'], 'paper_count': len(document['scope']['papers']),
            'papers_with_references': len({o['seed_paper_id'] for o in document.get('observations', [])})
                if 'observations' in document else sum(bool(p.get('references')) for p in papers),
            'reference_count': document.get('coverage', {}).get('resolved_distinct_publications'),
            'provider_outcomes': [{'paper_id': p['paper_id'], **{k: s.get(k) for k in
                ('provider', 'status', 'reported_count', 'next_page', 'pages_retrieved', 'pagination_status', 'error', 'retrieved_at')},
                'raw_observations': sum(len(r.get('observations', [])) for r in s.get('records', []))}
                for p in papers for s in p.get('providers', [])],
            'failed_papers': sum(p['status'] == 'failed' for p in papers),
            'unavailable_papers': sum(p['status'] == 'unavailable' for p in papers),
            'policy': 'Provider coverage varies; unavailable lists do not mean zero references. Pagination completion does not establish completeness of the published bibliography.'}
