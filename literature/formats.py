"""Bibliographic interchange: JSON, CSV, RIS and conventional BibTeX entries."""
import csv
import json
import re
from pathlib import Path
from .sources import doi
from .store import identities

LIST_FIELDS = {'issns', 'keywords', 'mesh_terms', 'publication_types', 'author_list'}
STRUCTURED = LIST_FIELDS | {'library', 'metadata_provenance', 'journal_metric', 'journal_metric_reference', 'relationships', 'updates', 'metadata_links', 'full_text_urls', 'search_memberships'}
TEXT_FIELDS = {'title', 'abstract', 'authors', 'journal', 'doi', 'pmid', 'pmcid', 'year', 'volume', 'issue', 'pages', 'article_url', 'publisher'}


def normalize_record(record):
    if not isinstance(record, dict):
        raise ValueError('Each imported paper must be an object.')
    out = {k: v for k,v in record.items() if k != 'id'}
    for field in TEXT_FIELDS:
        value = out.get(field)
        if value is not None and not isinstance(value, (str, int)):
            raise ValueError(f'Imported {field} must be text.')
        out[field] = str(value) if value is not None else ''
    for field in LIST_FIELDS:
        value = out.get(field, [])
        if isinstance(value, str):
            value = [x.strip() for x in value.split(';') if x.strip()]
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise ValueError(f'Imported {field} must contain text values.')
        out[field] = list(dict.fromkeys(value))
    out['doi'] = doi(out['doi'])
    out['pmid'] = re.sub(r'^(?:PMID:\s*|https?://pubmed\.ncbi\.nlm\.nih\.gov/)', '', out['pmid'], flags=re.I).strip().strip('/')
    if out['pmid'] and not out['pmid'].isdigit():
        raise ValueError('Imported PMID must be numeric.')
    out['pmcid'] = re.sub(r'^(?:PMCID:\s*|https?://(?:www\.)?(?:ncbi\.nlm\.nih\.gov/pmc|pmc\.ncbi\.nlm\.nih\.gov)/articles/)', '', out['pmcid'], flags=re.I).strip().strip('/').upper()
    if out['pmcid'] and not re.fullmatch(r'PMC\d+', out['pmcid']):
        raise ValueError('Imported PMCID must have the form PMC followed by digits.')
    if not out['title'] and not any(out[key] for key in ('doi', 'pmid', 'pmcid')):
        raise ValueError('An imported paper needs a title or identifier.')
    if out.get('library') and not isinstance(out['library'], dict):
        raise ValueError('Imported library annotations must be an object.')
    if out.get('citation_count') not in (None, ''):
        try:
            value = int(out['citation_count'])
        except (ValueError, TypeError):
            raise ValueError('citation_count must be a nonnegative integer.') from None
        if value < 0 or str(value) != str(out['citation_count']):
            raise ValueError('citation_count must be a nonnegative integer.')
        out['citation_count'] = value
    else:
        out['citation_count'] = None
    if not out['authors'] and out['author_list']:
        out['authors'] = '; '.join(out['author_list'])
    json.dumps(out, allow_nan=False)
    return out


def read_ris(text):
    records, fields, last = [], None, None
    mapping = {'TI': 'title', 'T1': 'title', 'AB': 'abstract', 'N2': 'abstract', 'JO': 'journal',
               'JF': 'journal', 'T2': 'journal', 'DO': 'doi', 'VL': 'volume', 'IS': 'issue', 'UR': 'article_url', 'PB': 'publisher'}
    def finish():
        out = {target: '\n'.join(fields[tag]) for tag,target in mapping.items() if tag in fields}
        out['author_list'] = fields.get('AU') or fields.get('A1') or []
        out['keywords'], out['issns'] = fields.get('KW', []), fields.get('SN', [])
        out['year'] = (fields.get('PY') or fields.get('Y1') or [''])[0][:4]
        out['publication_types'] = fields.get('TY', [])
        out['pages'] = '-'.join(filter(None, [(fields.get('SP') or [''])[0], (fields.get('EP') or [''])[0]]))
        for value in fields.get('AN', []):
            if value.startswith('PMID:'):
                out['pmid'] = value[5:]
            elif value.startswith('PMCID:'):
                out['pmcid'] = value[6:]
        records.append(out)
    for line in text.splitlines():
        match = re.match(r'^([A-Z0-9]{2})\s{2}-\s?(.*)$', line)
        if match:
            tag, value = match.groups()
            if tag == 'TY':
                if fields is not None:
                    raise ValueError('RIS entry is missing ER termination.')
                fields = {}
            if fields is None:
                raise ValueError('RIS records must start with TY.')
            if tag == 'ER':
                finish()
                fields, last = None, None
            else:
                fields.setdefault(tag, []).append(value)
                last = tag
        elif line.strip() and fields is not None and last:
            fields[last][-1] += '\n' + line.strip()
        elif line.strip():
            raise ValueError('Invalid RIS content outside an entry.')
    if fields is not None:
        raise ValueError('RIS entry is missing ER termination.')
    return records


