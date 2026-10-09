"""SQLite search catalogue with immutable source observations."""
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from . import __version__
from .sources import doi, epmc_record, pubmed_records, crossref_record


def now():
    return datetime.now(timezone.utc).isoformat()


def identities(record):
    out = {}
    for key in ("doi", "pmid", "pmcid"):
        value = str(record.get(key) or "").strip()
        if value:
            out[key] = doi(value) if key == "doi" else value.upper() if key == "pmcid" else value
    if record.get("source") and record.get("source_id"):
        out["epmc:" + record["source"]] = str(record["source_id"])
    return out


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self._depth = 0
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS papers(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS aliases(kind TEXT, value TEXT, paper_id INTEGER REFERENCES papers(id),
          PRIMARY KEY(kind,value));
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, kind TEXT, source TEXT, query TEXT,
          started TEXT, finished TEXT, status TEXT, total INTEGER, retrieved INTEGER DEFAULT 0, error TEXT);
        CREATE TABLE IF NOT EXISTS raw_pages(id INTEGER PRIMARY KEY, job_id INTEGER REFERENCES jobs(id), payload TEXT);
        CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, paper_id INTEGER REFERENCES papers(id),
          provider TEXT, retrieved TEXT, record TEXT);
        CREATE TABLE IF NOT EXISTS discoveries(id INTEGER PRIMARY KEY, paper_id INTEGER REFERENCES papers(id),
          job_id INTEGER REFERENCES jobs(id), via TEXT, parent_id INTEGER REFERENCES papers(id));
        CREATE TABLE IF NOT EXISTS library_notes(paper_id INTEGER PRIMARY KEY REFERENCES papers(id),
          note TEXT DEFAULT '', favorite INTEGER DEFAULT 0, status TEXT DEFAULT 'unreviewed', updated TEXT);
        CREATE TABLE IF NOT EXISTS paper_tags(paper_id INTEGER REFERENCES papers(id), tag TEXT,
          PRIMARY KEY(paper_id,tag));
        CREATE TABLE IF NOT EXISTS collections(name TEXT PRIMARY KEY, created TEXT);
        CREATE TABLE IF NOT EXISTS paper_collections(paper_id INTEGER REFERENCES papers(id),
          collection TEXT REFERENCES collections(name), PRIMARY KEY(paper_id,collection));
        CREATE TABLE IF NOT EXISTS saved_queries(name TEXT PRIMARY KEY, specification TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS catalogue_settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        # Add search metadata without deleting anything from an older catalogue.
        for table, column, kind in (("jobs", "sort_order", "TEXT"),
                                    ("discoveries", "rank", "INTEGER"),
                                    ("discoveries", "observation_id", "INTEGER"),
                                    ("jobs", "parameters", "TEXT"), ("jobs", "checkpoint", "TEXT"),
                                    ("jobs", "attempts", "INTEGER DEFAULT 1")):
            if column not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self.db.commit()
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO catalogue_settings VALUES('library_id',?)", (str(uuid.uuid4()),))
        self.library_id = self.db.execute("SELECT value FROM catalogue_settings WHERE key='library_id'").fetchone()[0]

    @contextmanager
    def transaction(self):
        self._depth += 1
        try:
            yield
            if self._depth == 1:
                self.db.commit()
        except BaseException:
            if self._depth == 1:
                self.db.rollback()
            raise
        finally:
            self._depth -= 1

    def annotation(self, paper_id):
        row = self.db.execute('SELECT * FROM library_notes WHERE paper_id=?', (paper_id,)).fetchone()
        return {'note': row['note'] if row else '', 'favorite': bool(row['favorite']) if row else False,
                'status': row['status'] if row else 'unreviewed',
                'updated': row['updated'] if row else None,
                'tags': [r[0] for r in self.db.execute('SELECT tag FROM paper_tags WHERE paper_id=? ORDER BY tag', (paper_id,))],
                'collections': [r[0] for r in self.db.execute('SELECT collection FROM paper_collections WHERE paper_id=? ORDER BY collection', (paper_id,))]}

    def annotate(self, paper_id, note=None, favorite=None, status=None, tags=(), remove_tags=()):
        self.get(paper_id)
        if note is not None and not isinstance(note, str) or favorite is not None and not isinstance(favorite, bool):
            raise ValueError('Notes must be text and favorite must be a boolean.')
        if any(not isinstance(tag, str) or not tag.strip() for tag in list(tags) + list(remove_tags)):
            raise ValueError('Tags must be nonempty text.')
        if status is not None and status not in {'unreviewed', 'included', 'excluded', 'uncertain'}:
            raise ValueError('Review status must be unreviewed, included, excluded, or uncertain.')
        old = self.annotation(paper_id)
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO library_notes VALUES(?,?,?,?,?)',
                            (paper_id, old['note'] if note is None else note,
                             old['favorite'] if favorite is None else bool(favorite), status or old['status'], now()))
            for tag in tags:
                if not tag.strip():
                    raise ValueError('Tags cannot be empty.')
                self.db.execute('INSERT OR IGNORE INTO paper_tags VALUES(?,?)', (paper_id, tag.strip()))
            for tag in remove_tags:
                self.db.execute('DELETE FROM paper_tags WHERE paper_id=? AND tag=?', (paper_id, tag.strip()))
        return self.annotation(paper_id)

    def import_annotations(self, data, overwrite=False):
        if not isinstance(data, dict) or data.get('library_id') != self.library_id or not isinstance(data.get('annotations'), list):
            raise ValueError('Annotation file must belong to this catalogue and contain an annotations list.')
        with self.transaction():
            for item in data['annotations']:
                if not isinstance(item, dict) or not isinstance(item.get('id'), int) or not isinstance(item.get('library'), dict):
                    raise ValueError('Each annotation needs an integer paper ID and a library object.')
                ident, annotation = item['id'], item['library']
                self.get(ident)
                current = self.annotation(ident)
                if not overwrite and current['updated'] and current['updated'] != item.get('base_updated'):
                    raise ValueError(f'Paper {ident} annotations changed after this report was generated. Rebuild the report or explicitly use --overwrite.')
                desired_tags = annotation.get('tags', [])
                if not isinstance(desired_tags, list):
                    raise ValueError('Annotation tags must be a list.')
                self.annotate(ident, annotation.get('note', ''), annotation.get('favorite', False), annotation.get('status', 'unreviewed'), desired_tags,
                              [t for t in current['tags'] if t not in desired_tags])
        return {'updated_papers': len(data['annotations'])}

    def collection(self, name, add=(), remove=(), rename=None, delete=False):
        if not name.strip():
            raise ValueError('Collection name cannot be empty.')
        name = name.strip()
        with self.transaction():
            existing = self.db.execute('SELECT created FROM collections WHERE name=?', (name,)).fetchone()
            if rename or delete:
                if not existing or rename and delete or add or remove:
                    raise ValueError('Select an existing collection and one management operation.')
                if delete:
                    self.db.execute('DELETE FROM paper_collections WHERE collection=?', (name,))
                    self.db.execute('DELETE FROM collections WHERE name=?', (name,))
                    return {'deleted_collection': name, 'papers_preserved': True}
                rename = rename.strip()
                if not rename or self.db.execute('SELECT 1 FROM collections WHERE name=?', (rename,)).fetchone():
                    raise ValueError('The new collection name must be nonempty and unused.')
                self.db.execute('INSERT INTO collections VALUES(?,?)', (rename, existing[0]))
                self.db.execute('UPDATE paper_collections SET collection=? WHERE collection=?', (rename, name))
                self.db.execute('DELETE FROM collections WHERE name=?', (name,))
                name = rename
            self.db.execute('INSERT OR IGNORE INTO collections VALUES(?,?)', (name, now()))
            for ident in add:
                self.get(ident)
                self.db.execute('INSERT OR IGNORE INTO paper_collections VALUES(?,?)', (ident, name))
            for ident in remove:
                self.db.execute('DELETE FROM paper_collections WHERE paper_id=? AND collection=?', (ident, name))
        return {'name': name, 'paper_ids': [r[0] for r in self.db.execute('SELECT paper_id FROM paper_collections WHERE collection=? ORDER BY paper_id', (name,))]}

    def list_papers(self, ids=None, collection=None, tag=None, favorite=False, status=None, text=None,
                    author=None, journal=None, year_from=None, year_to=None, article_type=None):
        wanted = set(ids) if ids is not None else None
        result = []
        for row in self.db.execute('SELECT id FROM papers ORDER BY id'):
            if wanted is not None and row[0] not in wanted:
                continue
            paper = self.get(row[0])
            annotation = self.annotation(row[0])
            if collection and collection not in annotation['collections'] or tag and tag not in annotation['tags']:
                continue
            if favorite and not annotation['favorite'] or status and status != annotation['status']:
                continue
            if any(value and value.casefold() not in str(paper.get(field, '')).casefold()
                   for field, value in [('authors', author), ('journal', journal)]):
                continue
            if text and text.casefold() not in (paper.get('title', '') + ' ' + paper.get('abstract', '')).casefold():
                continue
            year = str(paper.get('year') or '')
            if (year_from or year_to) and (not year.isdigit() or year_from and int(year) < year_from or year_to and int(year) > year_to):
                continue
            if article_type and article_type.casefold() not in {t.casefold() for t in paper.get('publication_types', [])}:
                continue
            result.append({**paper, 'library': annotation})
        return result

    def save_query(self, name, specification):
        if not name.strip():
            raise ValueError('Saved query name cannot be empty.')
        from .sources import search_query
        if not isinstance(specification, dict) or not isinstance(specification.get('query'), str) or not specification['query'].strip():
            raise ValueError('A saved query needs nonempty query text.')
        provider = specification.get('source') or 'pubmed'
        if provider not in {'pubmed', 'europepmc', 'crossref'} or specification.get('sort', 'relevance') not in {'relevance', 'date'}:
            raise ValueError('Unsupported saved query source or sort order.')
        providers = specification.get('sources', [provider])
        if not isinstance(providers, list) or not providers or any(p not in {'pubmed', 'europepmc', 'crossref'} for p in providers):
            raise ValueError('Saved query sources must be a nonempty list of supported databases.')
        limit = specification.get('limit')
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError('Saved query limit must be a positive integer.')
        for selected_provider in providers:
            search_query(selected_provider, specification['query'], specification.get('filters'))
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO saved_queries VALUES(?,?,?)', (name.strip(), json.dumps(specification), now()))

    def query(self, name):
        row = self.db.execute('SELECT specification FROM saved_queries WHERE name=?', (name,)).fetchone()
        if not row:
            raise ValueError(f'Unknown saved query: {name}')
        return json.loads(row[0])

    def checkpoint(self, job_id, token):
        self.db.execute('UPDATE jobs SET checkpoint=? WHERE id=?', (json.dumps(token), job_id))

    def search_job(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE kind='search' AND id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError(f'Unknown search ID: {job_id}')
        return dict(row)

    def get(self, paper_id):
        row = self.db.execute("SELECT data FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not row:
            raise ValueError(f"Unknown paper ID: {paper_id}")
        return {"id": paper_id, **json.loads(row["data"])}

    def _compatible(self, records):
        for key in ("doi", "pmid", "pmcid"):
            values = {identities(r).get(key) for r in records} - {None}
            if len(values) > 1:
                raise ValueError(f"Conflicting {key} identifiers; records were not merged.")

    def _merge(self, source_id, target_id):
        if source_id == target_id:
            return target_id
        source, target = self.get(source_id), self.get(target_id)
        self._compatible([source, target])
        merged = {k: v for k, v in source.items() if k != "id"}
        merged.update({k: v for k, v in target.items() if k != "id" and v not in ("", None, [], {})})
        self.db.execute("UPDATE papers SET data=? WHERE id=?", (json.dumps(merged), target_id))
        left, right = self.annotation(source_id), self.annotation(target_id)
        statuses = {left['status'], right['status']} - {'unreviewed'}
        if any([left['note'], right['note'], left['favorite'], right['favorite'], statuses]):
            self.annotate(target_id, note='\n\n'.join(dict.fromkeys(filter(None, [right['note'], left['note']]))),
                          favorite=left['favorite'] or right['favorite'],
                          status=next(iter(statuses)) if len(statuses) == 1 else 'uncertain' if statuses else 'unreviewed')
        for table, column in [('paper_tags', 'tag'), ('paper_collections', 'collection')]:
            self.db.execute(f'INSERT OR IGNORE INTO {table} SELECT ?,{column} FROM {table} WHERE paper_id=?', (target_id, source_id))
            self.db.execute(f'DELETE FROM {table} WHERE paper_id=?', (source_id,))
        self.db.execute('DELETE FROM library_notes WHERE paper_id=?', (source_id,))
        existing = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("aliases", "observations", "discoveries", "links", "screenings", "downloads"):
            if table in existing:
                self.db.execute(f"UPDATE {table} SET paper_id=? WHERE paper_id=?", (target_id, source_id))
        self.db.execute("UPDATE discoveries SET parent_id=? WHERE parent_id=?", (target_id, source_id))
        if "edges" in existing:
            for field in ("citing", "cited"):
                self.db.execute(f"UPDATE edges SET {field}=? WHERE {field}=?", (target_id, source_id))
        self.db.execute("DELETE FROM papers WHERE id=?", (source_id,))
        return target_id

    def merge(self, source_id, target_id):
        with self.transaction():
            return self._merge(source_id, target_id)

    def upsert(self, record, provider):
        record = {k: v for k, v in record.items() if k != 'id'}
        if record.get('doi'):
            record['doi'] = doi(record['doi'])
        if record.get('pmcid'):
            record['pmcid'] = str(record['pmcid']).upper()
        if record.get('pmid'):
            record['pmid'] = str(record['pmid'])
        aliases = identities(record)
        matches = set()
        for kind, value in aliases.items():
            row = self.db.execute("SELECT paper_id FROM aliases WHERE kind=? AND value=?", (kind, value)).fetchone()
            if row:
                matches.add(row[0])
        self._compatible([record] + [self.get(i) for i in matches])
        with self.transaction():
            if matches:
                paper_id = min(matches)
                for other in matches - {paper_id}:
                    self._merge(other, paper_id)
                current = self.get(paper_id)
                current.pop("id", None)
                for key, value in record.items():
                    if value not in ("", None, [], {}) and key != 'metadata_provenance':
                        current[key] = value
            else:
                current = dict(record)
                paper_id = self.db.execute("INSERT INTO papers(data) VALUES(?)", ('{}',)).lastrowid
            provenance = dict(current.get('metadata_provenance') or {})
            for key, value in record.items():
                if value not in ('', None, [], {}) and key != 'metadata_provenance':
                    provenance[key] = {'provider': provider, 'retrieved_at': record.get('provider_retrieved_at') or now()}
            current['metadata_provenance'] = provenance
            self.db.execute('UPDATE papers SET data=? WHERE id=?', (json.dumps(current), paper_id))
            for kind, value in aliases.items():
                self.db.execute("INSERT OR IGNORE INTO aliases VALUES(?,?,?)", (kind, value, paper_id))
            self.db.execute("INSERT INTO observations(paper_id,provider,retrieved,record) VALUES(?,?,?,?)",
                            (paper_id, provider, now(), json.dumps(record)))
        return paper_id

    def job(self, kind, source, query, sort=None, parameters=None):
        with self.transaction():
            return self.db.execute("INSERT INTO jobs(kind,source,query,started,status,sort_order,parameters) VALUES(?,?,?,?,?,?,?)",
                                   (kind, source, query, now(), "running", sort, json.dumps(parameters or {}))).lastrowid

    def page(self, job_id, payload):
        with self.transaction():
            self.db.execute("INSERT INTO raw_pages(job_id,payload) VALUES(?,?)", (job_id, json.dumps(payload)))

    def finish(self, job_id, status, retrieved=0, total=None, error=None):
        with self.transaction():
            self.db.execute("UPDATE jobs SET finished=?,status=?,retrieved=?,total=?,error=? WHERE id=?",
                            (now(), status, retrieved, total, error, job_id))

    def discover(self, paper_id, job_id, via, parent_id=None, rank=None):
        observation = self.db.execute("SELECT MAX(id) FROM observations WHERE paper_id=?", (paper_id,)).fetchone()[0]
        with self.transaction():
            self.db.execute("INSERT INTO discoveries(paper_id,job_id,via,parent_id,rank,observation_id) VALUES(?,?,?,?,?,?)",
                            (paper_id, job_id, via, parent_id, rank, observation))

    def duplicates(self):
        titles = {}
        for row in self.db.execute("SELECT id,data FROM papers"):
            record = json.loads(row["data"])
            key = re.sub(r"\W+", "", record.get("title", "").casefold())
            if key:
                titles.setdefault(key, []).append(row["id"])
        return {"title_candidates_for_review": [v for v in titles.values() if len(v) > 1],
                "policy": "Exact identifiers merge on import; title similarity never merges automatically."}

    def export(self):
        result = {}
        for table in ("papers", "aliases", "jobs", "raw_pages", "observations", "discoveries"):
            rows = [dict(row) for row in self.db.execute(f"SELECT * FROM {table}")]
            for row in rows:
                for field in ("data", "payload", "record", "details"):
                    if field in row:
                        row[field] = json.loads(row[field])
            result[table] = rows
        return result

    def search_export(self, search_id=None):
        if search_id is None:
            job = self.db.execute("SELECT * FROM jobs WHERE kind='search' ORDER BY id DESC LIMIT 1").fetchone()
        else:
            job = self.db.execute("SELECT * FROM jobs WHERE kind='search' AND id=?", (search_id,)).fetchone()
        if not job:
            raise ValueError("No matching search. Run a search first or select a valid --search-id.")
        papers, seen = [], set()
        rows = self.db.execute("SELECT d.*, o.record, o.provider, o.retrieved FROM discoveries d LEFT JOIN observations o ON o.id=d.observation_id WHERE d.job_id=? ORDER BY d.id", (job["id"],))
        for row in rows:
            if row["paper_id"] in seen:
                continue
            seen.add(row["paper_id"])
            paper = ({"id": row["paper_id"], **json.loads(row["record"])}
                     if row["record"] else self.get(row["paper_id"]))
            if row['record']:
                paper['metadata_provenance'] = {key: {'provider': row['provider'], 'retrieved_at': paper.get('provider_retrieved_at') or row['retrieved']} for key,value in paper.items() if key != 'id' and value not in ('', None, [], {})}
            paper.update(search_rank=row["rank"], library=self.annotation(row['paper_id']),
                         relevance_rank=row["rank"] if job["sort_order"] == "relevance" else None,
                         relevance_score=None, ranking_source=job["source"])
            papers.append(paper)
        # Recover structured author names from the archived original response when
        # an older parser saved only a display string. Never rewrite observations.
        if any(not p.get('author_list') for p in papers):
            authors = {}
            for archived in self.db.execute('SELECT payload FROM raw_pages WHERE job_id=? ORDER BY id', (job['id'],)):
                payload = json.loads(archived[0])
                if isinstance(payload, str) and payload.lstrip().startswith('<'):
                    records = pubmed_records(payload)
                elif isinstance(payload, dict) and 'resultList' in payload:
                    records = [epmc_record(r) for r in payload['resultList'].get('result', [])]
                elif isinstance(payload, dict) and isinstance(payload.get('message'), dict) and 'items' in payload['message']:
                    records = [crossref_record(r) for r in payload['message']['items']]
                else:
                    continue
                for record in records:
                    if record.get('author_list'):
                        for key in identities(record).items():
                            authors[key] = record['author_list']
            for paper in papers:
                if not paper.get('author_list'):
                    recovered = next((authors[key] for key in identities(paper).items() if key in authors), None)
                    if recovered:
                        paper['author_list'] = recovered
                        paper.setdefault('metadata_provenance', {})['author_list'] = {
                            **paper.get('metadata_provenance', {}).get('authors', {'provider': job['source'], 'retrieved_at': job['started']}),
                            'derivation': 'Parsed structured names from the archived original API response.'}
        return {"schema_version": 2, "library_id": self.library_id, "generated_at": now(), "software_version": __version__, "search": dict(job), "papers": papers}

    def library_export(self, **filters):
        papers = self.list_papers(**filters)
        return {'schema_version': 2, 'library_id': self.library_id, 'software_version': __version__, 'generated_at': now(),
                'search': {'id': 'library', 'query': 'Library selection', 'source': 'local',
                           'sort_order': 'local', 'started': now(), 'status': 'complete',
                           'total': len(papers), 'error': None, 'filters': filters}, 'papers': papers}

    def close(self):
        self.db.close()
