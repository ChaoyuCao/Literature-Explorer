"""Public metadata APIs. No scientific measurement or full-text extraction."""
import html
import re
from datetime import date
from urllib.parse import quote
from xml.etree import ElementTree as ET
from .http import APIError

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF = 'https://api.crossref.org'


def search_query(provider, query, filters=None):
    """Build source-specific filters without changing the user's original query."""
    if filters is not None and (not isinstance(filters, dict) or set(filters) - {'author', 'journal', 'article_type', 'date_from', 'date_to'}):
        raise ValueError('Unsupported search filters.')
    filters = {k: v for k, v in (filters or {}).items() if v is not None and v != ''}
    if any(not isinstance(v, str) for v in filters.values()):
        raise ValueError('Search filter values must be text.')
    for field in ('date_from', 'date_to'):
        if field in filters:
            try:
                date.fromisoformat(filters[field])
            except (ValueError, TypeError):
                raise ValueError('Date filters must be YYYY-MM-DD.') from None
    if filters.get('date_from', '') > filters.get('date_to', '9999-12-31'):
        raise ValueError('The start date must precede the end date.')
    if provider == 'crossref':
        return query
    clauses = [f'({query})'] if filters else [query]
    fields = {'author': 'Author', 'journal': 'Journal', 'article_type': 'Publication Type'} if provider == 'pubmed' else {'author': 'AUTH', 'journal': 'JOURNAL', 'article_type': 'PUB_TYPE'}
    for key, field in fields.items():
        if filters.get(key):
            value = filters[key]
            if '"' in value:
                raise ValueError('Filter text cannot contain a double quote.')
            clauses.append(f'"{value}"[{field}]' if provider == 'pubmed' else f'{field}:"{value}"')
    if filters.get('date_from') or filters.get('date_to'):
        start, end = filters.get('date_from', '1000-01-01'), filters.get('date_to', '3000-12-31')
        clauses.append(f'("{start}"[Date - Publication] : "{end}"[Date - Publication])' if provider == 'pubmed' else f'FIRST_PDATE:[{start} TO {end}]')
    return ' AND '.join(clauses)


def crossref_record(raw):
    parts = (raw.get('published') or raw.get('issued') or {}).get('date-parts', [[]])[0]
    authors = [' '.join(filter(None, [a.get('given'), a.get('family')])) or a.get('name', '') for a in raw.get('author', [])]
    return {'title': plain((raw.get('title') or [''])[0]), 'abstract': plain(raw.get('abstract')),
            'authors': '; '.join(authors), 'author_list': authors,
            'journal': plain((raw.get('container-title') or [''])[0]),
            'journal_abbreviation': plain((raw.get('short-container-title') or [''])[0]),
            'issns': raw.get('ISSN', []), 'doi': doi(raw.get('DOI')), 'pmid': '', 'pmcid': '',
            'year': str(parts[0]) if parts else '', 'publication_date': '-'.join(str(p).zfill(2) if i else str(p) for i,p in enumerate(parts)),
            'volume': raw.get('volume', ''), 'issue': raw.get('issue', ''), 'pages': raw.get('page', ''),
            'publication_types': [raw['type']] if raw.get('type') else [], 'keywords': raw.get('subject', []),
            'citation_count': raw.get('is-referenced-by-count'), 'citation_source': 'Crossref',
            'article_url': raw.get('URL') or 'https://doi.org/' + doi(raw.get('DOI')),
            'source': 'crossref', 'source_id': doi(raw.get('DOI')),
            'metadata_links': raw.get('link', []), 'relationships': raw.get('relation', {}),
            'updates': raw.get('update-to', []), 'publisher': raw.get('publisher', '')}


def plain(value):
    return html.unescape(re.sub("<[^>]+>", "", value or "")).strip()


def doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value or "", flags=re.I).strip().lower()


def epmc_record(raw):
    source, ident = raw.get("source", ""), raw.get("id", "")
    journal_info = raw.get("journalInfo", {})
    journal = journal_info.get("journal", {})
    return {
        "title": plain(raw.get("title")), "abstract": plain(raw.get("abstractText")),
        "authors": raw.get("authorString", ""), "year": str(raw.get("pubYear", "")),
        "author_list": [x.get('fullName', '') for x in raw.get('authorList', {}).get('author', []) if x.get('fullName')],
        "journal": plain(raw.get("journalTitle") or journal.get("title", "")),
        "journal_abbreviation": journal.get("medlineAbbreviation", ""),
        "issns": list(dict.fromkeys(filter(None, [journal.get("issn"), journal.get("essn")]))),
        "publication_date": raw.get("firstPublicationDate", ""),
        "volume": journal_info.get("volume", ""), "issue": journal_info.get("issue", ""),
        "pages": raw.get("pageInfo", ""),
        "citation_count": raw.get("citedByCount"),
        "citation_source": "Europe PMC",
        "keywords": raw.get("keywordList", {}).get("keyword", []),
        "mesh_terms": [x.get("descriptorName", "") for x in raw.get("meshHeadingList", {}).get("meshHeading", [])],
        "doi": doi(raw.get("doi")), "pmid": str(raw.get("pmid") or (ident if source == "MED" else "")),
        "pmcid": raw.get("pmcid", ""), "source": source, "source_id": ident,
        "is_open_access": raw.get("isOpenAccess", ""), "publication_types": raw.get("pubTypeList", {}).get("pubType", []),
        "full_text_urls": raw.get("fullTextUrlList", {}).get("fullTextUrl", []),
        "article_url": f"https://europepmc.org/article/{source}/{ident}",
    }


