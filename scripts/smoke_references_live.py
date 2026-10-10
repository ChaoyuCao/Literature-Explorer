"""Optional small live check of reference/citation metadata APIs; no article downloads."""
import json
import tempfile
from pathlib import Path

from literature.http import HTTP
from literature.references import ReferenceRetriever, reference_summary


def main():
    # Published article identifiers, used only for this explicit live API check.
    report = {'library_id': 'live-api-check', 'search': {'id': 'live-check'},
              'papers': [{'id': 1, 'doi': '10.1126/science.1193270', 'pmid': '20813920'}]}
    with tempfile.TemporaryDirectory(prefix='literature-reference-live-') as directory:
        http = HTTP(timeout=25, retries=0)
        try:
            for direction in ('references', 'citations'):
                data = ReferenceRetriever(http).retrieve(report, Path(directory)/(direction+'.json'), direction=direction, limit=2)
                print(json.dumps({'direction': direction, **reference_summary(data),
                    'providers': [{k: s[k] for k in ('provider', 'status', 'reported_count', 'error')}
                                  for p in data['papers'] for s in p['providers']]}, indent=2))
                if data['status'] == 'partial':
                    raise RuntimeError('Live API check returned partial metadata; inspect the provider errors.')
        finally:
            http.close()


if __name__ == '__main__':
    main()
