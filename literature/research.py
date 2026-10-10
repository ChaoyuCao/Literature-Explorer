"""Citation relationships, deterministic term trends, and bounded discovery."""
import json
import re
from collections import Counter
from itertools import combinations
from pathlib import Path

from .analysis import STOP_WORDS
from .progress import emit_progress
from .references import ReferenceRetriever, write_json
from .store import identities, now


def keyword_trends(papers, top=30):
    years = Counter(str(p.get('year')) for p in papers if re.fullmatch(r'\d{4}', str(p.get('year') or '')))
    sources = {}
    for field in ('keywords', 'mesh_terms', 'title_terms', 'title_phrases'):
        total, annual, pairs = Counter(), {}, Counter()
        covered = 0
        for paper in papers:
            if field in ('keywords', 'mesh_terms'):
                terms = {re.sub(r'\s+', ' ', str(v)).strip().casefold() for v in paper.get(field, [])} - {''}
            else:
                words = re.findall(r'\b[\w-]+\b', (paper.get('title') or '').casefold())
                good = lambda w: len(w) > 2 and w not in STOP_WORDS and not w.isdigit()
                terms = ({w for w in words if good(w)} if field == 'title_terms' else
                         {' '.join((a, b)) for a, b in zip(words, words[1:]) if good(a) and good(b)})
            covered += bool(terms)
            total.update(terms)
            year = str(paper.get('year') or '')
            if year in years:
                annual.setdefault(year, Counter()).update(terms)
        selected = sorted(total, key=lambda t: (-total[t], t))[:top]
        chosen = set(selected)
        # Co-occurrence counts papers, not repeated mentions; restrict to displayed terms.
        for paper in papers:
            if field in ('keywords', 'mesh_terms'):
                terms = {re.sub(r'\s+', ' ', str(v)).strip().casefold() for v in paper.get(field, [])}
            else:
                words = re.findall(r'\b[\w-]+\b', (paper.get('title') or '').casefold())
                terms = set(words) if field == 'title_terms' else {' '.join(v) for v in zip(words, words[1:])}
            pairs.update(combinations(sorted(terms & chosen), 2))
        sources[field] = {'covered_papers': covered, 'terms': [
            {'term': term, 'paper_count': total[term], 'years': {
                year: {'paper_count': annual.get(year, {}).get(term, 0), 'total_papers': years[year],
                       'share': annual.get(year, {}).get(term, 0) / years[year]} for year in sorted(years)}} for term in selected],
            'cooccurrence': [{'left': a, 'right': b, 'paper_count': count}
                             for (a, b), count in sorted(pairs.items(), key=lambda v: (-v[1], v[0]))[:100]]}
    return {'paper_count': len(papers), 'year_counts': dict(sorted(years.items())),
            'unknown_year_papers': len(papers) - sum(years.values()), 'sources': sources,
            'policy': 'Terms count unique papers. Year shares use all selected papers with that publication year; missing keyword metadata is not evidence of absence. Title phrases are adjacent word pairs, not AI topics.'}


