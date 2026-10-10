"""Desktop controls for the existing metadata search engine and report explorer."""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import __version__
from .progress import parse_progress
from .reports import discover_metrics, enrich_report, load_metrics, write_report
from .sources import search_query

DATABASES = {'pubmed': 'PubMed', 'europepmc': 'Europe PMC', 'crossref': 'Crossref'}


def enable_dpi_awareness():
    if os.name == 'nt':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def search_arguments(query, sources, limit, sort, filters, output, metrics='', references=False, network=False):
    query = query.strip()
    if not query:
        raise ValueError('Enter keywords or a database query.')
    if not sources:
        raise ValueError('Select at least one database.')
    value = None
    if limit.strip():
        try:
            value = int(limit)
        except ValueError:
            raise ValueError('The per-database limit must be a positive integer, or blank for all results.') from None
        if value < 1:
            raise ValueError('The per-database limit must be positive.')
    for source in sources:
        search_query(source, query, filters)
    arguments = ['search', '--sources', *sources, '--query', query, '--sort', sort, '--output', str(output)]
    if value is not None:
        arguments.extend(['--limit', str(value)])
    for key, text in filters.items():
        if text.strip():
            arguments.extend(['--' + key.replace('_', '-'), text.strip()])
    if metrics.strip():
        if not Path(metrics).is_file():
            raise ValueError('Choose an existing journal-metric CSV, or leave it blank.')
        metric_summary(metrics)
        arguments.extend(['--journal-metrics', metrics])
    else:
        arguments.append('--no-journal-metrics')
    if references:
        arguments.append('--retrieve-references')
    if network:
        arguments.append('--citation-network')
    return arguments


def metric_summary(path):
    names, _, year = load_metrics(path)
    if year is None or not names:
        raise ValueError('The journal-metric CSV contains no usable journal entries.')
    entries = {entry['journal']: entry for entry in names.values()}
    available = sum(entry['impact_factor'] is not None for entry in entries.values())
    return f'Loaded {len(entries):,} journals · {available:,} IF values · {year} metric year'


def engine_command():
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).with_name('literature.exe')
        if not executable.is_file():
            raise RuntimeError('Keep literature.exe beside LiteratureExplorer.exe. Extract the complete portable ZIP.')
        return [str(executable)]
    python = Path(sys.executable)
    if python.name.lower() == 'pythonw.exe':
        python = python.with_name('python.exe')
    return [str(python), '-m', 'literature']


