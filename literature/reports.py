"""Search JSON and an offline dashboard; journal metrics are supplied with provenance."""
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from .analysis import metadata_statistics
from .research import keyword_trends


def journal_key(value):
    # Keep conjunctions: Science Education and Science & Education are different journals.
    return re.sub(r"[^\w&]+", "", str(value or "").casefold())


def issn_key(value):
    return re.sub(r"[^0-9X]", "", str(value or "").upper())


def discover_metrics(workspace, bundle_dir=None):
    """Find an external table without searching unrelated user directories."""
    workspace = Path(workspace).expanduser()
    bundle = Path(bundle_dir) if bundle_dir else (Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent)
    candidates = [workspace / '.literature/journal-metrics.csv', workspace / 'journal-metrics.csv',
                  workspace / 'queries/journal-metrics.csv', bundle / 'journal-metrics.csv',
                  bundle / 'queries/journal-metrics.csv']
    return next((path.resolve() for path in candidates if path.is_file()), None)


def load_metrics(path=None, year=None):
    if not path:
        if year is not None:
            raise ValueError("--metrics-year requires --journal-metrics.")
        return {}, {}, None
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"journal", "impact_factor", "year", "source"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Journal metrics CSV needs journal, impact_factor, year, source; issn is optional.")
        entries = []
        for line, row in enumerate(reader, 2):
            raw_value = (row.get("impact_factor") or "").strip()
            status = (row.get("status") or "available").strip()
            if not raw_value and status != "unavailable":
                continue
            try:
                value, metric_year = float(raw_value) if raw_value else None, int(row["year"])
            except (ValueError, TypeError):
                raise ValueError(f"Invalid impact factor or year on CSV line {line}.") from None
            if (value is not None and (not math.isfinite(value) or value < 0)) or not 1900 <= metric_year <= 2100:
                raise ValueError(f"Invalid impact factor or year on CSV line {line}.")
            if status not in {"available", "unavailable"} or (status == "unavailable" and value is not None):
                raise ValueError(f"Invalid metric status on CSV line {line}.")
            if not row["journal"].strip() or not row["source"].strip():
                raise ValueError(f"Journal name and metric source are required on CSV line {line}.")
            entry = {"journal": row["journal"].strip(), "impact_factor": value,
                            "year": metric_year, "source": row["source"].strip(),
                            "status": status,
                            "aliases": [x.strip() for x in (row.get('aliases') or '').split('|') if x.strip()],
                            "issns": [issn_key(x) for x in re.split(r"[;,|\s]+", row.get("issn") or "") if x]}
            for field in ("source_kind", "retrieved_at", "notes", "release_year", "source_page"):
                if (row.get(field) or "").strip():
                    entry[field] = int(row[field]) if field in {"release_year", "source_page"} else row[field].strip()
            entries.append(entry)
    selected_year = year if year is not None else max((x["year"] for x in entries), default=None)
    if year is not None and not any(x["year"] == year for x in entries):
        raise ValueError(f"No journal metrics for {year} in the CSV.")
    names, issns = {}, {}
    for entry in entries:
        if entry["year"] != selected_year:
            continue
        for index, keys in ((names, [journal_key(x) for x in [entry['journal'], *entry['aliases']]]), (issns, entry["issns"])):
            for key in keys:
                previous = index.get(key)
                if previous and (previous["impact_factor"], previous["source"], journal_key(previous["journal"])) != (entry["impact_factor"], entry["source"], journal_key(entry["journal"])):
                    raise ValueError(f"Conflicting journal metrics for {key} in {selected_year}.")
                index[key] = entry
    return names, issns, selected_year


