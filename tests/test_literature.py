import tempfile
import unittest
from pathlib import Path
from literature.collector import Collector
from literature.http import APIError
from literature.sources import Sources, pubmed_records
from literature.store import Store

class FakeHTTP:
    def __init__(self, pages):
        self.pages, self.calls = iter(pages), []

    def json(self, url, params=None):
        self.calls.append((url, params))
        value = next(self.pages)
        if isinstance(value, Exception):
            raise value
        return value


def page(rows, total, cursor=None):
    result = {"hitCount": total, "resultList": {"result": rows}}
    if cursor:
        result["nextCursorMark"] = cursor
    return result


def article(ident, doi=""):
    return {"id": ident, "source": "MED", "pmid": ident, "doi": doi, "title": "Paper " + ident}


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "catalog.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_cross_source_merge_retains_observations(self):
        first = self.store.upsert({"pmid": "42", "title": "Title", "abstract": "Abstract"}, "pubmed")
        second = self.store.upsert({"pmid": "42", "doi": "https://doi.org/10.1/ABC"}, "europepmc")
        self.assertEqual(first, second)
        self.assertEqual(self.store.get(first)["abstract"], "Abstract")
        self.assertEqual(len(self.store.export()["observations"]), 2)

    def test_bridge_merges_identifier_fragments_and_discovery(self):
        first = self.store.upsert({"pmid": "42", "title": "Title"}, "pubmed")
        second = self.store.upsert({"doi": "10.1/abc", "title": "Title"}, "repository")
        job = self.store.job("search", "pubmed", "test")
        self.store.discover(second, job, "search", rank=1)
        self.store.upsert({"doi": "10.1/abc", "pmid": "42"}, "europepmc")
        self.assertEqual(len(self.store.export()["papers"]), 1)
        self.assertEqual(self.store.export()["discoveries"][0]["paper_id"], first)

    def test_conflicting_identifiers_never_merge(self):
        self.store.upsert({"pmid": "42", "doi": "10.1/abc"}, "pubmed")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.store.upsert({"pmid": "42", "doi": "10.1/different"}, "europepmc")
        self.assertEqual(len(self.store.export()["observations"]), 1)

    def test_identical_titles_require_review(self):
        a = self.store.upsert({"doi": "10.1/preprint", "title": "Same title"}, "preprint")
        b = self.store.upsert({"doi": "10.1/published", "title": "Same title"}, "pubmed")
        self.assertEqual(self.store.duplicates()["title_candidates_for_review"], [[a, b]])
        with self.assertRaises(ValueError):
            self.store.merge(a, b)



    def test_search_cursor_and_provenance(self):
        http = FakeHTTP([page([article("1")], 2, "next"), page([article("2")], 2)])
        result = Collector(self.store, Sources(http)).search("europepmc", "TEST_CHANNEL")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(http.calls[1][1]["cursorMark"], "next")
        exported = self.store.export()
        self.assertEqual(len(exported["raw_pages"]), 2)
        self.assertEqual(len(exported["discoveries"]), 2)
        self.assertEqual(exported["jobs"][0]["query"], "TEST_CHANNEL")

    def test_explicit_limit_is_visible(self):
        result = Collector(self.store, Sources(FakeHTTP([page([article("1")], 100)]))).search("europepmc", "TEST_CHANNEL", 1)
        self.assertEqual(result["status"], "limited")
        self.assertEqual(result["reported_total"], 100)

    def test_failure_retains_partial_results(self):
        http = FakeHTTP([page([article("1")], 2, "next"), APIError("Network unavailable")])
        with self.assertRaises(APIError):
            Collector(self.store, Sources(http)).search("europepmc", "TEST_CHANNEL")
        exported = self.store.export()
        self.assertEqual(len(exported["papers"]), 1)
        self.assertEqual(exported["jobs"][0]["status"], "failed")
        self.assertEqual(exported["jobs"][0]["retrieved"], 1)

    def test_repeated_cursor_is_failure_not_complete(self):
        http = FakeHTTP([page([article("1")], 3, "same"), page([article("2")], 3, "same")])
        with self.assertRaises(APIError):
            Collector(self.store, Sources(http)).search("europepmc", "TEST_CHANNEL")

    def test_pubmed_large_query_is_explicit_failure(self):
        http = FakeHTTP([{"esearchresult": {"count": "10001", "idlist": []}}])
        iterator = Sources(http).search_pubmed("broad")
        next(iterator)
        with self.assertRaisesRegex(APIError, "split"):
            next(iterator)










class ParsingTests(unittest.TestCase):
    def test_pubmed_structured_abstract_and_identifiers(self):
        xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>42</PMID><Article>
        <ArticleTitle>A <i>TEST_CHANNEL</i> paper</ArticleTitle>
        <Journal><Title>Journal</Title><JournalIssue><PubDate><MedlineDate>2023 Jan-Feb</MedlineDate></PubDate></JournalIssue></Journal>
        <Abstract><AbstractText Label="RESULTS">A <b>result</b>.</AbstractText></Abstract>
        <AuthorList><Author><LastName>Smith</LastName><Initials>A</Initials></Author></AuthorList>
        </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/ABC</ArticleId>
        <ArticleId IdType="pmc">PMC42</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""
        record = list(pubmed_records(xml))[0]
        self.assertEqual(record["title"], "A TEST_CHANNEL paper")
        self.assertEqual(record["abstract"], "RESULTS: A result.")
        self.assertEqual(record["doi"], "10.1/abc")
        self.assertEqual(record["year"], "2023")


if __name__ == "__main__":
    unittest.main()
