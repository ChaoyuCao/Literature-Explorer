"""Deterministic publication identities and lossless reference observations.

Positions identify observations, never publications. Bibliographic candidates are
review suggestions; only shared, compatible identifiers consolidate publications.
"""
import hashlib
import json
import re
from copy import deepcopy
from urllib.parse import unquote

from .sources import doi, plain

FIELDS = ('title', 'authors', 'year', 'journal', 'abstract', 'volume', 'issue', 'pages', 'article_url')


def identifier(kind, value):
    value = unquote(str(value or '').strip())
    if kind == 'doi':
        value = doi(value.strip())
        return value if re.fullmatch(r'10\.\d{4,9}/\S+', value) else ''
    if kind == 'pmid':
        value = re.sub(r'^(?:PMID\s*:\s*|https?://pubmed\.ncbi\.nlm\.nih\.gov/)', '', value, flags=re.I).strip('/')
        return str(int(value)) if value.isdigit() and int(value) > 0 else ''
    value = re.sub(r'^(?:PMCID\s*:\s*|https?://(?:pmc\.)?ncbi\.nlm\.nih\.gov/(?:pmc/)?articles/)', '', value, flags=re.I).strip('/').upper()
    return 'PMC' + str(int(value[3:])) if re.fullmatch(r'PMC\d+', value) and int(value[3:]) > 0 else ''


def publication_identifiers(record):
    lower = {str(k).lower(): v for k, v in record.items()}
    result = {k: str(v) for k, v in record.items() if k.startswith('source:') and v}
    for kind, aliases in (('doi', ('doi',)), ('pmid', ('pmid', 'pubmed')), ('pmcid', ('pmcid', 'pmc'))):
        value = next((identifier(kind, lower.get(a)) for a in aliases if identifier(kind, lower.get(a))), '')
        if value:
            result[kind] = value
    source = str(record.get('source') or '').upper()
    source_id = str(record.get('source_id') or record.get('id') or '').strip()
    if source in ('MED', 'PMC') and source_id:
        kind = 'pmid' if source == 'MED' else 'pmcid'
        normalized = identifier(kind, source_id)
        if normalized:
            result.setdefault(kind, normalized)
            result['source:' + source] = normalized
    elif source and source_id and source not in ('CROSSREF', 'PUBMED', 'FIXTURE'):
        result['source:' + source] = source_id
    return result


def token(value, prefix):
    return prefix + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def observations_from(document):
    """Read v1 provider records preferentially: merged v1 rows are not raw counts."""
    from .references import reference_record
    observations = []
    for entry in document['papers']:
        providers = entry.get('providers', [])
        if any('records' in p for p in providers):
            records = [(state, record) for state in providers for record in state.get('records', [])]
        else:
            records = [({}, record) for record in entry.get('references', [])]
        occurrence = {}
        for state, legacy in records:
            for evidence in legacy.get('observations', []):
                provider = evidence.get('provider') or state.get('provider') or 'unknown'
                occurrence[provider] = occurrence.get(provider, 0) + 1
                raw = deepcopy(evidence.get('raw') or {})
                parsed = reference_record(raw, provider, evidence.get('position', occurrence[provider]))
                # A legacy parsed field can fill metadata, never overwrite supplied IDs.
                for field in FIELDS + ('citation_text',):
                    if not parsed.get(field) and legacy.get(field):
                        parsed[field] = deepcopy(legacy[field])
                ids = publication_identifiers(raw)
                if not ids:
                    ids = publication_identifiers({k: v for k, v in legacy.items() if k != 'id'})
                variants = {}
                for kind, aliases in (('doi', ('doi',)), ('pmid', ('pmid', 'pubmed')), ('pmcid', ('pmcid', 'pmc'))):
                    values = {identifier(kind, v) for k, v in raw.items() if str(k).lower() in aliases} - {''}
                    if str(raw.get('source') or '').upper() == {'pmid': 'MED', 'pmcid': 'PMC'}.get(kind):
                        values.add(identifier(kind, raw.get('id')))
                        values.discard('')
                    if len(values) > 1:
                        variants[kind] = sorted(values)
                observations.append({'id': token([entry['paper_id'], provider, occurrence[provider]], 'obs-'),
                    'seed_paper_id': entry['paper_id'], 'direction': document.get('kind', 'references'),
                    'provider': provider, 'position': evidence.get('position'),
                    'retrieved_at': evidence.get('retrieved_at') or state.get('retrieved_at'),
                    'raw_citation_text': parsed.get('citation_text') or None,
                    'supplied_identifiers': {k: v for k, v in raw.items() if str(k).lower() in
                        ('doi', 'pmid', 'pubmed', 'pmcid', 'pmc', 'source', 'id')},
                    'identifiers': ids, 'metadata': {k: parsed.get(k) or None for k in FIELDS},
                    'raw': raw, 'provenance': deepcopy(evidence),
                    'resolution_status': 'unresolved', 'publication_id': None,
                    'matching_evidence': [], 'candidates': []})
                observations[-1]['identifier_conflicts'] = variants
    return observations