def pubmed_records(payload):
    root = ET.fromstring(payload)
    for article in root.findall("PubmedArticle"):
        citation = article.find("MedlineCitation")
        item = citation.find("Article")
        ids = {node.get("IdType"): node.text or "" for node in article.findall("./PubmedData/ArticleIdList/ArticleId")}
        pmid = citation.findtext("PMID", "")
        authors = []
        for author in item.findall("./AuthorList/Author"):
            authors.append(author.findtext("CollectiveName") or " ".join(filter(None, [author.findtext("LastName"), author.findtext("Initials")])))
        date = item.find("./Journal/JournalIssue/PubDate")
        year = date.findtext("Year", "") if date is not None else ""
        if not year and date is not None:
            year = (re.search(r"\d{4}", date.findtext("MedlineDate", "")) or [""])[0]
        yield {
            "title": "".join(item.find("ArticleTitle").itertext()) if item.find("ArticleTitle") is not None else "",
            "abstract": "\n".join((node.get("Label", "") + ": " if node.get("Label") else "") + "".join(node.itertext()) for node in item.findall("./Abstract/AbstractText")),
            "authors": ", ".join(authors), "author_list": authors, "year": year, "journal": item.findtext("./Journal/Title", ""),
            "journal_abbreviation": item.findtext("./Journal/ISOAbbreviation", ""),
            "issns": list(dict.fromkeys(filter(None, [item.findtext("./Journal/ISSN"), citation.findtext("./MedlineJournalInfo/ISSNLinking")]))),
            "publication_date": "-".join(filter(None, [year, date.findtext("Month", ""), date.findtext("Day", "")])) if date is not None else year,
            "volume": item.findtext("./Journal/JournalIssue/Volume", ""),
            "issue": item.findtext("./Journal/JournalIssue/Issue", ""),
            "pages": item.findtext("./Pagination/MedlinePgn", ""),
            "keywords": ["".join(n.itertext()) for n in citation.findall("./KeywordList/Keyword")],
            "mesh_terms": [n.text for n in citation.findall("./MeshHeadingList/MeshHeading/DescriptorName")],
            "article_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "doi": doi(ids.get("doi")), "pmid": pmid, "pmcid": ids.get("pmc", ""),
            "source": "MED", "source_id": pmid,
            "publication_types": [n.text for n in item.findall("./PublicationTypeList/PublicationType")],
        }


