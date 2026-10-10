"""Audit an existing report/reference/network/HTML bundle without changing inputs."""
import argparse
import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from literature.publications import canonicalize, publication_export
from literature.references import write_json
from literature.reference_report import write_reference_report
from literature.reports import write_report
from literature.research import citation_network


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True)
    parser.add_argument('--references', required=True)
    parser.add_argument('--network', required=True)
    parser.add_argument('--html', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    files = [Path(getattr(args, k)).resolve() for k in ('report', 'references', 'network', 'html')]
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(p.parent == output for p in files):
        raise ValueError('Audit output must be separate from input fixtures.')
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    before = {p.name: digest(p) for p in files}
    report, original, old_network = [json.loads(p.read_text(encoding='utf-8-sig')) for p in files[:3]]
    data = canonicalize(deepcopy(original), report)
    network = citation_network(report, data)
    expected = Counter((e['paper_id'], s['provider'], o.get('position'), json.dumps(o['raw'], sort_keys=True))
        for e in original['papers'] for s in e['providers'] for r in s['records'] for o in r['observations'])
    actual = Counter((o['seed_paper_id'], o['provider'], o['position'], json.dumps(o['raw'], sort_keys=True)) for o in data['observations'])
    assert expected == actual, 'Original observation evidence changed or was lost'
    assert not any(e['citing'] == e['cited'] for e in network['edges'])
    papers = {p['id']: p for p in report['papers']}
    flagged = {papers[e['citing']].get('pmid') for e in network['flagged_self_links']}
    old_flagged = {papers[e['citing']].get('pmid') for e in old_network['edges'] if e['citing'] == e['cited']}
    assert old_flagged <= flagged, 'A legacy article-to-itself link was not flagged'
    counts = []
    for entry in data['papers']:
        obs = [o for o in data['observations'] if o['seed_paper_id'] == entry['paper_id']]
        counts.append({'paper_id': entry['paper_id'], 'raw_observations': len(obs),
                       'identified_publications_before_optional_enrichment': len({o['publication_id'] for o in obs if o['publication_id']}),
                       'unresolved_observations': sum(o['resolution_status'] == 'unresolved' for o in obs)})
    summary = {'schema_version': 2, 'inputs_sha256': before, 'input_files_unchanged': True,
        'original_legacy_reference_groups': sum(len(e['references']) for e in original['papers']),
        'evidence_preserved': expected == actual, 'coverage_before_optional_enrichment': data['coverage'],
        'flagged_self_link_pmids': sorted(flagged), 'network_statistics': network['statistics'], 'per_paper': counts,
        'policy': 'This offline audit establishes identifier-supported counts only, not an expected true unique-reference total.'}
    write_json(output/'upgraded.references.json', data)
    write_json(output/'upgraded.publications.json', publication_export(data))
    write_json(output/'upgraded.network.json', network)
    write_reference_report(data, output/'upgraded.references.html')
    report.update(citation_network=network, reference_analysis={'schema_version': 2, 'coverage': data['coverage'],
        'provider_outcomes': [], 'reference_dashboard': str(output/'upgraded.references.html')})
    write_report(report, output/'upgraded.json', output/'upgraded.html')
    assert before == {p.name: digest(p) for p in files}, 'Input files were modified'
    write_json(output/'audit.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'per_paper'}, indent=2))


if __name__ == '__main__':
    main()