def citation_network(report, references, progress=None):
    from .publications import canonicalize
    if references['scope'].get('library_id') != report.get('library_id'):
        raise ValueError('Reference file belongs to a different catalogue.')
    papers = {p['id']: p for p in report['papers']}
    for entry in references['papers']:
        if entry['paper_id'] in papers:
            expected = identities(papers[entry['paper_id']])
            if any(expected.get(k) and expected[k] != v for k, v in entry['identifiers'].items() if k in expected):
                raise ValueError('Reference file paper identifiers differ from the report.')
    if references.get('schema_version', 1) < 2 or 'publications' not in references:
        canonicalize(references, report)
    displayed = {}
    for publication in references['publications']:
        selected = set(publication['library_ids']) & papers.keys()
        if selected:
            displayed[publication['id']] = min(selected)
    for paper_id in papers:
        if paper_id not in displayed.values():
            displayed['unidentified-seed-' + str(paper_id)] = paper_id
    entries = [e for e in references['papers'] if e['paper_id'] in papers]
    emit_progress(progress, 'network', 0, len(entries), edges=0, status='running')
    observations = [o for o in references['observations'] if o['seed_paper_id'] in papers]
    relations = [r for r in references['relationships'] if set(r['seed_paper_ids']) & papers.keys()]
    edges, self_links, outside = [], [], []
    for relation in relations:
        left, right = relation['citing'], relation['cited']
        evidence = {**relation, 'citing_publication_id': left, 'cited_publication_id': right,
                    'citing': displayed.get(left), 'cited': displayed.get(right)}
        if relation['status'] == 'self_link_pending_review':
            self_links.append(evidence)
        elif left in displayed and right in displayed:
            edges.append(evidence)
        else:
            outside.append(evidence)
    coverage = []
    for position, entry in enumerate(entries, 1):
        rows = [o for o in observations if o['seed_paper_id'] == entry['paper_id']]
        coverage.append({'paper_id': entry['paper_id'], 'status': entry['status'],
            'raw_observations': len(rows), 'reference_count': len({o['publication_id'] for o in rows if o['publication_id']}),
            'unresolved_observations': sum(o['resolution_status'] == 'unresolved' for o in rows),
            'ambiguous_observations': sum(o['resolution_status'] in ('ambiguous', 'review_required', 'conflict') for o in rows),
            'provider_outcomes': [{k: s.get(k) for k in ('provider', 'status', 'reported_count', 'next_page',
                'pages_retrieved', 'pagination_status', 'error')} for s in entry.get('providers', [])],
            'bibliography_completeness': 'unknown'})
        emit_progress(progress, 'network', position, len(entries), edges=len(edges), self_links=len(self_links), status='running')
    incoming, outgoing = Counter(), Counter()
    for edge in edges:
        outgoing[edge['citing']] += 1
        incoming[edge['cited']] += 1
    unresolved = sum(o['resolution_status'] == 'unresolved' for o in observations)
    ambiguous = sum(o['resolution_status'] in ('ambiguous', 'conflict', 'review_required') for o in observations)
    network = {'schema_version': 2, 'kind': 'citation_network', 'library_id': report.get('library_id'),
        'generated_at': now(), 'nodes': [{'id': paper_id, 'publication_id': pub_id,
            'title': papers[paper_id].get('title', ''), 'year': papers[paper_id].get('year', ''),
            'doi': papers[paper_id].get('doi', ''), 'pmid': papers[paper_id].get('pmid', ''),
            'incoming': incoming[paper_id], 'outgoing': outgoing[paper_id],
            'degree_scope': 'displayed_network'} for pub_id, paper_id in displayed.items()],
        'edges': edges, 'flagged_self_links': self_links, 'resolved_outside_network': outside,
        'resolved_outside_network_count': len(outside), 'unresolved_observations': unresolved,
        'ambiguous_observations': ambiguous, 'coverage': coverage,
        'unmatched_references': sum(len(r['observation_ids']) for r in outside) + unresolved,
        'ambiguous_references': ambiguous,
        'statistics': {'distinct_edges': len(edges), 'flagged_self_links': len(self_links),
            'resolved_outside_network': len(outside), 'unresolved_observations': unresolved,
            'ambiguous_observations': ambiguous},
        'policy': 'Edges combine compatible resolved publication identities and provider evidence. Article-to-itself links are flagged and excluded from degrees and expansion. Degrees are local to displayed nodes, never global citation counts.'}
    emit_progress(progress, 'network', len(entries), len(entries), edges=len(edges), self_links=len(self_links), status='complete')
    return network