def run_engine(arguments, workspace, events, cancel):
    """Only this worker reads processes; Tk widgets are updated on the main thread."""
    try:
        with tempfile.TemporaryDirectory(prefix='literature-desktop-') as directory:
            output = Path(directory) / 'result.json'
            env = dict(os.environ)
            if getattr(sys, 'frozen', False):
                env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
            else:
                env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
            with output.open('w', encoding='utf-8') as stream:
                process = subprocess.Popen([*engine_command(), '--cancel-file', str(cancel), *arguments],
                    cwd=workspace, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                messages = []
                for line in process.stderr:
                    message = line.rstrip()
                    counts = parse_progress(message)
                    if counts is not None:
                        events.put(('counts', counts))
                    else:
                        messages.append(message)
                        events.put(('progress', message))
                process.stderr.close()
                code = process.wait()
            raw = output.read_text(encoding='utf-8').strip()
            try:
                result = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                raise RuntimeError('The search engine returned an unreadable response.\n' + '\n'.join(messages[-5:])) from None
            events.put(('done', code, result, '\n'.join(messages[-5:])))
    except Exception as exc:
        events.put(('error', str(exc)))


def rebuild_existing(path, output, events, metrics=None):
    try:
        report = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(report, dict) or not isinstance(report.get('papers'), list) or not {'search', 'statistics', 'journal_metrics'} <= report.keys():
            raise ValueError('Choose a JSON report exported by this tool. Use Paper library → Import bibliography for other JSON files.')
        if metrics:
            enrich_report(report, metrics)
        result = write_report(report, output, Path(output).with_suffix('.html'))
        events.put(('done', 0, result, ''))
    except (ValueError, OSError, TypeError) as exc:
        events.put(('error', str(exc)))


class DesktopApp:
    def __init__(self, root, preferences_path=None):
        self.root = root
        self.preferences_path = Path(preferences_path or Path.home() / 'LiteratureExplorer' / 'desktop-settings.json')
        preferences = {}
        try:
            preferences = json.loads(self.preferences_path.read_text(encoding='utf-8'))
            if not isinstance(preferences, dict):
                preferences = {}
        except (OSError, ValueError):
            pass
        self.events = queue.Queue()
        self.busy, self.closing = False, False
        self.cancel = None
        self.callback = None
        self.last_report = None
        self.paper_rows, self.job_rows = {}, {}
        self.workspace = tk.StringVar(value=preferences.get('workspace', str(Path.home() / 'LiteratureExplorer')))
        self.metrics = tk.StringVar(value=preferences.get('metrics', ''))
        self.metrics_status = tk.StringVar()
        self._metrics_automatic = preferences.get('metrics_automatic', not bool(self.metrics.get()))
        self._metric_refresh = None
        self.limit = tk.StringVar(value=preferences.get('limit', '100'))
        self.sort = tk.StringVar(value=preferences.get('sort', 'relevance'))
        self.sources = {key: tk.BooleanVar(value=key in preferences.get('sources', ['pubmed', 'europepmc'])) for key in DATABASES}
        self.offline = tk.BooleanVar(value=False)
        self.auto_open = tk.BooleanVar(value=True)
        self.retrieve_references = tk.BooleanVar(value=False)
        self.build_network = tk.BooleanVar(value=False)
        self.analysis_seed = tk.StringVar(value='Latest report')
        self.discovery_direction = tk.StringVar(value='references')
        self.discovery_limit = tk.StringVar(value='100')
        self.last_analysis_arguments = None
        self.status = tk.StringVar(value='Ready to search')
        self.last_result = None
        root.title('Literature Explorer · ' + __version__)
        root.geometry('1100x850')
        root.minsize(900, 740)
        root.configure(background='#f3f6fa')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background='#f3f6fa')
        style.configure('TLabel', background='#f3f6fa', foreground='#24364b')
        style.configure('TCheckbutton', background='#f3f6fa')
        style.layout('Database.TCheckbutton', style.layout('TRadiobutton'))
        style.configure('Database.TCheckbutton', background='#f3f6fa')
        style.configure('TLabelframe', background='#f3f6fa')
        style.configure('TLabelframe.Label', background='#f3f6fa')
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'), foreground='#12283f')
        style.configure('Hint.TLabel', foreground='#596b7f', font=('Segoe UI', 9))
        style.configure('Accent.TButton', background='#087f8c', foreground='white', padding=(16, 9))
        style.map('Accent.TButton', background=[('active', '#096873'), ('disabled', '#a4b6bd')])
        style.configure('Treeview', rowheight=29, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'))
        body = ttk.Frame(root, padding=22)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Literature Explorer', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, text='Search across databases. Build one library. Explore the evidence.', style='Hint.TLabel').pack(anchor='w', pady=(3, 15))
        workspace_row = ttk.Frame(body)
        workspace_row.pack(fill='x', pady=(0, 14))
        ttk.Label(workspace_row, text='Workspace').pack(side='left', padx=(0, 10))
        self.workspace_entry = ttk.Entry(workspace_row, textvariable=self.workspace)
        self.workspace_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(workspace_row, text='Choose folder', command=self.choose_workspace).pack(side='left', padx=8)
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill='both', expand=True)
        self.search_tab, self.library_tab, self.history_tab, self.analysis_tab = [ttk.Frame(self.notebook, padding=16) for _ in range(4)]
        for frame, label in [(self.search_tab, 'Search'), (self.library_tab, 'Paper library'), (self.history_tab, 'Search history'), (self.analysis_tab, 'Citation analysis')]:
            self.notebook.add(frame, text=label)
        self._search_controls()
        self._library_controls()
        self._history_controls()
        self._analysis_controls()
        self.workspace.trace_add('write', self._workspace_changed)
        self.metrics.trace_add('write', self._metrics_changed)
        if self._metrics_automatic or not self.metrics.get() or not Path(self.metrics.get()).is_file():
            self._detect_metrics()
        self._refresh_metrics()
        footer = ttk.Frame(body)
        self.notebook.pack_forget()
        footer.pack(side='bottom', fill='x', pady=(12, 0))
        self.notebook.pack(fill='both', expand=True)
        self.analysis_progress = ttk.Frame(footer)
        self.analysis_progress.pack(fill='x', pady=(0, 8))
        self.progress_rows, self.progress_bars, self.progress_labels = {}, {}, {}
        self.analysis_counts = {}
        for stage in ('references', 'citations', 'resolution', 'network'):
            row = ttk.Frame(self.analysis_progress)
            label = tk.StringVar()
            ttk.Label(row, textvariable=label).pack(anchor='w')
            bar = ttk.Progressbar(row, mode='determinate', maximum=1)
            bar.pack(fill='x', pady=(3, 6))
            self.progress_rows[stage], self.progress_bars[stage], self.progress_labels[stage] = row, bar, label
        status_row = ttk.Frame(footer)
        status_row.pack(fill='x')
        ttk.Label(status_row, textvariable=self.status).pack(side='left', fill='x', expand=True)
        self.progress = ttk.Progressbar(status_row, mode='indeterminate', length=160)
        self.progress.pack(side='right')
        self.stop_button = ttk.Button(status_row, text='Stop', command=self.stop, state='disabled')
        self.stop_button.pack(side='right', padx=10)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.report_callback_exception = lambda kind,error,trace: messagebox.showerror('Literature Explorer', str(error))
        self.poll_id = root.after(100, self._poll)

    def _search_controls(self):
        canvas = tk.Canvas(self.search_tab, background='#f3f6fa', highlightthickness=0)
        scroll = ttk.Scrollbar(self.search_tab, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        frame = ttk.Frame(canvas)
        item = canvas.create_window((0,0), window=frame, anchor='nw')
        frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
        self.root.bind('<MouseWheel>', lambda event: canvas.yview_scroll(int(-event.delta/120), 'units') if self.notebook.select()==str(self.search_tab) and not isinstance(event.widget, tk.Text) else None, add='+')
        ttk.Label(frame, text='Keywords or database query', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        self.query = tk.Text(frame, height=3, wrap='word', font=('Segoe UI', 11), relief='solid', borderwidth=1)
        self.query.pack(fill='x', pady=(7, 5))
        ttk.Label(frame, text='Use plain keywords across databases. PubMed field tags such as [tiab] apply to PubMed only.', style='Hint.TLabel').pack(anchor='w')
        options = ttk.Frame(frame)
        options.pack(fill='x', pady=13)
        self.database_buttons = {}
        for key, label in DATABASES.items():
            button = ttk.Checkbutton(options, text=label, variable=self.sources[key], style='Database.TCheckbutton')
            button.pack(side='left', padx=(0, 15))
            self.database_buttons[key] = button
        ttk.Label(options, text='Limit per database').pack(side='left', padx=(14, 6))
        ttk.Entry(options, textvariable=self.limit, width=8).pack(side='left')
        ttk.Label(options, text='blank = all', style='Hint.TLabel').pack(side='left', padx=6)
        ttk.Combobox(options, textvariable=self.sort, values=['relevance', 'date'], state='readonly', width=12).pack(side='right')
        filters = ttk.LabelFrame(frame, text='Optional filters', padding=10)
        self.filter_frame = filters
        filters.pack(fill='x')
        self.filters = {}
        for index, (key, label) in enumerate([('date_from', 'From date · YYYY-MM-DD'), ('date_to', 'To date · YYYY-MM-DD'), ('author', 'Author'), ('journal', 'Journal'), ('article_type', 'Database publication type')]):
            row, col = divmod(index, 3)
            filters.columnconfigure(col, weight=1)
            cell = ttk.Frame(filters)
            cell.grid(row=row, column=col, sticky='ew', padx=5, pady=5)
            ttk.Label(cell, text=label).pack(anchor='w')
            self.filters[key] = tk.StringVar()
            ttk.Entry(cell, textvariable=self.filters[key]).pack(fill='x', pady=(3, 0))
        metric_row = ttk.Frame(frame)
        self.metric_row = metric_row
        metric_row.pack(fill='x', pady=(12, 5))
        ttk.Label(metric_row, text='Journal-metric CSV (optional)').pack(side='left', padx=(0, 10))
        ttk.Entry(metric_row, textvariable=self.metrics).pack(side='left', fill='x', expand=True)
        ttk.Button(metric_row, text='Browse', command=self.choose_metrics).pack(side='left', padx=(8, 0))
        ttk.Button(metric_row, text='Clear', command=lambda: self.metrics.set('')).pack(side='left', padx=(8, 0))
        metric_status_row = ttk.Frame(frame)
        metric_status_row.pack(fill='x', pady=(0, 5))
        ttk.Label(metric_status_row, textvariable=self.metrics_status, style='Hint.TLabel', wraplength=640).pack(side='left', fill='x', expand=True)
        self.apply_metrics_button = ttk.Button(metric_status_row, text='Apply CSV to a report', command=self.apply_metrics)
        self.apply_metrics_button.pack(side='right')
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=10)
        self.search_button = ttk.Button(actions, text='Search & build report', style='Accent.TButton', command=self.start_search)
        self.search_button.pack(side='left')
        ttk.Button(actions, text='Load query file', command=self.load_query).pack(side='left', padx=8)
        ttk.Button(actions, text='Open existing report', command=self.open_existing).pack(side='left')
        ttk.Checkbutton(actions, text='Open report when ready', variable=self.auto_open).pack(side='right')
        ttk.Checkbutton(actions, text='Use cache only', variable=self.offline).pack(side='right', padx=10)
        analysis_options = ttk.Frame(frame)
        analysis_options.pack(fill='x', before=filters, pady=(0, 8))
        ttk.Checkbutton(analysis_options, text='Retrieve reference lists (separate JSON)', variable=self.retrieve_references).pack(side='left')
        ttk.Checkbutton(analysis_options, text='Build citation network (requires references)', variable=self.build_network).pack(side='left', padx=12)
        actions.pack(before=filters)
        self.filter_button = ttk.Button(frame, text='Show optional filters', command=self.toggle_filters)
        self.filter_button.pack(fill='x', before=filters, pady=(0,8))
        filters.pack_forget()
        ttk.Label(frame, text='Progress', font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(5, 4))
        self.log = scrolledtext.ScrolledText(frame, height=5, wrap='word', font=('Segoe UI', 9), state='disabled', background='white', relief='flat')
        self.log.pack(fill='both', expand=True)
        report_row = ttk.Frame(frame)
        report_row.pack(fill='x', pady=(8, 0))
        self.open_button = ttk.Button(report_row, text='Open latest HTML report', command=self.open_latest, state='disabled')
        self.open_button.pack(side='left')
        ttk.Button(report_row, text='Open workspace', command=self.open_workspace).pack(side='left', padx=8)

    def toggle_filters(self):
        if self.filter_frame.winfo_manager():
            self.filter_frame.pack_forget()
            self.filter_button.configure(text='Show optional filters')
        else:
            self.filter_frame.pack(fill='x', before=self.metric_row)
            self.filter_button.configure(text='Hide optional filters')

    def _table(self, frame, columns, widths):
        wrap = ttk.Frame(frame)
        wrap.pack(fill='both', expand=True, pady=10)
        table = ttk.Treeview(wrap, columns=columns, show='headings', selectmode='extended')
        for column, width in zip(columns, widths):
            table.heading(column, text=column)
            table.column(column, width=width, minwidth=45)
        scroll = ttk.Scrollbar(wrap, orient='vertical', command=table.yview)
        horizontal = ttk.Scrollbar(wrap, orient='horizontal', command=table.xview)
        table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        table.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        return table

    def _library_controls(self):
        row = ttk.Frame(self.library_tab)
        row.pack(fill='x')
        ttk.Label(row, text='Title / abstract contains').pack(side='left')
        self.library_text = tk.StringVar()
        ttk.Entry(row, textvariable=self.library_text).pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(row, text='Refresh library', command=self.refresh_library).pack(side='left')
        self.library_table = self._table(self.library_tab, ['ID', 'Title', 'Journal', 'Year', 'Review'], [55, 400, 220, 65, 90])
        self.library_table.bind('<Double-1>', lambda event: self.paper_details())
        actions = ttk.Frame(self.library_tab)
        actions.pack(fill='x')
        ttk.Button(actions, text='View paper details', command=self.paper_details).pack(side='left')
        ttk.Button(actions, text='Import bibliography', command=self.import_bibliography).pack(side='left', padx=8)
        ttk.Button(actions, text='Report for selected papers', command=self.report_selection).pack(side='left')
        ttk.Label(self.library_tab, text='Notes, tags, favorites and review decisions can be edited in the HTML explorer.', style='Hint.TLabel').pack(anchor='w', pady=(10, 0))

    def _history_controls(self):
        ttk.Button(self.history_tab, text='Refresh search history', command=self.refresh_history).pack(anchor='w')
        self.history_table = self._table(self.history_tab, ['ID', 'Database', 'Query', 'Status', 'Retrieved'], [55, 110, 430, 95, 85])
        actions = ttk.Frame(self.history_tab)
        actions.pack(fill='x')
        ttk.Button(actions, text='Build report / merge selected searches', command=self.report_history).pack(side='left')
        ttk.Button(actions, text='Resume selected search', command=self.resume_search).pack(side='left', padx=8)
        ttk.Label(self.history_tab, text='Select several rows to combine their results. Each database keeps its own search and rank.', style='Hint.TLabel').pack(anchor='w', pady=(10, 0))

    def _analysis_controls(self):
        frame = self.analysis_tab
        ttk.Label(frame, text='References and citation discovery', font=('Segoe UI', 14, 'bold')).pack(anchor='w', pady=(0, 10))
        ttk.Label(frame, text='Retrieve lists first, then optionally resolve publication identities and metadata.\nA separate reference browser shows consolidated publications, original evidence, and matches for review.', wraplength=780).pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=18)
        ttk.Label(row, text='Seed papers').pack(side='left', padx=(0, 10))
        ttk.Combobox(row, textvariable=self.analysis_seed, values=['Latest report', 'Selected library papers', 'Selected searches'], state='readonly', width=25).pack(side='left')
        ttk.Button(row, text='Open a report', command=self.open_existing).pack(side='left', padx=10)
        row = ttk.Frame(frame)
        row.pack(fill='x')
        ttk.Button(row, text='Retrieve reference lists', command=self.analyze_references).pack(side='left')
        ttk.Checkbutton(row, text='Build citation network', variable=self.build_network).pack(side='left', padx=12)
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=8)
        ttk.Button(row, text='Resolve / enrich retrieved references', command=self.resolve_existing_references).pack(side='left')
        ttk.Button(row, text='Browse references', command=self.browse_references).pack(side='left', padx=12)
        ttk.Label(frame, text='Discovery follows one citation step. The limit counts unique discovered papers, excluding seeds.\nResults are saved in a new report; your keyword search is preserved.', wraplength=780).pack(anchor='w', pady=(22, 10))
        row = ttk.Frame(frame)
        row.pack(fill='x')
        ttk.Label(row, text='Direction').pack(side='left', padx=(0, 10))
        ttk.Combobox(row, textvariable=self.discovery_direction, values=['references', 'citations', 'both'], state='readonly', width=15).pack(side='left')
        ttk.Label(row, text='New-paper limit').pack(side='left', padx=(18, 8))
        ttk.Entry(row, textvariable=self.discovery_limit, width=8).pack(side='left')
        ttk.Button(row, text='Discover related papers', command=self.start_discovery).pack(side='left', padx=12)
        ttk.Button(frame, text='Resume last analysis / discovery', command=self.resume_analysis).pack(anchor='w', pady=18)
        ttk.Label(frame, text='Availability varies by provider. Unresolved citation text is retained in JSON but is not automatically added to the library.\nUse cache only to repeat an analysis without network requests.', wraplength=780, style='Hint.TLabel').pack(anchor='w')

    def _analysis_selection(self):
        mode = self.analysis_seed.get()
        if mode == 'Selected library papers':
            selected = self.library_table.selection()
            if not selected:
                raise ValueError('Select seed papers in the Paper library tab first.')
            return ['--library', '--ids', *selected]
        if mode == 'Selected searches':
            selected = self.history_table.selection()
            if not selected:
                raise ValueError('Select seed searches in the Search history tab first.')
            return ['--search-ids', *selected]
        if not self.last_report or not self.last_report.with_suffix('.json').is_file():
            raise ValueError('Open or create a JSON/HTML report first.')
        return ['--report', str(self.last_report.with_suffix('.json'))]

    def analyze_references(self):
        if self.busy:
            return
        try:
            args = ['references', *self._analysis_selection(), '--output', str(self._output('references')), *self._metric_arguments()]
            if self.build_network.get():
                args.append('--citation-network')
            self.last_analysis_arguments = args
            self._launch(args, self._report_done)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Check analysis settings', str(exc))

    def resolve_existing_references(self):
        if self.busy:
            return
        try:
            reference_file = (self.last_result or {}).get('references')
            if not reference_file:
                reference_file = filedialog.askopenfilename(title='Choose reference observations', filetypes=[('Reference JSON', '*.json')])
            if not reference_file:
                return
            args = ['resolve-references', *self._analysis_selection(), '--references-file', reference_file,
                    '--output', str(self._output('resolved-references')), *self._metric_arguments()]
            if self.build_network.get():
                args.append('--citation-network')
            self.last_analysis_arguments = args
            self._launch(args, self._report_done)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Check analysis settings', str(exc))

    def browse_references(self):
        path = (self.last_result or {}).get('reference_dashboard')
        if not path and self.last_report:
            path = self.last_report.with_suffix('.references.html')
        if path and Path(path).is_file():
            webbrowser.open(Path(path).resolve().as_uri())
        else:
            messagebox.showinfo('References', 'Retrieve reference lists or open an analyzed report first.')

    def start_discovery(self):
        if self.busy:
            return
        try:
            limit = int(self.discovery_limit.get())
            if limit < 1:
                raise ValueError('The discovery limit must be positive.')
            args = ['discover', *self._analysis_selection(), '--direction', self.discovery_direction.get(), '--limit', str(limit),
                    '--output', str(self._output('discovery')), *self._metric_arguments()]
            reference_file = (self.last_result or {}).get('references')
            if self.analysis_seed.get() == 'Latest report' and reference_file and self.discovery_direction.get() in ('references', 'both'):
                args.extend(['--references-file', reference_file])
            self.last_analysis_arguments = args
            self._launch(args, self._report_done)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Check discovery settings', str(exc))

    def resume_analysis(self):
        if not self.busy and self.last_analysis_arguments:
            self._launch(self.last_analysis_arguments, self._report_done)
        elif not self.busy:
            messagebox.showinfo('No analysis to resume', 'Start reference analysis or discovery first. Repeating it resumes its saved checkpoint.')

    def choose_workspace(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(title='Choose a workspace')
        if folder:
            self.workspace.set(folder)
            self.paper_rows.clear()
            self.job_rows.clear()
            self.library_table.delete(*self.library_table.get_children())
            self.history_table.delete(*self.history_table.get_children())

    def _workspace_changed(self, *unused):
        self.paper_rows.clear()
        self.job_rows.clear()
        self.library_table.delete(*self.library_table.get_children())
        self.history_table.delete(*self.history_table.get_children())
        if self._metrics_automatic:
            self._detect_metrics()

    def _detect_metrics(self):
        path = discover_metrics(self.workspace.get())
        self.metrics.set(str(path) if path else '')
        self._metrics_automatic = True

    def _metrics_changed(self, *unused):
        self._metrics_automatic = False
        if self._metric_refresh:
            self.root.after_cancel(self._metric_refresh)
        self._metric_refresh = self.root.after(350, self._refresh_metrics)

    def _refresh_metrics(self):
        self._metric_refresh = None
        path = self.metrics.get().strip()
        try:
            self.metrics_status.set(metric_summary(path) if path else 'No CSV selected. New reports will show IF as unavailable.')
        except (ValueError, OSError) as exc:
            self.metrics_status.set('CSV not loaded: ' + str(exc))

    def _save_preferences(self):
        self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
        self.preferences_path.write_text(json.dumps({'workspace': str(Path(self.workspace.get()).expanduser().resolve()),
            'metrics': self.metrics.get(), 'metrics_automatic': self._metrics_automatic, 'limit': self.limit.get(),
            'sort': self.sort.get(), 'sources': [key for key,value in self.sources.items() if value.get()]}, indent=2), encoding='utf-8')

    def choose_metrics(self):
        path = filedialog.askopenfilename(title='Journal-metric CSV', filetypes=[('CSV files', '*.csv')])
        if path:
            try:
                metric_summary(path)
                self.metrics.set(path)
                self._refresh_metrics()
                self._save_preferences()
            except (ValueError, OSError) as exc:
                messagebox.showerror('Cannot load journal metrics', str(exc))

    def load_query(self):
        path = filedialog.askopenfilename(title='Load a query', filetypes=[('Text files', '*.txt'), ('All files', '*.*')])
        if path:
            try:
                text = Path(path).read_text(encoding='utf-8-sig')
                self.query.delete('1.0', 'end')
                self.query.insert('1.0', text)
            except OSError as exc:
                messagebox.showerror('Cannot load query', str(exc))

    def _output(self, name):
        return Path(self.workspace.get()).expanduser().resolve() / 'outputs' / (name + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')

    def _metric_arguments(self):
        if self.metrics.get().strip():
            metric_summary(self.metrics.get())
            return ['--journal-metrics', self.metrics.get()]
        return ['--no-journal-metrics']

    def apply_metrics(self):
        if self.busy:
            return
        try:
            if not self.metrics.get().strip():
                raise ValueError('Choose a journal-metric CSV first.')
            metric_summary(self.metrics.get())
            path = self.last_report.with_suffix('.json') if self.last_report else None
            if not path or not path.is_file():
                selected = filedialog.askopenfilename(title='Apply metrics to a JSON report', filetypes=[('JSON report', '*.json')])
                if not selected:
                    return
                path = Path(selected)
            self._launch(['apply-metrics', '--output', str(self._output('report-with-if'))], self._report_done, report_file=path)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Cannot apply journal metrics', str(exc))

    def start_search(self):
        try:
            args = search_arguments(self.query.get('1.0', 'end'), [key for key,value in self.sources.items() if value.get()],
                self.limit.get(), self.sort.get(), {key: value.get() for key,value in self.filters.items() if value.get().strip()},
                self._output('search'), self.metrics.get(), self.retrieve_references.get(), self.build_network.get())
            if self.retrieve_references.get() or self.build_network.get():
                # Resume references against saved results, without repeating the keyword search.
                output = args[args.index('--output') + 1]
                self.last_analysis_arguments = ['references', '--report', output, '--output', str(Path(output).with_name(Path(output).stem + '-analysis.json')),
                                               '--references-output', str(Path(output).with_suffix('.references.json')), *self._metric_arguments()]
                if self.build_network.get():
                    self.last_analysis_arguments.append('--citation-network')
            self._launch(args, self._report_done)
        except (ValueError, OSError) as exc:
            messagebox.showerror('Check search settings', str(exc))

    def _launch(self, args, callback, report_file=None):
        if self.busy:
            return
        if not self.workspace.get().strip():
            raise ValueError('Choose a workspace folder.')
        workspace = Path(self.workspace.get()).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self._save_preferences()
        state = workspace / '.literature'
        state.mkdir(exist_ok=True)
        self.cancel = state / ('desktop-cancel-' + datetime.now().strftime('%Y%m%d%H%M%S%f'))
        global_args = ['--state', str(state)]
        if self.offline.get():
            global_args.append('--offline')
        self.busy, self.callback = True, callback
        self.status.set('Working…')
        self.search_button.configure(state='disabled')
        self.apply_metrics_button.configure(state='disabled')
        self.workspace_entry.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.analysis_counts.clear()
        for stage, row in self.progress_rows.items():
            row.pack_forget()
            self.progress_bars[stage].configure(value=0, maximum=1)
        self.progress.configure(mode='indeterminate', value=0)
        self.progress.start(12)
        self._log('Starting ' + args[0] + '…')
        if report_file:
            thread = threading.Thread(target=rebuild_existing, args=(report_file, args[args.index('--output')+1], self.events, self.metrics.get().strip() or None), daemon=True)
        else:
            thread = threading.Thread(target=run_engine, args=([*global_args, *args], workspace, self.events, self.cancel), daemon=True)
        thread.start()

    def _log(self, message):
        self.log.configure(state='normal')
        self.log.insert('end', message + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _poll(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'progress':
                self._log(event[1])
            elif event[0] == 'counts':
                self._analysis_progress(event[1])
            else:
                self.busy = False
                self.progress.stop()
                self.search_button.configure(state='normal')
                self.apply_metrics_button.configure(state='normal')
                self.workspace_entry.configure(state='normal')
                self.stop_button.configure(state='disabled')
                if self.cancel:
                    self.cancel.unlink(missing_ok=True)
                if event[0] == 'error':
                    self.status.set('Unable to complete this action')
                    messagebox.showerror('Literature Explorer', event[1])
                else:
                    _, code, result, details = event
                    self.last_result = result
                    self.status.set('Completed' if code == 0 else 'Stopped; saved results are available in search history' if code == 130 else 'Some results may be incomplete; see progress details')
                    if self.callback and result is not None and not self.closing:
                        self.callback(result)
                    if code not in (0, 130):
                        messagebox.showwarning('Action incomplete', details or 'Review the progress log and search history.')
                if self.closing:
                    self.root.destroy()
                    return
        self.poll_id = self.root.after(100, self._poll)

    def _analysis_progress(self, counts):
        stage = counts['stage']
        done, total = counts['completed'], counts['total']
        self.analysis_counts[stage] = counts
        maximum = max(1, total)
        value = done if total else int(stage != 'network' or counts.get('status') == 'complete')
        percent = 100 * value // maximum
        self.progress_rows[stage].pack(fill='x')
        self.progress_bars[stage].configure(maximum=maximum, value=value)
        if stage == 'network':
            label = (f'Citation network: {done:,}/{total:,} papers processed ({percent:.0f}%)'
                     f' · {counts.get("edges", 0):,} links'
                     + (' · Complete' if counts.get('status') == 'complete' else ''))
        elif stage == 'resolution':
            label = (f'Reference resolution: {done:,}/{total:,} metadata tasks processed ({percent:.0f}%)'
                     f' · {counts.get("resolved", 0):,} referenced publication identities')
        else:
            label = (f'{stage.title()}: {done:,}/{total:,} papers processed ({percent:.0f}%)'
                     f' · {counts.get("with_references", 0):,} with lists'
                     f' · {counts.get("unavailable", 0):,} unavailable'
                     f' · {counts.get("empty", 0):,} empty'
                     f' · {counts.get("issues", 0):,} partial/errors')
        self.progress_labels[stage].set(label)
        self.progress.stop()
        self.progress.configure(mode='determinate', maximum=maximum, value=value)
        if self.stop_button['state'] != 'disabled':
            self.status.set('Building citation network…' if stage == 'network' else f'Retrieving {stage}…')

    def _report_done(self, result):
        if isinstance(result, dict) and result.get('dashboard'):
            self.last_report = Path(result['dashboard'])
            self.open_button.configure(state='normal')
            self._log(str(result.get('paper_count', '?')) + ' unique papers. JSON and HTML saved to ' + str(self.last_report.parent))
            for key in ('references', 'publications', 'reference_dashboard', 'network', 'discovery_checkpoint'):
                if result.get(key):
                    self._log(key.replace('_', ' ').title() + ': ' + str(result[key]))
            try:
                report = json.loads(Path(result['json']).read_text(encoding='utf-8'))
                if report.get('reference_analysis'):
                    c = report['reference_analysis']['coverage']
                    self._log(f"References: {c['raw_observations']:,} raw observations; {c['resolved_distinct_publications']:,} identified publications; "
                              f"{c['unresolved_observations']:,} unresolved; {c['ambiguous_matches']:,} ambiguous; "
                              f"{c['conflicting_observations']:,} conflicting. {c['distinct_citation_edges']:,} citation edges; "
                              f"{c['flagged_self_links']:,} article-to-itself links flagged. Bibliography completeness is unknown.")
                info = report['journal_metrics']
                self._log(f"Impact factors: {info['matched_journals']} of {report['statistics']['journal_count']} journals; {info['matched_papers']} of {result['paper_count']} papers. " + (f"Metric year {info['year']}." if info['year'] else 'No CSV supplied.'))
            except (OSError, ValueError, KeyError):
                pass
            if self.auto_open.get() and not self.closing:
                self.open_latest()

    def stop(self):
        if self.busy and self.cancel:
            self.cancel.touch()
            self.status.set('Stopping after the current request; committed results will be preserved…')
            self.stop_button.configure(state='disabled')

    def close(self):
        if self.busy:
            if messagebox.askyesno('Close Literature Explorer', 'Stop the current action and close after the current request? Saved search pages will be preserved.'):
                self.closing = True
                self.stop()
        else:
            self.root.after_cancel(self.poll_id)
            self.root.destroy()

    def open_latest(self):
        if self.last_report and self.last_report.is_file():
            webbrowser.open(self.last_report.resolve().as_uri())

    def open_workspace(self):
        path = Path(self.workspace.get()).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            os.startfile(path)
        else:
            webbrowser.open(path.as_uri())

    def open_existing(self):
        path = filedialog.askopenfilename(title='Open a report', filetypes=[('HTML report', '*.html'), ('JSON report', '*.json')])
        if path:
            selected = Path(path)
            if selected.suffix.lower() == '.html':
                self.last_report = selected
                self.open_button.configure(state='normal')
                self.open_latest()
            else:
                self._launch(['open-report', '--output', str(self._output('opened-report'))], self._report_done, report_file=path)

    def refresh_library(self):
        args = ['papers']
        if self.library_text.get().strip():
            args += ['--text', self.library_text.get().strip()]
        self._launch(args, self._library_done)

    def _library_done(self, papers):
        self.library_table.delete(*self.library_table.get_children())
        self.paper_rows = {str(p['id']): p for p in papers}
        for key, paper in self.paper_rows.items():
            self.library_table.insert('', 'end', iid=key, values=(key, paper.get('title', ''), paper.get('journal', ''), paper.get('year', ''), paper.get('library', {}).get('status', 'unreviewed')))
        self.status.set(str(len(papers)) + ' papers in this library selection')

    def refresh_history(self):
        self._launch(['jobs'], self._history_done)

    def _history_done(self, jobs):
        self.history_table.delete(*self.history_table.get_children())
        self.job_rows = {str(job['id']): job for job in jobs}
        for key, job in reversed(list(self.job_rows.items())):
            self.history_table.insert('', 'end', iid=key, values=(key, DATABASES.get(job['source'], job['source']), job['query'], job['status'], job['retrieved']))
        self.status.set(str(len(jobs)) + ' saved database searches')

    def report_history(self):
        selected = self.history_table.selection()
        if not selected:
            messagebox.showinfo('Select searches', 'Select one or more saved searches first.')
            return
        self._launch(['merge', '--search-ids', *selected, '--output', str(self._output('merged-history')), *self._metric_arguments()], self._report_done)

    def resume_search(self):
        selected = self.history_table.selection()
        if len(selected) != 1:
            messagebox.showinfo('Select one search', 'Select one interrupted or failed search to resume.')
            return
        if self.job_rows[selected[0]]['status'] not in ('running', 'failed', 'interrupted'):
            messagebox.showinfo('Search already finished', 'This search is finished. Start a new search to retrieve a different scope.')
            return
        self._launch(['resume', selected[0], '--output', str(self._output('resumed')), *self._metric_arguments()], self._report_done)

    def report_selection(self):
        selected = self.library_table.selection()
        if not selected:
            messagebox.showinfo('Select papers', 'Select the papers to include in the report.')
            return
        self._launch(['visualize', '--library', '--ids', *selected, '--output', str(self._output('selected-papers')), *self._metric_arguments()], self._report_done)

    def import_bibliography(self):
        path = filedialog.askopenfilename(title='Import a bibliography', filetypes=[('Bibliographies', '*.json *.csv *.ris *.bib'), ('All files', '*.*')])
        if path:
            self._launch(['import', path], lambda result: self.refresh_library())

    def paper_details(self):
        selected = self.library_table.selection()
        if not selected:
            return
        paper = self.paper_rows[selected[0]]
        window = tk.Toplevel(self.root)
        window.title('Paper details')
        window.geometry('760x560')
        text = scrolledtext.ScrolledText(window, wrap='word', font=('Segoe UI', 11), padx=16, pady=16)
        text.pack(fill='both', expand=True)
        text.insert('1.0', '\n\n'.join([paper.get('title') or '(Title unavailable)', paper.get('authors') or '',
            (paper.get('journal') or '') + ' · ' + str(paper.get('year') or ''),
            'DOI: ' + (paper.get('doi') or 'unavailable'), paper.get('abstract') or 'Abstract unavailable.']))
        text.configure(state='disabled')


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
