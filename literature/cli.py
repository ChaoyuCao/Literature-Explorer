"""Search, organize, analyze and exchange bibliographic metadata."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote
from . import __version__
from .analysis import compare_searches, merge_searches
from .collector import Collector
from .config import DEFAULTS, load_config, public_config
from .formats import import_records, read_records, write_records
from .http import HTTP
from .reports import discover_metrics, enrich_report, load_metrics, write_report
from .sources import Sources
from .store import Store
from .references import ReferenceRetriever, reference_summary, write_json
from .research import citation_network, discover
from .publications import canonicalize, publication_export
from .resolution import resolve_references

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def report_options(command, config=None):
    config = config or DEFAULTS
    project_metrics = discover_metrics(Path.cwd())
    default = config.get('journal_metrics') or (str(project_metrics) if project_metrics else None)
    metrics = command.add_mutually_exclusive_group()
    metrics.add_argument('--journal-metrics', default=default, help='Journal metric CSV; configured or project cache by default')
    metrics.add_argument('--no-journal-metrics', dest='journal_metrics', action='store_const', const=None)
    command.add_argument('--metrics-year', type=int)
    command.add_argument('--output', help='Output file; JSON plus HTML by default')
    command.add_argument('--dashboard', help='HTML output path')
    command.add_argument('--format', choices=['json', 'csv', 'bibtex', 'ris'], default='json')
    command.add_argument('--retrieve-references', action='store_true', help='Save available reference lists in a separate JSON (off by default)')
    command.add_argument('--references-output', help='Reference JSON/checkpoint path; defaults to OUTPUT.references.json')
    command.add_argument('--references-file', help='Use an existing reference JSON without retrieving lists again')
    command.add_argument('--refresh-references', action='store_true', help='Restart reference/discovery checkpoints instead of resuming')
    command.add_argument('--citation-network', action='store_true', help='Build an in-corpus citation network; retrieves references unless --references-file is supplied')
    command.add_argument('--resolve-references', action='store_true', help='Optionally bridge reference identifiers and enrich metadata; citation-only candidates remain for review')


def remote_filters(command):
    command.add_argument('--author')
    command.add_argument('--journal')
    command.add_argument('--article-type', help='Database type; Crossref defaults to journal-article (all includes other works)')
    command.add_argument('--date-from', help='YYYY-MM-DD, inclusive')
    command.add_argument('--date-to', help='YYYY-MM-DD, inclusive')


def selection_options(command):
    command.add_argument('--ids', nargs='+', type=int)
    command.add_argument('--collection')
    command.add_argument('--tag')
    command.add_argument('--favorite', action='store_true')
    command.add_argument('--status', choices=['unreviewed', 'included', 'excluded', 'uncertain'])
    command.add_argument('--text')
    command.add_argument('--author')
    command.add_argument('--journal')
    command.add_argument('--year-from', type=int)
    command.add_argument('--year-to', type=int)
    command.add_argument('--article-type')


def parser(config=None):
    config = config or DEFAULTS
    root = argparse.ArgumentParser(description='Search, organize, analyze and export literature metadata.')
    root.add_argument('--version', action='version', version='literature ' + __version__)
    root.add_argument('--config', help='JSON configuration file')
    root.add_argument('--state', default=config['state'], help='Writable catalogue directory')
    root.add_argument('--offline', action='store_true', default=config['offline'], help='Use cached metadata only')
    root.add_argument('--refresh', action='store_true', help='Bypass fresh cached responses')
    root.add_argument('--quiet', action='store_true', help='Suppress progress messages on stderr')
    root.add_argument('--cancel-file', help=argparse.SUPPRESS)
    commands = root.add_subparsers(dest='command', required=True)
    search = commands.add_parser('search', help='Search keywords or rerun a saved query')
    databases = search.add_mutually_exclusive_group()
    databases.add_argument('--source', choices=['pubmed', 'europepmc', 'crossref'])
    databases.add_argument('--sources', nargs='+', choices=['pubmed', 'europepmc', 'crossref'], help='Search each selected database and write one deduplicated report; limit applies per database')
    query = search.add_mutually_exclusive_group(required=True)
    query.add_argument('--query')
    query.add_argument('--query-file')
    query.add_argument('--saved', help='Saved query name')
    search.add_argument('--save-as', help='Save the query and filters under this name')
    search.add_argument('--limit', type=int)
    search.add_argument('--sort', choices=['relevance', 'date'])
    remote_filters(search)
    report_options(search, config)
    batch = commands.add_parser('batch', help='Run queries from a JSON list of search specifications')
    batch.add_argument('--file', required=True)
    batch.add_argument('--source', choices=['pubmed', 'europepmc', 'crossref'])
    batch.add_argument('--limit', type=int)
    report_options(batch, config)
    resume = commands.add_parser('resume', help='Resume a failed/interrupted search from its checkpoint')
    resume.add_argument('search_id', type=int)
    report_options(resume, config)
    lookup = commands.add_parser('lookup', help='Look up DOI, PMID or PMCID metadata')
    lookup.add_argument('identifiers', nargs='+')
    lookup.add_argument('--collection')
    commands.add_parser('jobs', help='List saved search history')
    papers = commands.add_parser('papers', help='Filter and list the local paper library')
    selection_options(papers)
    for name in ('export', 'visualize'):
        command = commands.add_parser(name, help='Export a saved search or library selection')
        source = command.add_mutually_exclusive_group()
        source.add_argument('--search-id', type=int)
        source.add_argument('--library', action='store_true')
        selection_options(command)
        report_options(command, config)
    compare = commands.add_parser('compare', help='Compare saved searches and visualize their union/overlap')
    compare.add_argument('--search-ids', nargs='+', type=int, required=True)
    report_options(compare, config)
    merge = commands.add_parser('merge', help='Merge saved searches into one deduplicated report with metadata gap filling')
    merge.add_argument('--search-ids', nargs='+', type=int, required=True)
    report_options(merge, config)
    for name in ('references', 'resolve-references', 'discover'):
        command = commands.add_parser(name, help='Retrieve reference lists for saved papers' if name == 'references' else 'Discover papers one citation step from selected seeds')
        selected = command.add_mutually_exclusive_group()
        selected.add_argument('--search-id', type=int)
        selected.add_argument('--search-ids', nargs='+', type=int, help='Use the merged papers from several saved searches')
        selected.add_argument('--report', help='Existing JSON paper report; original files are preserved')
        selected.add_argument('--library', action='store_true')
        selection_options(command)
        report_options(command, config)
        if name == 'discover':
            command.add_argument('--direction', choices=['references', 'citations', 'both'], default='references')
            command.add_argument('--limit', type=int, default=100, help='Maximum unique discovered papers, excluding seeds (default 100)')
    imported = commands.add_parser('import', help='Import JSON, CSV, BibTeX or RIS bibliographies')
    imported.add_argument('file')
    imported.add_argument('--format', choices=['json', 'csv', 'bibtex', 'ris'])
    imported.add_argument('--collection')
    annotation = commands.add_parser('annotate', help='Edit a paper note, tags, favorite or review status')
    annotation.add_argument('paper_id', type=int)
    annotation.add_argument('--note')
    annotation.add_argument('--tag', action='append', default=[])
    annotation.add_argument('--remove-tag', action='append', default=[])
    annotation.add_argument('--favorite', action=argparse.BooleanOptionalAction, default=None)
    annotation.add_argument('--status', choices=['unreviewed', 'included', 'excluded', 'uncertain'])
    annotations = commands.add_parser('annotations', help='Apply browser-exported annotation edits to this catalogue')
    annotations.add_argument('--file', required=True)
    annotations.add_argument('--overwrite', action='store_true', help='Apply annotations even if the catalogue was edited since the report')
    collection = commands.add_parser('collections', help='List collections or change collection membership')
    collection.add_argument('--name')
    collection.add_argument('--add', nargs='+', type=int, default=[])
    collection.add_argument('--remove', nargs='+', type=int, default=[])
    management = collection.add_mutually_exclusive_group()
    management.add_argument('--rename')
    management.add_argument('--delete', action='store_true', help='Delete a collection while preserving papers')
    queries = commands.add_parser('queries', help='List saved queries or save a search specification')
    queries.add_argument('--name')
    queries.add_argument('--query')
    queries.add_argument('--source', choices=['pubmed', 'europepmc', 'crossref'])
    queries.add_argument('--limit', type=int)
    queries.add_argument('--sort', choices=['relevance', 'date'], default='relevance')
    queries.add_argument('--delete', action='store_true')
    remote_filters(queries)
    enrichment = commands.add_parser('enrich', help='Fill missing metadata for a library selection')
    selection_options(enrichment)
    dedup = commands.add_parser('deduplicate', help='Review title candidates or merge compatible IDs')
    dedup.add_argument('--merge', nargs=2, type=int, metavar=('SOURCE_ID', 'TARGET_ID'))
    settings = commands.add_parser('config', help='Show effective configuration or write a template')
    settings.add_argument('--write')
    cache = commands.add_parser('cache', help='Show metadata cache size or clear it')
    cache.add_argument('--clear', action='store_true')
    metrics = commands.add_parser('metrics', help='Validate and install a local metric CSV in the writable state directory')
    metrics.add_argument('--file', required=True)
    return root


def selection(args):
    return {key: getattr(args, key, None) for key in ('ids', 'collection', 'tag', 'favorite', 'status', 'text', 'author', 'journal', 'year_from', 'year_to', 'article_type')}


def specification(args, config, store=None):
    saved = store.query(args.saved) if getattr(args, 'saved', None) else {}
    filters = dict(saved.get('filters') or {})
    filters.update({key: getattr(args, key) for key in ('author', 'journal', 'article_type', 'date_from', 'date_to') if getattr(args, key, None) is not None})
    query = getattr(args, 'query', None)
    if getattr(args, 'query_file', None):
        query = Path(args.query_file).read_text(encoding='utf-8-sig').strip()
    spec = {'source': getattr(args, 'source', None) or saved.get('source') or config['source'],
            'query': query if query is not None else saved.get('query'),
            'limit': args.limit if args.limit is not None else saved.get('limit'),
            'sort': getattr(args, 'sort', None) or saved.get('sort') or 'relevance', 'filters': filters}
    sources = getattr(args, 'sources', None) or (saved.get('sources') if not getattr(args, 'source', None) else None)
    if sources:
        spec['sources'] = list(dict.fromkeys(sources))
        spec['source'] = spec['sources'][0]
    return spec


def save_search(store, args, search_id=None, report=None, output_dir='outputs', analyze=True):
    report = enrich_report(report or store.search_export(search_id), args.journal_metrics, args.metrics_year)
    suffix = {'json': '.json', 'csv': '.csv', 'bibtex': '.bib', 'ris': '.ris'}[args.format]
    output = Path(args.output or Path(output_dir).expanduser() / ('search-' + str(report['search']['id']) + suffix)).expanduser()
    reference_path = Path(getattr(args, 'references_output', None) or output.with_suffix('.references.json')).expanduser()
    network_path = output.with_suffix('.network.json')
    publications_path = output.with_suffix('.publications.json')
    reference_dashboard = output.with_suffix('.references.html')
    input_reference = getattr(args, 'references_file', None)
    wants_references = (getattr(args, 'retrieve_references', False) or getattr(args, 'citation_network', False)
                        or getattr(args, 'resolve_references', False))
    if input_reference and getattr(args, 'retrieve_references', False):
        raise ValueError('Choose --references-file or --retrieve-references, not both.')
    if getattr(args, 'references_output', None) and not wants_references:
        raise ValueError('--references-output requires --retrieve-references or --citation-network.')
    outputs = [output, Path(args.dashboard or output.with_suffix('.html')), reference_path, network_path,
               publications_path, reference_dashboard]
    if len({p.resolve() for p in outputs}) != len(outputs):
        raise ValueError('Paper, HTML, reference, and network paths must be different.')
    if getattr(args, 'report', None) and Path(args.report).resolve() in {p.resolve() for p in outputs}:
        raise ValueError('Choose output paths different from the original input report.')
    if input_reference and Path(input_reference).resolve() in {p.resolve() for p in outputs}:
        raise ValueError('Paper, HTML, and network outputs must not overwrite the input reference file.')
    # Write usable search results before optional network requests.
    if args.format == 'json':
        result = write_report(report, output, args.dashboard or str(output.with_suffix('.html')))
        if analyze and (wants_references or input_reference):
            interrupted = False
            if input_reference:
                document = json.loads(Path(input_reference).read_text(encoding='utf-8-sig'))
                reference_path = Path(input_reference)
            else:
                try:
                    document = ReferenceRetriever(args._http, args._progress).retrieve(report, reference_path,
                                      resume=not args.refresh_references)
                except KeyboardInterrupt:
                    document = json.loads(reference_path.read_text(encoding='utf-8'))
                    interrupted = True
            if document.get('kind') != 'references':
                raise ValueError('Choose a reference-list JSON, not a citing-paper list.')
            if document['scope'].get('library_id') != report.get('library_id'):
                raise ValueError('Reference file belongs to a different catalogue.')
            selected_ids = {p['id'] for p in report['papers']}
            available_ids = {p['id'] for p in document['scope']['papers']}
            if input_reference and not selected_ids <= available_ids:
                raise ValueError('Reference input does not cover all selected papers.')
            if selected_ids != available_ids:
                document['parent_scope'] = document['scope'].copy()
                document['papers'] = [e for e in document['papers'] if e['paper_id'] in selected_ids]
                document['scope'] = {**document['scope'], 'papers': [p for p in document['scope']['papers'] if p['id'] in selected_ids]}
            # Never mutate a supplied reference fixture/input: write the upgraded
            # evidence and resumable resolution to the new report's sidecar.
            reference_path = Path(getattr(args, 'references_output', None) or output.with_suffix('.references.json')).expanduser()
            if input_reference and reference_path.resolve() == Path(input_reference).resolve():
                raise ValueError('Reference output must not overwrite its input reference file.')
            if input_reference and reference_path.exists() and not args.refresh_references:
                saved = json.loads(reference_path.read_text(encoding='utf-8-sig'))
                if saved.get('scope') != document.get('scope'):
                    raise ValueError('Reference output checkpoint belongs to a different paper selection.')
                document = saved
            canonicalize(document, report, store.list_papers())
            write_json(reference_path, document)
            if args.resolve_references and not interrupted:
                try:
                    resolve_references(document, report, store, Sources(args._http), reference_path, args._progress)
                except KeyboardInterrupt:
                    interrupted = True
            network = citation_network(report, document,
                args._progress if args.citation_network and not interrupted else None)  # Also validates catalogue/identifiers.
            report['reference_retrieval'] = {**reference_summary(document), 'file': str(reference_path.resolve())}
            result['references'] = str(reference_path.resolve())
            result['reference_status'] = document['status']
            result['resolution_status'] = document.get('resolution_status', 'not_requested')
            write_json(publications_path, publication_export(document))
            result['publications'] = str(publications_path.resolve())
            from .reference_report import write_reference_report
            write_reference_report(document, reference_dashboard)
            result['reference_dashboard'] = str(reference_dashboard.resolve())
            try:
                browse_href = quote(Path(os.path.relpath(reference_dashboard.resolve(), outputs[1].resolve().parent)).as_posix(), safe='/')
            except ValueError:
                browse_href = reference_dashboard.resolve().as_uri()
            report['reference_analysis'] = {'schema_version': 2, 'coverage': document['coverage'],
                'publications_file': str(publications_path.resolve()),
                'references_file': str(reference_path.resolve()),
                'reference_dashboard': str(reference_dashboard.resolve()),
                'reference_dashboard_href': browse_href,
                'provider_outcomes': report['reference_retrieval']['provider_outcomes'],
                'relationships': document['relationships']}
            if args.citation_network:
                report['citation_network'] = network
                write_json(network_path, network)
                result['network'] = str(network_path.resolve())
            result.update(write_report(report, output, args.dashboard or str(output.with_suffix('.html'))))
            if interrupted:
                print(json.dumps({'partial_results': result}), file=sys.stderr)
                raise KeyboardInterrupt
        return result
    if wants_references or input_reference:
        raise ValueError('Reference analysis requires --format json.')
    if args.dashboard and Path(args.dashboard).resolve() == output.resolve():
        raise ValueError('Bibliography and dashboard paths must be different.')
    result = write_records(report['papers'], output, args.format)
    if args.dashboard or args.command == 'visualize':
        dashboard = args.dashboard or str(output.with_suffix('.html'))
        sidecar = output.with_name(output.name + '.report.json')
        if sidecar.resolve() == Path(dashboard).resolve():
            raise ValueError('Dashboard and report JSON paths must be different.')
        result.update(write_report(report, sidecar, dashboard))
    return result


def main(argv=None):
    store, http = None, None
    try:
        preliminary = argparse.ArgumentParser(add_help=False)
        preliminary.add_argument('--config')
        known, _ = preliminary.parse_known_args(argv)
        config = load_config(known.config)
        args = parser(config).parse_args(argv)
        args.output_dir = config['output_dir']
        state = Path(args.state).expanduser().resolve()
        # An installed local metric cache takes precedence over the project fallback.
        local_metrics = state / 'journal-metrics.csv'
        if hasattr(args, 'journal_metrics') and local_metrics.exists() and '--journal-metrics' not in (argv or sys.argv[1:]) and '--no-journal-metrics' not in (argv or sys.argv[1:]) and not config.get('journal_metrics'):
            args.journal_metrics = str(local_metrics)
        if args.command == 'config':
            if args.write:
                path = Path(args.write)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('x', encoding='utf-8') as handle:
                    json.dump(DEFAULTS, handle, indent=2)
                result = {'configuration': str(path.resolve())}
            else:
                result = public_config({**config, 'state': str(state), 'offline': args.offline})
        else:
            store = Store(state / 'catalog.sqlite3')
            def progress(message):
                if args.cancel_file and Path(args.cancel_file).exists():
                    raise KeyboardInterrupt
                if not args.quiet:
                    print(message, file=sys.stderr, flush=True)
            if args.quiet and not args.cancel_file:
                progress = None
            http = HTTP(timeout=config['timeout'], retries=config['retries'], cache_dir=state / 'cache',
                        cache_ttl=-1 if args.refresh else config['cache_ttl'], offline=args.offline,
                        email=config['email'], ncbi_api_key=config['ncbi_api_key'], progress=progress)
            collector = Collector(store, Sources(http), progress)
            args._http, args._progress = http, progress
            if hasattr(args, 'journal_metrics'):
                load_metrics(args.journal_metrics, args.metrics_year)
                if args.output and args.dashboard and Path(args.output).resolve() == Path(args.dashboard).resolve():
                    raise ValueError('Output and dashboard paths must be different.')
            if args.command in ('search', 'resume'):
                try:
                    if args.command == 'search':
                        spec = specification(args, config, store)
                        if args.save_as:
                            store.save_query(args.save_as, spec)
                        if spec.get('sources'):
                            result = collector.search_many(spec['sources'], spec['query'], spec['limit'], spec['sort'], spec['filters'])
                            report = merge_searches(store, collector.multi_search_ids, spec['query'])
                            report['merge']['requested_sources'] = spec['sources']
                            result['status'] = report['search']['status']
                        else:
                            result = collector.search(**{'provider': spec['source'], **{k:v for k,v in spec.items() if k != 'source'}})
                    else:
                        result = collector.resume(args.search_id)
                except (Exception, KeyboardInterrupt):
                    if getattr(collector, 'multi_search_ids', None):
                        partial = merge_searches(store, collector.multi_search_ids, spec['query'])
                        partial['merge']['requested_sources'] = spec['sources']
                        print(json.dumps({'partial_results': save_search(store, args, report=partial, output_dir=args.output_dir, analyze=False)}), file=sys.stderr)
                    elif getattr(collector, 'last_job_id', None):
                        print(json.dumps({'partial_results': save_search(store, args, collector.last_job_id, output_dir=args.output_dir, analyze=False)}), file=sys.stderr)
                    raise
                if 'job_id' in result:
                    result.update(save_search(store, args, result['job_id'], output_dir=args.output_dir))
                elif args.command == 'search' and spec.get('sources'):
                    result.update(save_search(store, args, report=report, output_dir=args.output_dir))
            elif args.command == 'batch':
                specs = json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
                if not isinstance(specs, list) or any(not isinstance(s, dict) or not s.get('query') for s in specs):
                    raise ValueError('Batch file must be a JSON list of objects containing query.')
                if args.output or args.dashboard:
                    raise ValueError('Batch creates one report pair per search in the configured output directory; omit --output/--dashboard.')
                result = []
                for spec in specs:
                    if set(spec) - {'query', 'source', 'limit', 'sort', 'filters', 'name'}:
                        raise ValueError('Unsupported field in batch search specification.')
                    try:
                        if hasattr(collector, 'last_job_id'):
                            del collector.last_job_id
                        if spec.get('name'):
                            store.save_query(spec['name'], {k:v for k,v in spec.items() if k != 'name'})
                        item = collector.search(spec.get('source') or args.source or config['source'], spec['query'], spec.get('limit', args.limit), spec.get('sort', 'relevance'), spec.get('filters'))
                        item.update(save_search(store, args, item['job_id'], output_dir=args.output_dir))
                    except (ValueError, RuntimeError, OSError) as exc:
                        item = {'status': 'failed', 'query': spec['query'], 'error': str(exc)}
                        if getattr(collector, 'last_job_id', None):
                            item.update(save_search(store, args, collector.last_job_id, output_dir=args.output_dir))
                    result.append(item)
            elif args.command in ('export', 'visualize'):
                selected = selection(args)
                use_library = args.library or any(v for v in selected.values())
                if use_library and args.search_id is not None:
                    original = store.search_export(args.search_id)
                    allowed = {p['id'] for p in store.list_papers(**selected)}
                    original['papers'] = [p for p in original['papers'] if p['id'] in allowed]
                    original['selection'] = selected
                    report = original
                else:
                    report = store.library_export(**selected) if use_library else store.search_export(args.search_id)
                result = save_search(store, args, report=report, output_dir=args.output_dir)
            elif args.command == 'compare':
                result = save_search(store, args, report=compare_searches(store, args.search_ids), output_dir=args.output_dir)
            elif args.command == 'merge':
                report = merge_searches(store, args.search_ids)
                result = {**save_search(store, args, report=report, output_dir=args.output_dir), 'status': report['search']['status']}
            elif args.command in ('references', 'resolve-references', 'discover'):
                if args.report:
                    report = json.loads(Path(args.report).read_text(encoding='utf-8-sig'))
                    if not isinstance(report, dict) or not isinstance(report.get('papers'), list) or 'search' not in report:
                        raise ValueError('Choose a JSON paper report.')
                    if report.get('library_id') != store.library_id:
                        raise ValueError('Select the workspace/catalogue that created this report.')
                elif args.search_ids:
                    report = merge_searches(store, args.search_ids)
                elif args.library or any(v for v in selection(args).values()):
                    if args.search_id is not None:
                        report = store.search_export(args.search_id)
                        allowed = {p['id'] for p in store.list_papers(**selection(args))}
                        report['papers'] = [p for p in report['papers'] if p['id'] in allowed]
                    else:
                        report = store.library_export(**selection(args))
                else:
                    report = store.search_export(args.search_id)
                selected = selection(args)
                if args.report or args.search_ids:
                    if any(v for v in selected.values()):
                        allowed = {p['id'] for p in store.list_papers(**selected)}
                        report['papers'] = [p for p in report['papers'] if p['id'] in allowed]
                        report['selection'] = selected
                if not args.output:
                    args.output = str(Path(args.output_dir) / (args.command + '-' + str(report['search']['id']) + '.json'))
                if args.command in ('references', 'resolve-references'):
                    if args.command == 'resolve-references':
                        if not args.references_file:
                            raise ValueError('Use --references-file to resolve previously retrieved observations.')
                        args.resolve_references = True
                    if not args.references_file:
                        args.retrieve_references = True
                else:
                    if args.format != 'json':
                        raise ValueError('Discovery reports require --format json.')
                    if args.report and Path(args.output).resolve() == Path(args.report).resolve():
                        raise ValueError('Discovery must not overwrite its seed report.')
                    report = discover(store, collector.sources, http, report, args.output, args.direction, args.limit,
                                      progress, args.refresh_references, args.references_file)
                    args.references_file = None
                result = save_search(store, args, report=report, output_dir=args.output_dir)
                if args.command == 'discover':
                    result['status'] = report['search']['status']
                    result['discovery_checkpoint'] = report['discovery']['checkpoint']
            elif args.command == 'papers':
                result = store.list_papers(**selection(args))
            elif args.command == 'jobs':
                result = [dict(row) for row in store.db.execute("SELECT * FROM jobs WHERE kind='search' ORDER BY id")]
            elif args.command == 'lookup':
                result = []
                for identifier in args.identifiers:
                    try:
                        record, provider = collector.sources.lookup(identifier)
                        ident = store.upsert(record, provider)
                        if args.collection:
                            store.collection(args.collection, add=[ident])
                        result.append({'identifier': identifier, 'status': 'saved', 'paper': store.get(ident)})
                    except (ValueError, RuntimeError, OSError) as exc:
                        result.append({'identifier': identifier, 'status': 'failed', 'error': str(exc)})
            elif args.command == 'import':
                result = import_records(store, read_records(args.file, args.format), 'import:' + Path(args.file).name, args.collection)
            elif args.command == 'annotate':
                result = store.annotate(args.paper_id, args.note, args.favorite, args.status, args.tag, args.remove_tag)
            elif args.command == 'annotations':
                result = store.import_annotations(json.loads(Path(args.file).read_text(encoding='utf-8-sig')), args.overwrite)
            elif args.command == 'collections':
                if args.name:
                    result = store.collection(args.name, args.add, args.remove, args.rename, args.delete)
                elif args.add or args.remove or args.rename or args.delete:
                    raise ValueError('Specify --name when changing collection membership.')
                else:
                    result = [store.collection(r[0]) for r in store.db.execute('SELECT name FROM collections ORDER BY name')]
            elif args.command == 'queries':
                if args.delete:
                    if not args.name or args.query:
                        raise ValueError('Deleting a saved query requires --name and no --query.')
                    store.query(args.name)
                    with store.transaction():
                        store.db.execute('DELETE FROM saved_queries WHERE name=?', (args.name,))
                    result = {'deleted_query': args.name}
                elif args.name or args.query:
                    if not args.name or not args.query:
                        raise ValueError('Saving a query requires both --name and --query.')
                    spec = specification(args, config)
                    store.save_query(args.name, spec)
                    result = {'name': args.name, **spec}
                else:
                    result = [{'name': r['name'], 'specification': json.loads(r['specification']), 'updated': r['updated']} for r in store.db.execute('SELECT * FROM saved_queries ORDER BY name')]
            elif args.command == 'enrich':
                result = collector.enrich([p['id'] for p in store.list_papers(**selection(args))])
            elif args.command == 'deduplicate':
                result = {'merged_into': store.merge(*args.merge)} if args.merge else store.duplicates()
            elif args.command == 'cache':
                result = http.cache_info(args.clear)
            elif args.command == 'metrics':
                _, _, year = load_metrics(args.file)
                if year is None:
                    raise ValueError('The metric CSV needs at least one available or explicitly unavailable entry.')
                path = state / 'journal-metrics.csv'
                path.write_bytes(Path(args.file).read_bytes())
                result = {'journal_metrics': str(path), 'metric_year': year}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if isinstance(result, dict) and result.get('status') in ('partial', 'failed', 'interrupted'):
            return 1
        if isinstance(result, dict) and (result.get('reference_status') in ('partial', 'failed')
                                       or result.get('resolution_status') in ('partial', 'failed')):
            return 1
        return 1 if isinstance(result, list) and any(isinstance(r, dict) and (r.get('status') == 'failed' or r.get('reference_status') in ('partial', 'failed')) for r in result) else 0
    except KeyboardInterrupt:
        print('Interrupted; saved search pages can be resumed with the resume command.', file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    finally:
        if http:
            http.close()
        if store:
            store.close()