class BibReader:
    def __init__(self, text):
        self.text, self.pos = text, 0
        self.macros = dict(zip(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'],
                              ['January','February','March','April','May','June','July','August','September','October','November','December']))

    def space(self):
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.text[self.pos] == '%':
                end = self.text.find('\n', self.pos)
                self.pos = end + 1 if end >= 0 else len(self.text)
            else:
                break

    def expect(self, value):
        self.space()
        if not self.text.startswith(value, self.pos):
            raise ValueError(f'Invalid BibTeX near character {self.pos}: expected {value}.')
        self.pos += len(value)

    def balanced(self, opening, closing):
        self.expect(opening)
        start, depth = self.pos, 1
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == '\\':
                self.pos += 2
                continue
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    value = self.text[start:self.pos]
                    self.pos += 1
                    return value
            self.pos += 1
        raise ValueError('Unterminated BibTeX value.')

    def value(self):
        self.space()
        if self.pos >= len(self.text):
            raise ValueError('Missing BibTeX value.')
        char = self.text[self.pos]
        if char == '{':
            value = self.balanced('{', '}')
        elif char == '"':
            self.pos += 1
            start, depth = self.pos, 0
            while self.pos < len(self.text):
                char = self.text[self.pos]
                if char == '\\':
                    self.pos += 2
                    continue
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                elif char == '"' and depth == 0:
                    break
                self.pos += 1
            if self.pos == len(self.text):
                raise ValueError('Unterminated quoted BibTeX value.')
            value = self.text[start:self.pos]
            self.pos += 1
        else:
            match = re.match(r'[\w:./+-]+', self.text[self.pos:])
            if not match:
                raise ValueError('Invalid BibTeX value.')
            token = match[0]
            self.pos += len(token)
            if token.isdigit():
                value = token
            elif token.lower() in self.macros:
                value = self.macros[token.lower()]
            else:
                raise ValueError(f'Undefined BibTeX string: {token}.')
        self.space()
        if self.text.startswith('#', self.pos):
            self.pos += 1
            value += self.value()
        return value

    def read(self):
        records = []
        while True:
            self.space()
            if self.pos == len(self.text):
                break
            self.expect('@')
            match = re.match(r'[A-Za-z]+', self.text[self.pos:])
            if not match:
                raise ValueError('Missing BibTeX entry type.')
            kind = match[0].lower()
            self.pos += len(match[0])
            self.space()
            opening = self.text[self.pos:self.pos+1]
            if opening not in ('{', '('):
                raise ValueError('BibTeX entries require braces or parentheses.')
            closing = '}' if opening == '{' else ')'
            if kind == 'comment':
                self.balanced(opening, closing)
                continue
            self.expect(opening)
            if kind == 'preamble':
                self.value()
                self.expect(closing)
                continue
            if kind != 'string':
                key = re.match(r'[^,\s{}()]+', self.text[self.pos:])
                if not key:
                    raise ValueError('Missing BibTeX citation key.')
                self.pos += len(key[0])
                self.expect(',')
            fields = {}
            while True:
                self.space()
                if self.text.startswith(closing, self.pos):
                    self.pos += 1
                    break
                match = re.match(r'[\w-]+', self.text[self.pos:])
                if not match:
                    raise ValueError('Invalid BibTeX field name.')
                name = match[0].lower()
                self.pos += len(match[0])
                self.expect('=')
                fields[name] = self.value()
                if kind == 'string':
                    self.macros[name] = fields[name]
                self.space()
                if self.text.startswith(',', self.pos):
                    self.pos += 1
                elif not self.text.startswith(closing, self.pos):
                    raise ValueError('Missing comma between BibTeX fields.')
            if kind != 'string':
                out = {target: fields[source] for source,target in [('title','title'),('abstract','abstract'),('journal','journal'),('booktitle','journal'),('doi','doi'),('year','year'),('volume','volume'),('number','issue'),('pages','pages'),('url','article_url'),('pmid','pmid'),('pmcid','pmcid'),('publisher','publisher')] if source in fields}
                out = {k: decode_tex(v) for k,v in out.items()}
                out['author_list'] = [decode_tex(x[1:-1] if x.startswith('{') and x.endswith('}') else x) for x in split_authors(fields.get('author', ''))]
                out['keywords'] = [x.strip() for x in fields.get('keywords', '').split(';') if x.strip()]
                out['keywords'] = [decode_tex(x) for x in out['keywords']]
                out['publication_types'] = [kind]
                records.append(out)
        return records


