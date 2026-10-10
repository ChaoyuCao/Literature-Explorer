"""Optional cached, resumable metadata enrichment; never an LLM dependency."""
from xml.etree.ElementTree import ParseError

from .progress import emit_progress
from .publications import (bibliography_evidence, canonicalize, compatible,
                           publication_identifiers, token)
from .references import write_json
from .sources import NCBI, pubmed_records
from .store import now


def resolve_references(document, report, store, sources, path, progress=None):
    library = store.list_papers()
    canonicalize(document, report, library)
    tasks = document.setdefault('resolution_tasks', {})
    records = document.setdefault('metadata_records', {})
    document['resolution_status'] = 'running'
    identified = [p for p in document['publications'] if p['observation_ids']]
    complete_library = {key for p in identified if p['in_library'] and p['title'] and p['journal'] and p['year']
                        for key in p['identifiers'].items()}
    pmids = sorted({p['pmid'] for p in identified if p.get('pmid') and
                    not set(p['identifiers'].items()) & complete_library}, key=int)
    work = [('pubmed:' + token(pmids[i:i+100], ''), 'pubmed', pmids[i:i+100]) for i in range(0, len(pmids), 100)]
    work += [('identity:' + token(p['identifiers'], ''), 'identity', p['identifiers'])
             for p in identified if not p.get('pmid') and not set(p['identifiers'].items()) & complete_library]
    citation_tasks = {}
    for observation in document['observations']:
        if observation['identifiers']:
            continue
        text = observation.get('raw_citation_text') or ' '.join(str(v) for v in observation['metadata'].values() if v)
        if text:
            key = 'bibliography:' + token([observation['metadata'], observation['raw_citation_text']], '')
            citation_tasks.setdefault(key, (observation, text))
    work += [(key, 'bibliography', data) for key, data in citation_tasks.items()]
    done = sum(tasks.get(k, {}).get('status') == 'complete' for k, _, _ in work)

    def save_record(record, method, query):
        ids = publication_identifiers(record)
        records[token([method, query, ids], 'metadata-')] = {'record': record, 'method': method, 'query': query,
            'retrieved_at': record.get('provider_retrieved_at') or now(),
            'response_info': dict(getattr(sources.http, 'response_info', {}))}

    def checkpoint():
        canonicalize(document, report, library)
        write_json(path, document)

    try:
        emit_progress(progress, 'resolution', done, len(work), status='running')
        for key, kind, value in work:
            if tasks.get(key, {}).get('status') == 'complete':
                continue
            if progress:
                progress('Resolution: ' + kind + ', task ' + str(done + 1) + '/' + str(len(work)) + '.')
            task = tasks[key] = {'status': 'running', 'method': kind, 'errors': [], 'candidates': []}
            try:
                if kind == 'pubmed':
                    payload = sources.http.get(NCBI + '/efetch.fcgi', {'db': 'pubmed', 'id': ','.join(value),
                        'retmode': 'xml', 'tool': 'mechanosensor_literature'})
                    rows = list(pubmed_records(payload))
                    requested = set(value)
                    if any(r['pmid'] not in requested for r in rows):
                        raise ValueError('PubMed metadata includes an unrequested PMID.')
                    for record in rows:
                        save_record(sources.stamp(record), 'pubmed_identifier_bridge', record['pmid'])
                    missing = requested - {r['pmid'] for r in rows}
                    if missing:
                        raise ValueError('PubMed omitted requested identifiers: ' + ', '.join(sorted(missing)))
                elif kind == 'identity':
                    reused = next((p for p in document['publications'] if p['title'] and p['year'] and p['journal']
                        and compatible(value, p['identifiers']) and set(value.items()) & set(p['identifiers'].items())
                        and any(e['method'] != 'supplied_identifiers' for e in p['metadata_evidence'])), None)
                    if reused:
                        task.update(status='complete', method='reused_metadata', publication_id=reused['id'])
                        done += 1
                        emit_progress(progress, 'resolution', done, len(work), status='running',
                                      resolved=document['coverage']['resolved_distinct_publications'])
                        continue
                    try:
                        rows = sources.identifier_metadata(value)
                    except (ValueError, RuntimeError, OSError) as exc:
                        rows = []
                        task.setdefault('provider_warnings', []).append(str(exc))
                    valid = [r for r in rows if set(publication_identifiers(r).items()) & set(value.items())]
                    if len(valid) > 1:
                        task['candidates'] = [{'record': r, 'evidence': {'method': 'identifier_lookup'}} for r in valid]
                        raise ValueError('Identifier lookup returned multiple publications; review required.')
                    if valid:
                        if not compatible(value, publication_identifiers(valid[0])):
                            task['identifier_conflict'] = {'supplied': value, 'returned': publication_identifiers(valid[0])}
                            task['candidates'] = [{'record': valid[0], 'evidence': {'method': 'conflicting_identifier_response'}}]
                            raise ValueError('Metadata identifiers conflict with supplied reference identifiers.')
                        save_record(valid[0], 'europepmc_identifier_bridge', value)
                    else:
                        lookup = value.get('doi') or value.get('pmcid') or value.get('pmid')
                        if not lookup:
                            raise ValueError('No supported metadata lookup for this source ID.')
                        record, provider = sources.lookup(lookup)
                        if not compatible(value, publication_identifiers(record)):
                            raise ValueError('Metadata identifiers conflict with supplied reference identifiers.')
                        save_record(record, provider + '_exact_identifier', value)
                else:
                    observation, text = value
                    candidates = []
                    local = [p for p in document['publications'] if p['title']]
                    for record in local:
                        evidence = bibliography_evidence(observation, record)
                        if evidence['title'] or evidence['score'] >= 3:
                            candidates.append({'record': record, 'method': 'local_bibliographic_candidate', 'evidence': evidence})
                    task['candidates'] = candidates
                    remote = sources.bibliography_candidates(text)
                    for record in remote:
                        candidates.append({'record': record, 'method': 'crossref_bibliographic_candidate',
                                           'evidence': bibliography_evidence(observation, record)})
                    # The same candidate can occur in both library and provider results.
                    unique = {}
                    for candidate in candidates:
                        ids = publication_identifiers(candidate['record'])
                        if ids:
                            signature = token(ids, '')
                            existing = unique.setdefault(signature, {**candidate, 'supporting_methods': []})
                            existing['supporting_methods'].append(candidate['method'])
                    task['candidates'] = sorted(unique.values(), key=lambda c: (-c['evidence']['score'],
                                                str(c['record'].get('doi') or c['record'].get('id'))))[:10]
                task['status'] = 'complete'
            except (ValueError, RuntimeError, OSError, ParseError) as exc:
                task['status'] = 'failed'
                task['errors'].append(str(exc))
            done += 1
            checkpoint()
            emit_progress(progress, 'resolution', done, len(work), status='running',
                          resolved=document['coverage']['resolved_distinct_publications'])
        document['resolution_status'] = 'partial' if any(tasks[k]['status'] == 'failed' for k, _, _ in work) else 'complete'
    except KeyboardInterrupt:
        document['resolution_status'] = 'interrupted'
        raise
    finally:
        checkpoint()
    emit_progress(progress, 'resolution', len(work), len(work), status=document['resolution_status'],
                  resolved=document['coverage']['resolved_distinct_publications'])
    return document
