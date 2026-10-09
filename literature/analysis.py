"""Transparent metadata statistics and comparisons between saved searches."""
import json
import re
from collections import Counter
from statistics import median
from . import __version__
from .store import now

STOP_WORDS = set('a an and are as at be been by can could for from has have in into is it its may of on or our than that the their these this to using was we were which with without study studies results analysis effect effects'.split())


def metadata_statistics(papers):
    authors, keywords, mesh, terms, tags = (Counter() for _ in range(5))
    citations = {}
    for paper in papers:
        authors.update(set(paper.get('author_list') or []))
        keywords.update(set(word.strip().casefold() for word in paper.get('keywords', []) if word.strip()))
        mesh.update(set(word.strip().casefold() for word in paper.get('mesh_terms', []) if word.strip()))
        terms.update(set(word for word in re.findall(r'\b[\w-]+\b', (paper.get('title') or '').casefold()) if len(word) > 2 and word not in STOP_WORDS and not word.isdigit()))
        tags.update(set((paper.get('library') or {}).get('tags', [])))
        value = paper.get('citation_count')
        if value is not None:
            source = paper.get('citation_source') or 'Unspecified source'
            citations.setdefault(source, []).append(value)
    return {'authors': dict(authors.most_common(30)), 'keywords': dict(keywords.most_common(30)),
            'mesh_terms': dict(mesh.most_common(30)), 'title_terms': dict(terms.most_common(30)),
            'tags': dict(tags.most_common()),
            'structured_author_count': sum(bool(p.get('author_list')) for p in papers),
            'review_status': dict(Counter((p.get('library') or {}).get('status', 'unreviewed') for p in papers)),
            'citation_summary': {source: {'papers_with_counts': len(values), 'total': sum(values),
                                          'median': median(values), 'max': max(values)} for source,values in citations.items()}}


def compare_searches(store, search_ids):
    search_ids = list(dict.fromkeys(search_ids))
    if len(search_ids) < 2:
        raise ValueError('Compare at least two different saved searches.')
    return _combined_report(store, search_ids)


def _combined_report(store, search_ids):
    reports = [store.search_export(i) for i in search_ids]
    sets = [{p['id'] for p in r['papers']} for r in reports]
    union = set().union(*sets)
    shared = set.intersection(*sets)
    matrix = []
    for i, left in enumerate(sets):
        for j in range(i + 1, len(sets)):
            right = sets[j]
            overlap, combined = left & right, left | right
            matrix.append({'left': search_ids[i], 'right': search_ids[j], 'shared': len(overlap),
                           'jaccard': len(overlap) / len(combined) if combined else None})
    papers = {}
    searches = []
    for index, report in enumerate(reports):
        searches.append({**report['search'], 'unique_papers': len(sets[index]),
                         'exclusive_papers': len(sets[index] - set().union(*(s for i,s in enumerate(sets) if i != index)))})
        for paper in report['papers']:
            papers.setdefault(paper['id'], {**paper, 'search_rank': None, 'relevance_rank': None,
                                            'relevance_score': None, 'search_memberships': []})
            papers[paper['id']]['search_memberships'].append({'search_id': report['search']['id'],
                                                            'source': report['search']['source'],
                                                            'rank': paper['search_rank']})
    return {'schema_version': 2, 'library_id': store.library_id, 'software_version': __version__, 'generated_at': now(),
            'search': {'id': 'comparison', 'source': 'combined', 'query': 'Compare searches: ' + ', '.join(map(str, search_ids)),
                       'status': 'comparison', 'sort_order': 'local', 'total': None, 'started': now(), 'error': None},
            'papers': list(papers.values()),
            'comparison': {'searches': searches, 'union_count': len(union), 'shared_by_all': len(shared),
                           'pairwise': matrix, 'matching_policy': 'Canonical catalogue IDs reconciled by exact DOI/PMID/PMCID; title-only similarities require review.'}}


def merge_searches(store, search_ids, query=None):
    """Merge selected snapshots, filling gaps while retaining every source record."""
    search_ids = list(dict.fromkeys(search_ids))
    if not search_ids:
        raise ValueError('Merge at least one saved search.')
    report = _combined_report(store, search_ids)
    papers = {p['id']: p for p in report['papers']}
    excluded = {'id', 'library', 'metadata_provenance', 'search_rank', 'relevance_rank',
                'relevance_score', 'provider_retrieved_at', 'source', 'source_id',
                'citation_count', 'citation_source', 'citation_retrieved_at'}
    for paper in papers.values():
        paper['source_records'], paper['sources'], paper['citation_counts'] = [], [], []
        paper['metadata_provenance'] = dict(paper.get('metadata_provenance') or {})
    for search_id in search_ids:
        snapshot = store.search_export(search_id)
        provider = snapshot['search']['source']
        for original in snapshot['papers']:
            paper = papers[original['id']]
            paper['source_records'].append({'search_id': search_id, 'database': provider,
                                           'record': original})
            if provider not in paper['sources']:
                paper['sources'].append(provider)
            for key, value in original.items():
                if key not in excluded and paper.get(key) in (None, '', [], {}) and value not in (None, '', [], {}):
                    paper[key] = value
                    provenance = (original.get('metadata_provenance') or {}).get(key)
                    if provenance:
                        paper['metadata_provenance'][key] = provenance
            if original.get('citation_count') is not None:
                paper['citation_counts'].append({'database': provider, 'search_id': search_id,
                    'source': original.get('citation_source'), 'count': original['citation_count'],
                    'retrieved_at': original.get('citation_retrieved_at') or (original.get('metadata_provenance') or {}).get('citation_count', {}).get('retrieved_at')})
                if paper.get('citation_count') is None:
                    for key in ('citation_count', 'citation_source', 'citation_retrieved_at'):
                        paper[key] = original.get(key)
                        provenance = (original.get('metadata_provenance') or {}).get(key)
                        if provenance:
                            paper['metadata_provenance'][key] = provenance
    searches = report['comparison']['searches']
    statuses = {s['status'] for s in searches}
    status = 'interrupted' if 'interrupted' in statuses else 'partial' if statuses & {'failed', 'running'} else 'limited' if 'limited' in statuses else 'complete'
    queries = list(dict.fromkeys(_original_query(s) for s in searches))
    report['search'].update(id='merged-' + '-'.join(map(str, search_ids)), query=query or ' | '.join(queries),
                            status=status, error='Some searches failed or were interrupted; see individual search errors.' if statuses & {'failed', 'running', 'interrupted'} else None)
    input_count = sum(s['unique_papers'] for s in searches)
    report['merge'] = {'input_paper_count': input_count, 'unique_paper_count': len(papers),
                      'duplicates_removed': input_count - len(papers),
                      'matching_policy': report['comparison']['matching_policy'],
                      'metadata_policy': 'First selected source with a populated field; later sources fill gaps. Original records and provider-specific citation counts are retained. Ranks remain per search.'}
    return report


def _original_query(search):
    # The job query may include database filters; keep the original submitted keywords.
    return json.loads(search.get('parameters') or '{}').get('original_query', search['query'])