def split_authors(value):
    parts, depth, start, pos = [], 0, 0, 0
    while pos < len(value):
        if value[pos] == '\\':
            pos += 2
            continue
        depth += (value[pos] == '{') - (value[pos] == '}')
        if depth == 0 and value[pos:pos+5].lower() == ' and ':
            parts.append(value[start:pos].strip())
            start = pos + 5
            pos += 4
        pos += 1
    return [x for x in parts + [value[start:].strip()] if x]


def decode_tex(value):
    return re.sub(r'\\textbackslash\{\}|\\[{}%&_#]', lambda m: '\\' if m[0] == '\\textbackslash{}' else m[0][1], value)


def read_records(path, format=None):
    path = Path(path)
    format = format or path.suffix.lstrip('.').lower()
    text = path.read_text(encoding='utf-8-sig')
    if format == 'json':
        data = json.loads(text)
        rows = data.get('papers') if isinstance(data, dict) and 'papers' in data else [data] if isinstance(data, dict) else data
    elif format == 'csv':
        with path.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            for key in list(row):
                value = row[key]
                if key in STRUCTURED and value and value[:1] in ('[', '{'):
                    row[key] = json.loads(value)
    elif format == 'ris':
        rows = read_ris(text)
    elif format in ('bib', 'bibtex'):
        rows = BibReader(text).read()
    else:
        raise ValueError('Supported formats: json, csv, bibtex (.bib), ris.')
    if not isinstance(rows, list):
        raise ValueError('The input must contain a list of paper records.')
    return [normalize_record(row) for row in rows]