def discover(store, sources, http, seed_report, output, direction='references', limit=100, progress=None, refresh=False, references_file=None):
    """One-hop expansion with a resumable checkpoint and an independent new-paper cap."""
    if direction not in ('references', 'citations', 'both') or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('Choose references/citations/both and a positive discovery limit.')
    output = Path(output).resolve()
    checkpoint = output.with_suffix('.discovery.json')
    scope = {'library_id': store.library_id, 'seeds': [{'id': p['id'], 'identifiers': identities(p)} for p in seed_report['papers']],
             'direction': direction, 'limit': limit, 'references_file': str(Path(references_file).resolve()) if references_file else None}
    if checkpoint.exists() and not refresh:
        state = json.loads(checkpoint.read_text(encoding='utf-8-sig'))
        if state.get('scope') != scope:
            raise ValueError('Discovery checkpoint has a different scope. Choose another output or use --refresh-references.')
    else:
        state = {'schema_version': 2, 'kind': 'discovery', 'scope': scope, 'status': 'running', 'next_candidate': 0,
                 'initial_library_ids': [p['id'] for p in store.list_papers()],
                 'candidates': [], 'found': [], 'links': [], 'errors': [], 'references_ready': False,
                 'seed_report': seed_report, 'job_id': store.job('discovery', 'citations', 'One-hop citation discovery',
                     parameters={'seed_ids': [p['id'] for p in seed_report['papers']], 'direction': direction, 'limit': limit, 'references_file': str(Path(references_file).resolve()) if references_file else None})}
        write_json(checkpoint, state)
    try:
        if not state['references_ready']:
            state['candidates'] = []
            state['errors'] = []
            state['unresolved_count'] = 0
            state['flagged_self_links'] = []
            for mode in (('references', 'citations') if direction == 'both' else (direction,)):
                path = output.with_suffix('.seed.references.json' if mode == 'references' else '.seed.citations.json')
                from .publications import canonicalize
                if references_file and mode == 'references':
                    data = json.loads(Path(references_file).read_text(encoding='utf-8-sig'))
                    if data.get('kind') != 'references' or data['scope'].get('library_id') != store.library_id:
                        raise ValueError('Choose reference observations from the current catalogue.')
                    if any(p['id'] not in {e['paper_id'] for e in data['papers']} for p in seed_report['papers']):
                        raise ValueError('Reference input does not cover the selected seed papers.')
                else:
                    data = ReferenceRetriever(http, progress).retrieve(seed_report, path, resume=not refresh, direction=mode)
                canonicalize(data, seed_report, store.list_papers())
                selected_ids = {p['id'] for p in seed_report['papers']}
                publications = {p['id']: p for p in data['publications']}
                grouped = {}
                for observation in data['observations']:
                    if observation['seed_paper_id'] not in selected_ids:
                        continue
                    if observation['resolution_status'] != 'resolved':
                        state.setdefault('unresolved_count', 0)
                        state['unresolved_count'] += 1
                        continue
                    if observation.get('citing_publication_id') == observation.get('cited_publication_id'):
                        state.setdefault('flagged_self_links', []).append(observation['id'])
                        continue
                    key = (observation['seed_paper_id'], observation['publication_id'])
                    candidate = grouped.setdefault(key, {'parent_id': observation['seed_paper_id'], 'direction': mode,
                        'reference': publications[observation['publication_id']], 'observation_ids': [], 'providers': [],
                        'evidence_file': str(Path(references_file).resolve()) if references_file and mode == 'references' else str(path)})
                    candidate['observation_ids'].append(observation['id'])
                    if observation['provider'] not in candidate['providers']:
                        candidate['providers'].append(observation['provider'])
                state['candidates'].extend(grouped.values())
                for entry in data['papers']:
                    if entry['paper_id'] in selected_ids and entry['status'] in ('partial', 'failed'):
                        state['errors'].append({'paper_id': entry['paper_id'], 'direction': mode,
                            'error': 'Some citation providers failed or returned incomplete metadata.'})
            state['references_ready'] = True
            write_json(checkpoint, state)
        seed_keys = {key for p in seed_report['papers'] for key in identities(p).items()}
        seed_ids = {p['id'] for p in seed_report['papers']}
        found_keys = {key for p in state['found'] for key in identities(p).items()}
        while state['next_candidate'] < len(state['candidates']):
            candidate = state['candidates'][state['next_candidate']]
            ref = candidate['reference']
            keys = set(identities(ref).items())
            if progress:
                progress(f'Discovery: candidate {state["next_candidate"] + 1}/{len(state["candidates"])}; {len(state["found"])} new papers saved (limit {limit}).')
            identifier = ref.get('doi') or ref.get('pmid') or ref.get('pmcid')
            if identifier and not keys & seed_keys:
                try:
                    existing = next((row[0] for key in keys if (row := store.db.execute(
                        'SELECT paper_id FROM aliases WHERE kind=? AND value=?', key).fetchone()) is not None), None)
                    was_in_library = existing is not None and existing in state.get('initial_library_ids', [])
                    if existing is None and len(state['found']) >= limit:
                        state.setdefault('deferred_candidates', []).append(state['next_candidate'])
                        state['next_candidate'] += 1
                        write_json(checkpoint, state)
                        continue
                    if existing is not None:
                        paper_id = existing
                        record = store.get(paper_id)
                        if any(ref.get(k) and record.get(k) and ref[k] != record[k] for k in ('doi', 'pmid', 'pmcid')):
                            raise ValueError('Library identifiers conflict with reference identifiers.')
                        if was_in_library:
                            state.setdefault('existing', []).append(paper_id)
                    else:
                        errors = []
                        for lookup_id in dict.fromkeys(v for v in (ref.get('doi'), ref.get('pmid'), ref.get('pmcid')) if v):
                            try:
                                record, provider = sources.lookup(lookup_id)
                                if any(ref.get(k) and record.get(k) and ref[k] != record[k] for k in ('doi', 'pmid', 'pmcid')):
                                    raise ValueError('Resolved metadata conflicts with reference identifiers.')
                                break
                            except (ValueError, RuntimeError, OSError) as exc:
                                errors.append(str(exc))
                        else:
                            raise ValueError('; '.join(errors))
                        paper_id = store.upsert(record, provider)
                        seed_ids = {r[0] for key in seed_keys if (r := store.db.execute(
                            'SELECT paper_id FROM aliases WHERE kind=? AND value=?', key).fetchone()) is not None}
                        if paper_id not in seed_ids and not any(set(identities(p).items()) & set(identities(record).items()) for p in state['found']):
                            state['found'].append({'id': paper_id, **{k: v for k, v in record.items() if k in ('doi', 'pmid', 'pmcid', 'source', 'source_id')}})
                            found_keys.update(identities(record).items())
                    if paper_id not in seed_ids:
                        link = {'parent_id': candidate['parent_id'], 'paper_id': paper_id, 'direction': candidate['direction'],
                                'in_library_before_discovery': was_in_library, 'observation_ids': candidate.get('observation_ids', []),
                                'providers': candidate.get('providers', []), 'evidence_file': candidate.get('evidence_file')}
                        if link not in state['links']:
                            store.discover(paper_id, state['job_id'], candidate['direction'], candidate['parent_id'])
                            state['links'].append(link)
                except (ValueError, RuntimeError, OSError) as exc:
                    state['errors'].append({'candidate': state['next_candidate'], 'identifier': identifier, 'error': str(exc)})
            elif not identifier:
                state.setdefault('unresolved_count', 0)
                state['unresolved_count'] += 1
            state['next_candidate'] += 1
            write_json(checkpoint, state)
        state['status'] = 'partial' if state['errors'] else 'limited' if state.get('deferred_candidates') else 'complete'
    except KeyboardInterrupt:
        state['status'] = 'interrupted'
        raise
    finally:
        write_json(checkpoint, state)
        store.finish(state['job_id'], state['status'], len(state['found']), len(state['candidates']))
    # Resolve identifiers again: enrichment may have reconciled catalogue IDs.
    result_ids = set()
    result_ids.update(state.get('existing', []))
    for record in state['found']:
        matches = {r[0] for key in identities(record).items() if (r := store.db.execute(
            'SELECT paper_id FROM aliases WHERE kind=? AND value=?', key).fetchone()) is not None}
        result_ids.update(matches)
    report = store.library_export(ids=sorted(result_ids)) if result_ids else store.library_export(ids=[-1])
    report['search'].update(id='discovery-' + str(state['job_id']), query='One-hop ' + direction + ' discovery',
                            source='citation-discovery', status=state['status'])
    report['discovery'] = {'direction': direction, 'depth': 1, 'limit': limit, 'seed_count': len(seed_report['papers']),
                           'seed_search_context': seed_report['search'],
                           'seed_search_provenance': {k: seed_report[k] for k in ('comparison', 'merge') if k in seed_report},
                           'links': state['links'], 'errors': state['errors'], 'unresolved_count': state.get('unresolved_count', 0),
                           'new_publications': len(state['found']), 'already_in_library': len(set(state.get('existing', []))),
                           'flagged_self_links': state.get('flagged_self_links', []), 'deferred_candidates': state.get('deferred_candidates', []),
                           'checkpoint': str(checkpoint), 'policy': 'Only resolved DOI/PMID/PMCID records are added. Original keyword search snapshots are preserved.'}
    return report
