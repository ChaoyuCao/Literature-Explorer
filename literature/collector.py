"""Metadata-only search with auditable database ordering and provenance."""
import re
import json
from .sources import search_query


class Collector:
    def __init__(self, store, sources, progress=None):
        self.store, self.sources, self.progress = store, sources, progress

    def search(self, provider, query, limit=None, sort="relevance", filters=None, resume_id=None):
        if not isinstance(query, str) or not query.strip() or (limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1)):
            raise ValueError("Provide a nonempty query and a positive limit.")
        if provider not in ("pubmed", "europepmc", "crossref"):
            raise ValueError("Provider must be pubmed, europepmc, or crossref.")
        if sort not in ("relevance", "date"):
            raise ValueError("Sort must be relevance or date.")
        if filters is not None and not isinstance(filters, dict):
            raise ValueError('Search filters must be an object.')
        if provider == 'crossref' and not (filters or {}).get('article_type'):
            filters = {**(filters or {}), 'article_type': 'journal-article'}
        if provider == "europepmc" and re.search(r"\bsort_\w+:", query, re.I):
            raise ValueError("Use --sort relevance/date instead of sort clauses in the Europe PMC query.")
        original_query = query
        query = search_query(provider, query, filters)
        checkpoint, count, total = None, 0, None
        if resume_id is not None:
            job = self.store.search_job(resume_id)
            if job['status'] not in {'failed', 'interrupted', 'running'}:
                raise ValueError('Only failed or interrupted searches can be resumed. Start a new search to change its scope.')
            job_id = job['id']
            checkpoint = json.loads(job['checkpoint']) if job['checkpoint'] else None
            count = self.store.db.execute('SELECT COUNT(*) FROM discoveries WHERE job_id=?', (job_id,)).fetchone()[0]
            if count and not checkpoint:
                raise ValueError('This older search has no resumable checkpoint. Run a new search with the same query.')
            total = job['total']
            with self.store.transaction():
                self.store.db.execute("UPDATE jobs SET status='running',error=NULL,finished=NULL,attempts=COALESCE(attempts,1)+1 WHERE id=?", (job_id,))
        else:
            job_id = self.store.job("search", provider, query, sort, {'original_query': original_query, 'filters': filters or {}, 'limit': limit})
        self.last_job_id = job_id
        try:
            if self.progress:
                self.progress(f'Search {job_id}: {provider}; {count} records already saved.')
            if total is not None and (count >= total or limit and count >= limit):
                iterator = ()
            elif provider == 'crossref':
                iterator = self.sources.search_crossref(query, limit, sort, checkpoint, filters)
            else:
                method = self.sources.search_pubmed if provider == 'pubmed' else self.sources.search_epmc
                iterator = method(query, limit, sort, checkpoint) if checkpoint else method(query, limit, sort)
            for page in iterator:
                next_count = count
                with self.store.transaction():
                    self.store.page(job_id, page["raw"])
                    total = page["total"]
                    for record in page["records"]:
                        if page.get('response_info', {}).get('fetched_at'):
                            record = {**record, 'provider_retrieved_at': page['response_info']['fetched_at']}
                        ident = self.store.upsert(record, provider)
                        self.store.discover(ident, job_id, "search", rank=next_count + 1)
                        next_count += 1
                    self.store.checkpoint(job_id, page.get('checkpoint'))
                    self.store.db.execute('UPDATE jobs SET retrieved=?,total=? WHERE id=?', (next_count, total, job_id))
                count = next_count
                if self.progress:
                    self.progress(f'Search {job_id}: {count} records saved / {total} database hits.')
            status = "limited" if total is not None and count < total else "complete"
            self.store.finish(job_id, status, count, total)
            return {"job_id": job_id, "status": status, "retrieved_records": count,
                    "reported_total": total, "paper_ids": [p['id'] for p in self.store.search_export(job_id)['papers']]}
        except BaseException as exc:
            self.store.finish(job_id, "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", count, total, str(exc))
            raise

    def resume(self, job_id):
        job = self.store.search_job(job_id)
        parameters = json.loads(job['parameters'] or '{}')
        return self.search(job['source'], parameters.get('original_query', job['query']),
                           parameters.get('limit'), job['sort_order'] or 'relevance',
                           parameters.get('filters'), resume_id=job_id)

    def search_many(self, providers, query, limit=None, sort='relevance', filters=None):
        """Continue across provider failures; each database keeps its own saved job."""
        providers = list(dict.fromkeys(providers))
        if not providers or any(p not in ('pubmed', 'europepmc', 'crossref') for p in providers):
            raise ValueError('Select supported databases: pubmed, europepmc, crossref.')
        if not isinstance(query, str) or not query.strip() or (limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1)):
            raise ValueError('Provide a nonempty query and a positive per-database limit.')
        if sort not in ('relevance', 'date'):
            raise ValueError('Sort must be relevance or date.')
        if filters is not None and not isinstance(filters, dict):
            raise ValueError('Search filters must be an object.')
        if 'europepmc' in providers and re.search(r'\bsort_\w+:', query, re.I):
            raise ValueError('Use --sort relevance/date instead of sort clauses in the Europe PMC query.')
        for provider in providers:
            search_query(provider, query, filters)
        self.multi_search_ids = []
        results = []
        for provider in providers:
            self.last_job_id = None
            try:
                item = self.search(provider, query, limit, sort, filters)
            except (ValueError, RuntimeError, OSError) as exc:
                item = {'job_id': self.last_job_id, 'status': 'failed', 'error': str(exc)}
            finally:
                if self.last_job_id is not None:
                    self.multi_search_ids.append(self.last_job_id)
            results.append({'source': provider, **item})
        # A later provider can bridge identifiers and replace an earlier catalogue ID.
        for item in results:
            if item['job_id'] is not None:
                item['paper_ids'] = [p['id'] for p in self.store.search_export(item['job_id'])['papers']]
        status = 'partial' if any(r['status'] == 'failed' for r in results) else 'limited' if any(r['status'] == 'limited' for r in results) else 'complete'
        return {'searches': results, 'status': status}

    def enrich(self, paper_ids):
        results = []
        for paper_id in paper_ids:
            paper = self.store.get(paper_id)
            identifier = paper.get('doi') or paper.get('pmid') or paper.get('pmcid')
            if not identifier:
                results.append({'id': paper_id, 'status': 'skipped', 'reason': 'No supported identifier'})
                continue
            try:
                record, provider = self.sources.lookup(identifier)
                # Fill missing fields; preserve existing values and their field-level provenance.
                missing = {k: v for k,v in record.items() if paper.get(k) in ('', None, [], {})}
                for key in ('doi', 'pmid', 'pmcid'):
                    if record.get(key):
                        missing[key] = record[key]
                if record.get('provider_retrieved_at'):
                    missing['provider_retrieved_at'] = record['provider_retrieved_at']
                updated_id = self.store.upsert(missing, provider)
                results.append({'id': updated_id, 'status': 'enriched', 'fields': sorted(missing)})
            except (ValueError, RuntimeError, OSError) as exc:
                results.append({'id': paper_id, 'status': 'failed', 'error': str(exc)})
        return results