def compatible(left, right):
    return all(k not in right or right[k] == value for k, value in left.items())


def bibliography_evidence(observation, record):
    """Conservative field comparisons for review; no fuzzy match is auto-accepted."""
    metadata, text = observation['metadata'], observation.get('raw_citation_text') or ''
    norm = lambda v: re.sub(r'[^\w]+', ' ', str(v or '').casefold()).strip()
    haystack = norm(text)
    evidence = {}
    for field in ('title', 'year', 'volume', 'journal'):
        a, b = norm(metadata.get(field)), norm(record.get(field))
        evidence[field] = bool(b and (a == b if a else b in haystack))
    a = norm(metadata.get('pages')).split(' ')[0] if metadata.get('pages') else ''
    b = norm(record.get('pages')).split(' ')[0] if record.get('pages') else ''
    evidence['first_page'] = bool(b and (a == b if a else re.search(r'\b' + re.escape(b) + r'\b', haystack)))
    author = re.split(r'[,; ]', str(record.get('authors') or ''))[0]
    evidence['first_author'] = bool(norm(author) and norm(author) in norm(metadata.get('authors') or text))
    evidence['score'] = sum(bool(v) for v in evidence.values())
    return evidence


def canonicalize(document, report, library=()):
    """Attach a v2 identity layer without discarding v1 provider checkpoints."""
    observations = observations_from(document)
    pool, index, conflicts = [], {}, []

    def add(record, evidence, library_id=None):
        ids = publication_identifiers(record)
        if not ids:
            return None
        hits = {i for key in ids.items() for i in index.get(key, set()) if pool[i] is not None}
        combined = dict(ids)
        consistent = True
        for i in sorted(hits):
            if not compatible(combined, pool[i]['identifiers']):
                consistent = False
            combined.update(pool[i]['identifiers'])
        # A source-qualified MED/PMC identifier must agree with its global ID.
        for source, kind in (('MED', 'pmid'), ('PMC', 'pmcid')):
            if ids.get('source:' + source) and ids.get(kind) != ids['source:' + source]:
                consistent = False
        if not consistent:
            issue = {'record_identifiers': ids, 'existing_identifiers': [pool[i]['identifiers'] for i in sorted(hits)],
                     'evidence': evidence, 'status': 'pending_review'}
            conflicts.append(issue)
            for i in hits:
                pool[i]['identifier_conflicts'].append(issue)
            return None
        target = min(hits) if hits else len(pool)
        if not hits:
            pool.append({'identifiers': {}, **{k: None for k in FIELDS}, 'library_ids': [],
                         'metadata_evidence': [], 'identifier_conflicts': [], 'observation_ids': []})
        current = pool[target]
        for other in sorted(hits - {target}):
            extra = pool[other]
            for key in ('library_ids', 'metadata_evidence', 'identifier_conflicts', 'observation_ids'):
                current[key].extend(v for v in extra[key] if v not in current[key])
            for field in FIELDS:
                if not current[field]:
                    current[field] = extra[field]
            pool[other] = None
            for values in index.values():
                if other in values:
                    values.discard(other)
                    values.add(target)
        current['identifiers'].update(combined)
        for field in FIELDS:
            if not current[field] and record.get(field):
                current[field] = deepcopy(record[field])
        current['metadata_evidence'].append(evidence)
        if library_id is not None and library_id not in current['library_ids']:
            current['library_ids'].append(library_id)
        for key in combined.items():
            index.setdefault(key, set()).add(target)
        return target

    # Library records bridge identifiers before provider observations are counted.
    anchors = {p['id']: p for p in [*report['papers'], *library]}
    for paper in anchors.values():
        add(paper, {'method': 'existing_library', 'paper_id': paper['id'],
                    'provenance': paper.get('metadata_provenance', {})}, paper['id'])
    for item in document.get('metadata_records', {}).values():
        add(item['record'], {k: deepcopy(v) for k, v in item.items() if k != 'record'})
    for observation in observations:
        if observation['identifier_conflicts']:
            conflicts.append({'status': 'pending_review', 'observation_id': observation['id'],
                              'conflicting_supplied_identifiers': observation['identifier_conflicts']})
            continue
        add({**observation['metadata'], **observation['identifiers']},
            {'method': 'supplied_identifiers', 'observation_id': observation['id'],
             'provider': observation['provider'], 'retrieved_at': observation['retrieved_at']})
    for publication in filter(None, pool):
        publication['id'] = token(sorted(publication['identifiers'].items()), 'pub-')
        publication['in_library'] = bool(publication['library_ids'])
        publication['links'] = {k: v for k, v in (
            ('doi', 'https://doi.org/' + publication['identifiers']['doi'] if publication['identifiers'].get('doi') else None),
            ('pubmed', 'https://pubmed.ncbi.nlm.nih.gov/' + publication['identifiers']['pmid'] + '/' if publication['identifiers'].get('pmid') else None),
            ('pmc', 'https://pmc.ncbi.nlm.nih.gov/articles/' + publication['identifiers']['pmcid'] + '/' if publication['identifiers'].get('pmcid') else None))}
        for kind in ('doi', 'pmid', 'pmcid'):
            publication[kind] = publication['identifiers'].get(kind)
    relationships = {}
    decisions = document.get('resolution_tasks', {})
    for observation in observations:
        ids = observation['identifiers']
        hits = {i for key in ids.items() for i in index.get(key, set()) if pool[i] is not None}
        candidates = [pool[i] for i in hits if compatible(ids, pool[i]['identifiers']) and not pool[i]['identifier_conflicts']]
        if observation['identifier_conflicts']:
            observation['resolution_status'] = 'conflict'
            observation['matching_evidence'] = [{'method': 'conflicting_supplied_identifiers',
                                                 'identifiers': observation['identifier_conflicts']}]
        elif len(candidates) == 1 and len(hits) == 1:
            publication = candidates[0]
            observation['publication_id'], observation['resolution_status'] = publication['id'], 'resolved'
            observation['matching_evidence'] = [{'method': 'exact_identifier', 'shared_identifiers':
                {k: v for k, v in ids.items() if publication['identifiers'].get(k) == v}}]
            publication['observation_ids'].append(observation['id'])
        elif hits:
            observation['resolution_status'] = 'conflict'
            observation['matching_evidence'] = [{'method': 'identifier_conflict', 'identifiers': ids}]
        else:
            task = decisions.get('bibliography:' + token([observation['metadata'], observation['raw_citation_text']], ''))
            candidates = task.get('candidates', []) if task else []
            observation['candidates'] = deepcopy(candidates)
            if candidates:
                observation['resolution_status'] = 'ambiguous' if len(candidates) > 1 else 'review_required'
                observation['matching_evidence'] = [{'method': 'bibliographic_candidates', 'auto_accepted': False}]
        source_keys = publication_identifiers(anchors.get(observation['seed_paper_id'], {}))
        seed_hits = {i for key in source_keys.items() for i in index.get(key, set()) if pool[i] is not None}
        source_id = pool[next(iter(seed_hits))]['id'] if len(seed_hits) == 1 else None
        observation['seed_publication_id'] = source_id
        observation['citing_publication_id'] = source_id if observation['direction'] == 'references' else observation['publication_id']
        observation['cited_publication_id'] = observation['publication_id'] if observation['direction'] == 'references' else source_id
        if observation['publication_id'] and source_id:
            citing, cited = ((source_id, observation['publication_id']) if observation['direction'] == 'references'
                             else (observation['publication_id'], source_id))
            observation['citing_publication_id'] = citing
            observation['cited_publication_id'] = cited
            key = (citing, cited)
            relationship = relationships.setdefault(key, {'citing': citing, 'cited': cited,
                'observation_ids': [], 'providers': [], 'seed_paper_ids': [],
                'status': 'self_link_pending_review' if citing == cited else 'resolved'})
            relationship['observation_ids'].append(observation['id'])
            for field, value in (('providers', observation['provider']), ('seed_paper_ids', observation['seed_paper_id'])):
                if value not in relationship[field]:
                    relationship[field].append(value)
    for entry in document['papers']:
        for state in entry.get('providers', []):
            state.setdefault('pagination_status', 'not_available' if state['status'] == 'unavailable' else
                'partial' if state['status'] in ('partial', 'failed') else 'in_progress' if state.get('next_page') else
                'limited' if state['status'] == 'limited' else 'complete')
    document.update(schema_version=2, observations=observations, publications=list(filter(None, pool)),
                    relationships=list(relationships.values()), identifier_conflicts=conflicts,
                    resolution_conflicts=[t['identifier_conflict'] for t in decisions.values() if t.get('identifier_conflict')],
                    search_context=deepcopy(report.get('search', {})), search_provenance={k: deepcopy(report[k]) for k in
                        ('comparison', 'merge') if k in report},
                    compatibility={'papers.references': 'Legacy groups retained for compatibility, never a distinct publication count.'})
    requested = len(document['scope'].get('papers', report['papers']))
    document['coverage'] = {'papers_requested': requested,
        'papers_processed': sum(e['status'] not in ('running', 'retrieving') for e in document['papers']),
        'raw_observations': len(observations),
        'resolved_distinct_publications': len({o['publication_id'] for o in observations if o['resolution_status'] == 'resolved'}),
        'unresolved_observations': sum(o['resolution_status'] == 'unresolved' for o in observations),
        'ambiguous_matches': sum(o['resolution_status'] == 'ambiguous' for o in observations),
        'review_required_observations': sum(o['resolution_status'] == 'review_required' for o in observations),
        'conflicting_observations': sum(o['resolution_status'] == 'conflict' for o in observations),
        'distinct_citation_edges': sum(r['status'] == 'resolved' for r in relationships.values()),
        'flagged_self_links': sum(r['status'] == 'self_link_pending_review' for r in relationships.values()),
        'retrieval_status': document.get('status', 'unknown'), 'bibliography_completeness': 'unknown',
        'resolution_status': document.get('resolution_status', 'not_requested'),
        'policy': 'Distinct counts use compatible publication identifiers, not summed identifier or observation counts. Counts may consolidate further after metadata bridging. Provider completion is not bibliography completeness.'}
    return document


def publication_export(document):
    """One bibliographic row per referenced identity; unresolved evidence stays separate."""
    referenced = {o['publication_id'] for o in document['observations'] if o['publication_id']}
    return {'schema_version': 2, 'kind': 'referenced_publications', 'scope': document['scope'],
            'search_context': document['search_context'], 'search_provenance': document['search_provenance'],
            'coverage': document['coverage'], 'publications': [p for p in document['publications'] if p['id'] in referenced],
            'unresolved_observations': [o for o in document['observations'] if o['resolution_status'] != 'resolved'],
            'relationships': document['relationships'], 'identifier_conflicts': document['identifier_conflicts']}