def write_records(papers, path, format=None):
    path = Path(path)
    format = format or path.suffix.lstrip('.').lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if format == 'json':
        path.write_text(json.dumps(papers, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    elif format == 'csv':
        fields = sorted({k for p in papers for k in p}) or ['title', 'doi']
        with path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for paper in papers:
                writer.writerow({k: json.dumps(v, ensure_ascii=False, allow_nan=False) if isinstance(v, (list, dict)) else v for k,v in paper.items()})
    elif format in ('bib', 'bibtex'):
        entries = []
        for index, paper in enumerate(papers, 1):
            kind = entry_type(paper)
            fields = {target: paper.get(source) for source,target in [('title','title'),('journal','booktitle' if kind in {'incollection', 'inproceedings', 'inbook'} else 'journal'),('year','year'),('doi','doi'),('abstract','abstract'),('volume','volume'),('issue','number'),('pages','pages'),('article_url','url'),('pmid','pmid'),('pmcid','pmcid'),('publisher','publisher')]}
            fields['keywords'] = '; '.join(paper.get('keywords', []))
            # Protect arbitrary text braces so exports can always be read back.
            def escape(value):
                return re.sub(r'[\\{}%&_#]', lambda m: '\\textbackslash{}' if m[0] == '\\' else '\\' + m[0], str(value))
            encoded = {k: escape(v) for k,v in fields.items() if v not in (None, '', [])}
            encoded['author'] = ' and '.join('{' + escape(name) + '}' for name in (paper.get('author_list') or [paper.get('authors', '')]) if name)
            entries.append('@' + kind + '{paper' + str(index) + ',\n' + ',\n'.join(f'  {k} = {{{v}}}' for k,v in encoded.items() if v) + '\n}')
        path.write_text('\n\n'.join(entries) + '\n', encoding='utf-8')
    elif format == 'ris':
        entries = []
        for paper in papers:
            kind = {'article': 'JOUR', 'book': 'BOOK', 'inbook': 'CHAP', 'incollection': 'CHAP', 'inproceedings': 'CONF', 'proceedings': 'CONF', 'phdthesis': 'THES', 'mastersthesis': 'THES', 'techreport': 'RPRT', 'misc': 'GEN'}[entry_type(paper)]
            lines = ['TY  - ' + kind]
            for key, tag in [('title','TI'),('journal','JO'),('year','PY'),('doi','DO'),('abstract','AB'),('volume','VL'),('issue','IS'),('pages','SP'),('article_url','UR'),('publisher','PB')]:
                if paper.get(key):
                    lines.append(tag + '  - ' + str(paper[key]).replace('\n', '\n      '))
            lines += ['AU  - ' + name for name in (paper.get('author_list') or [paper.get('authors', '')]) if name]
            lines += ['KW  - ' + word for word in paper.get('keywords', [])]
            lines += ['SN  - ' + issn for issn in paper.get('issns', [])]
            lines += ['AN  - ' + key.upper() + ':' + str(paper[key]) for key in ('pmid','pmcid') if paper.get(key)]
            entries.append('\n'.join(lines + ['ER  - ']))
        path.write_text('\n\n'.join(entries) + '\n', encoding='utf-8')
    else:
        raise ValueError('Supported formats: json, csv, bibtex (.bib), ris.')
    return {'output': str(path.resolve()), 'format': format, 'paper_count': len(papers)}


def entry_type(paper):
    mapping = {'journal-article': 'article', 'journal article': 'article', 'jour': 'article',
               'book-chapter': 'incollection', 'chap': 'incollection', 'proceedings-article': 'inproceedings',
               'conf': 'inproceedings', 'dissertation': 'phdthesis', 'thes': 'phdthesis',
               'rprt': 'techreport', 'gen': 'misc', 'dataset': 'misc', 'posted-content': 'misc'}
    supported = {'article', 'book', 'inbook', 'incollection', 'inproceedings', 'proceedings', 'phdthesis', 'mastersthesis', 'techreport', 'misc'}
    for value in paper.get('publication_types', []):
        kind = mapping.get(value.casefold(), value.casefold())
        if kind in supported:
            return kind
    return 'article'


def import_records(store, records, provider='import', collection=None):
    ids = []
    with store.transaction():
        for record in records:
            annotation = record.get('library') or {}
            paper = {k: v for k,v in record.items() if k not in {'library', 'search_rank', 'relevance_rank', 'relevance_score', 'journal_metric', 'journal_metric_reference', 'journal_group', 'journal_metric_status', 'journal_metric_note'}}
            ident = store.upsert(paper, provider)
            ids.append(ident)
            if annotation:
                store.annotate(ident, note=annotation.get('note'), favorite=annotation.get('favorite'),
                               status=annotation.get('status'), tags=annotation.get('tags', []))
                for name in annotation.get('collections', []):
                    store.collection(name, add=[ident])
        canonical = []
        for record, ident in zip(records, ids):
            for kind, value in identities(record).items():
                row = store.db.execute('SELECT paper_id FROM aliases WHERE kind=? AND value=?', (kind,value)).fetchone()
                if row:
                    ident = row[0]
                    break
            canonical.append(ident)
        ids = canonical
        if collection:
            store.collection(collection, add=ids)
    return {'imported_records': len(records), 'unique_papers': len(set(ids)), 'paper_ids': sorted(set(ids))}