def enrich_report(report, metrics_path=None, metrics_year=None):
    names, issns, selected_year = load_metrics(metrics_path, metrics_year)
    journals = {}
    for paper in report["papers"]:
        matches = [issns[issn_key(x)] for x in paper.get("issns", []) if issn_key(x) in issns]
        if len({(x["journal"], x["impact_factor"]) for x in matches}) > 1:
            raise ValueError(f"Ambiguous ISSN metric match for paper {paper['id']}.")
        metric = matches[0] if matches else names.get(journal_key(paper.get("journal")))
        if metric is None:
            metric = names.get(journal_key(paper.get("journal_abbreviation")))
        reference = ({k: v for k, v in metric.items() if k not in {"journal", "issns", "aliases"}}
                     if metric else None)
        if reference:
            reference["metric_name"] = "Journal Impact Factor"
        paper["journal_metric"] = reference if metric and metric["impact_factor"] is not None else None
        paper["journal_metric_reference"] = reference
        paper["journal_metric_status"] = metric["status"] if metric else "unmatched"
        paper["journal_metric_note"] = metric.get("notes", "") if metric else ("No matching entry in the selected journal metric table." if metrics_path else "No journal metric CSV was supplied.")
        # Preserve source journal text; use the supplied canonical name only for grouping.
        name = metric["journal"] if metric else paper.get("journal") or "Unknown journal"
        paper["journal_group"] = name
        key = journal_key(name)
        if key not in journals:
            journals[key] = {"journal": name, "paper_count": 0, "metric": paper["journal_metric"],
                             "metric_reference": reference, "metric_status": paper["journal_metric_status"],
                             "metric_note": paper["journal_metric_note"]}
        paper["journal_group"] = journals[key]["journal"]
        journals[key]["paper_count"] += 1
    papers = report["papers"]
    report["journal_metrics"] = {"year": selected_year,
                                 "file": str(Path(metrics_path).resolve()) if metrics_path else None,
                                 "matched_papers": sum(p["journal_metric"] is not None for p in papers),
                                 "matched_journals": sum(j["metric"] is not None for j in journals.values()),
                                 "source_kind_counts": dict(Counter(j["metric"].get("source_kind", "supplied") for j in journals.values() if j["metric"])),
                                 "enriched_at": datetime.now(timezone.utc).isoformat(),
                                 "policy": "ISSN or exact normalized journal name; missing values are null, never zero."}
    years = Counter(str(p.get("year") or "Unknown") for p in papers)
    types = Counter(t for p in papers for t in p.get("publication_types", []))
    report["statistics"] = {
        "paper_count": len(papers), "journal_count": len(journals),
        "abstract_count": sum(bool(p.get("abstract")) for p in papers),
        "doi_count": sum(bool(p.get("doi")) for p in papers),
        "journal_metadata_count": sum(bool(p.get("journal")) for p in papers),
        "citation_metadata_count": sum(p.get("citation_count") is not None for p in papers),
        "years": dict(sorted(years.items())), "publication_types": dict(types.most_common()),
        "journals": sorted(journals.values(), key=lambda j: (-j["paper_count"], j["journal"].casefold())),
        **metadata_statistics(papers),
    }
    report["notes"] = [
        "Statistics describe the retrieved unique papers, not all database hits when a search is capped or failed.",
        "Relevance is the database result position for this query, not an absolute relevance score.",
        "Unknown relevance ranks in older searches remain null; request a new relevance search to obtain them.",
        "Journal Impact Factors use one metric year for all journals, regardless of article publication year. Metric years and release years are recorded separately when supplied.",
        "Metric sources and retrieval dates are recorded per journal. Public JCR copies are secondary sources; publisher-confirmed values are identified separately.",
        "Unverified or unavailable impact factors remain null; CiteScore and other metrics are not substituted for JIF.",
        "Publication types can overlap. Missing metadata does not mean zero or absence.",
        "Citation counts are source-specific and depend on coverage and retrieval date. Summaries are separated by provider; missing counts are not zero.",
        "Author counts use structured author lists only; keywords and title terms count papers containing a term, not term occurrences. Name variants are not assumed to be the same person.",
        "Library annotations reflect current review decisions; bibliographic search snapshots remain unchanged.",
    ]
    report['keyword_trends'] = keyword_trends(papers)
    return report


def write_report(report, output, dashboard=None):
    output = Path(output).resolve()
    dashboard = Path(dashboard).resolve() if dashboard else None
    if dashboard == output:
        raise ValueError("JSON and dashboard paths must be different.")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    result = {"json": str(output), "paper_count": len(report["papers"])}
    if dashboard:
        dashboard.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(report, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        template = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
        script = Path(__file__).with_name('explorer.js').read_text(encoding='utf-8')
        dashboard.write_text(template.replace('__PRACTICAL_SCRIPT__', script).replace("__REPORT_DATA__", data), encoding="utf-8")
        result["dashboard"] = str(dashboard)
    return result