class Sources:
    def __init__(self, http):
        self.http = http

    def stamp(self, record):
        fetched = getattr(self.http, 'response_info', {}).get('fetched_at')
        return {**record, 'provider_retrieved_at': fetched} if fetched else record

    def search_epmc(self, query, limit=None, sort="relevance", resume=None):
        # Europe PMC defaults to relevance. Date ordering uses its documented query clause.
        api_query = query if sort == "relevance" else query + " sort_date:y"
        cursor, seen, count = (resume or {}).get('cursor', '*'), set(), (resume or {}).get('count', 0)
        while True:
            page = self.http.json(EPMC + "/search", {"query": api_query, "format": "json", "resultType": "core",
                                                    "pageSize": min(100, limit - count) if limit else 100, "cursorMark": cursor})
            rows = page.get("resultList", {}).get("result", [])
            if not rows and count < int(page.get("hitCount", 0)):
                raise APIError("Europe PMC returned an empty page before all results were retrieved.")
            yield {"raw": page, "records": [epmc_record(r) for r in rows], "total": int(page.get("hitCount", 0)),
                   "checkpoint": {'cursor': page.get('nextCursorMark'), 'count': count + len(rows)},
                   'response_info': getattr(self.http, 'response_info', {})}
            count += len(rows)
            next_cursor = page.get("nextCursorMark")
            if not rows or (limit and count >= limit) or count >= int(page.get("hitCount", 0)):
                return
            if not next_cursor or next_cursor == cursor or next_cursor in seen:
                raise APIError("Europe PMC pagination ended before all reported results were retrieved.")
            seen.add(cursor)
            cursor = next_cursor

    def search_pubmed(self, query, limit=None, sort="relevance", resume=None):
        if resume and 'ids' in resume:
            ids, total = resume['ids'], resume['total']
        else:
            search = self.http.json(NCBI + "/esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json",
                                                       "retmax": min(limit or 9999, 9999), "sort": "relevance" if sort == "relevance" else "pub_date",
                                                       "tool": "mechanosensor_literature"})
            result = search["esearchresult"]
            if result.get("errorlist") or result.get("ERROR"):
                raise APIError("PubMed rejected the query; check its search syntax.")
            total, ids = int(result["count"]), result.get("idlist", [])
            yield {"raw": search, "records": [], "total": total,
                   'checkpoint': {'ids': ids, 'total': total, 'offset': 0},
                   'response_info': getattr(self.http, 'response_info', {})}
        if total > 9999 and (limit is None or limit > 9999):
            raise APIError("PubMed result exceeds 9,999; split by publication date or use Europe PMC.")
        expected = min(total, limit or total, 9999)
        if len(ids) != expected:
            raise APIError('PubMed returned fewer identifiers than requested; search is incomplete.')
        for start in range((resume or {}).get('offset', 0), len(ids), 100):
            batch_ids = ids[start:start+100]
            payload = self.http.get(NCBI + "/efetch.fcgi", {"db": "pubmed", "id": ",".join(ids[start:start+100]),
                                                          "retmode": "xml", "tool": "mechanosensor_literature"})
            by_id = {r["pmid"]: r for r in pubmed_records(payload)}
            if set(by_id) != set(batch_ids):
                raise APIError("PubMed metadata response omitted requested IDs; search is incomplete.")
            yield {"raw": payload.decode("utf-8"), "records": [by_id[i] for i in batch_ids], "total": total,
                   'checkpoint': {'ids': ids, 'total': total, 'offset': start + len(batch_ids)},
                   'response_info': getattr(self.http, 'response_info', {})}

    def search_crossref(self, query, limit=None, sort='relevance', resume=None, filters=None):
        filters = filters or {}
        params = {'query.bibliographic': query, 'sort': 'score' if sort == 'relevance' else 'published', 'order': 'desc'}
        for key, api_key in [('author', 'query.author'), ('journal', 'query.container-title')]:
            if filters.get(key):
                params[api_key] = filters[key]
        selected = [f'{api}:{filters[key]}' for key,api in [('date_from', 'from-pub-date'), ('date_to', 'until-pub-date'), ('article_type', 'type')] if filters.get(key) and not (key == 'article_type' and filters[key] == 'all')]
        if selected:
            params['filter'] = ','.join(selected)
        cursor, count = (resume or {}).get('cursor', '*'), (resume or {}).get('count', 0)
        seen = {tuple(resume['last_ids'])} if resume and resume.get('last_ids') else set()
        while True:
            raw = self.http.json(CROSSREF + '/works', {**params, 'rows': min(100, limit-count) if limit else 100, 'cursor': cursor})
            message = raw['message']
            rows, total = message.get('items', []), int(message['total-results'])
            if not rows and count < total:
                raise APIError('Crossref returned an empty page before all results were retrieved.')
            signature = tuple(r.get('DOI') for r in rows)
            if signature and signature in seen:
                raise APIError('Crossref returned the same page again; search is incomplete.')
            next_cursor = message.get('next-cursor')
            yield {'raw': raw, 'records': [crossref_record(r) for r in rows], 'total': total,
                   'checkpoint': {'cursor': next_cursor or cursor, 'count': count+len(rows), 'last_ids': list(signature)},
                   'response_info': getattr(self.http, 'response_info', {})}
            count += len(rows)
            if not rows or count >= total or limit and count >= limit:
                return
            # Crossref may reuse a cursor token: detect repeated content rather than rejecting it.
            if not next_cursor:
                raise APIError('Crossref pagination did not advance; search is incomplete.')
            seen.add(signature)
            cursor = next_cursor

    def lookup(self, identifier):
        identifier = identifier.strip()
        value = doi(identifier)
        if value.startswith('10.') and '/' in value:
            raw = self.http.json(CROSSREF + '/works/' + quote(value, safe=''))
            record = crossref_record(raw['message'])
            if record['doi'] != value:
                raise APIError('Crossref returned a different DOI.')
            return self.stamp(record), 'crossref'
        if identifier.upper().startswith('PMID:'):
            identifier = identifier[5:].strip()
        if identifier.isdigit():
            payload = self.http.get(NCBI + '/efetch.fcgi', {'db': 'pubmed', 'id': identifier, 'retmode': 'xml', 'tool': 'mechanosensor_literature'})
            rows = list(pubmed_records(payload))
            if len(rows) != 1 or rows[0]['pmid'] != identifier:
                raise APIError('No matching PubMed identifier found.')
            return self.stamp(rows[0]), 'pubmed'
        if re.fullmatch(r'PMC\d+', identifier, re.I):
            raw = self.http.json(EPMC + '/search', {'query': 'PMCID:' + identifier.upper(), 'format': 'json', 'resultType': 'core'})
            rows = raw.get('resultList', {}).get('result', [])
            if len(rows) != 1:
                raise APIError('No unique matching PMCID found.')
            if rows[0].get('pmcid', '').upper() != identifier.upper():
                raise APIError('Europe PMC returned a different PMCID.')
            return self.stamp(epmc_record(rows[0])), 'europepmc'
        raise ValueError('Provide a DOI, PMID (digits or PMID:digits), or PMCID.')

