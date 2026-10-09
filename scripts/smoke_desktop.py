"""Exercise desktop widgets and the real background engine with offline fixtures."""
import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from literature.gui import DesktopApp, enable_dpi_awareness
from scripts.verify_release import cache_fixture


def settle(root, app, timeout=60):
    end = time.monotonic() + timeout
    while app.busy and time.monotonic() < end:
        root.update()
        time.sleep(.03)
    root.update()
    assert not app.busy, 'Desktop action timed out'


def capture(root, path):
    root.update()
    user = ctypes.windll.user32
    handle = user.GetParent(root.winfo_id())
    rect = (ctypes.c_long * 4)()
    user.GetWindowRect(handle, rect)
    path.parent.mkdir(parents=True, exist_ok=True)
    code = """param([string]$target,[int]$x,[int]$y,[int]$width,[int]$height)
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class DesktopCapture { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
[DesktopCapture]::SetProcessDPIAware() | Out-Null
$bitmap=New-Object System.Drawing.Bitmap($width,$height)
$graphics=[System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($x,$y,0,0,$bitmap.Size)
$bitmap.Save($target,[System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
"""
    helper = path.parent / 'capture-desktop.ps1'
    helper.write_text(code, encoding='utf-8')
    process = subprocess.Popen(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(helper), str(path), *map(str,[rect[0],rect[1],rect[2]-rect[0],rect[3]-rect[1]])], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    end = time.monotonic()+20
    while process.poll() is None and time.monotonic()<end:
        root.update()  # PrintWindow needs the target window to process paint events.
        time.sleep(.02)
    if process.poll() is None:
        process.kill()
        raise RuntimeError('Window capture timed out')
    if process.returncode:
        raise RuntimeError(process.stderr.read().decode(errors='replace'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', help='Frozen CLI executable to exercise from the desktop')
    parser.add_argument('--screenshot')
    args = parser.parse_args()
    errors = []
    enable_dpi_awareness()
    with tempfile.TemporaryDirectory(prefix='literature-desktop-test-') as directory:
        workspace = Path(directory)
        cache_fixture(workspace / '.literature')
        root = tk.Tk()
        app = DesktopApp(root, workspace/'preferences.json')
        assert app.query.get('1.0','end-1c') == '', 'A new explorer must start with an empty query'
        assert 'Radiobutton.indicator' in str(ttk.Style(root).layout('Database.TCheckbutton'))
        app.database_buttons['crossref'].invoke()
        assert all(value.get() for value in app.sources.values()), 'Database choices must allow multiple selections'
        app.database_buttons['pubmed'].invoke()
        assert not app.sources['pubmed'].get() and app.sources['europepmc'].get() and app.sources['crossref'].get()
        root.report_callback_exception = lambda kind,error,trace: errors.append(str(error))
        app.workspace.set(str(workspace))
        app.metrics.set('')  # Explicit opt-out must bypass the source checkout's automatic table.
        app.auto_open.set(False)
        app.offline.set(True)
        app.limit.set('1')
        for value in app.sources.values():
            value.set(True)
        app.query.delete('1.0','end')
        app.query.insert('1.0','RELEASE_TEST_FIXTURE')
        with patch('literature.gui.messagebox.showerror', side_effect=lambda title,text: errors.append(text)), patch('literature.gui.messagebox.showwarning', side_effect=lambda title,text: errors.append(text)), patch('literature.gui.webbrowser.open') as browser:
            if args.engine:
                command_patch = patch('literature.gui.engine_command', return_value=[str(Path(args.engine).resolve())])
            else:
                command_patch = patch('literature.gui.engine_command', return_value=[sys.executable,'-m','literature'])
            with command_patch:
                root.update()
                app.filter_button.invoke()
                assert app.filter_frame.winfo_manager()=='pack'
                app.filter_button.invoke()
                assert not app.filter_frame.winfo_manager()
                app.search_button.invoke()
                assert app.busy
                settle(root,app)
                assert app.last_report.is_file()
                data=json.loads(app.last_report.with_suffix('.json').read_text(encoding='utf-8'))
                assert data['merge']['duplicates_removed']==2
                assert len(data['papers'])==1
                original_json=app.last_report.with_suffix('.json')
                with patch('literature.gui.filedialog.askopenfilename', return_value=str(original_json)):
                    app.open_existing()
                settle(root,app)
                reopened=json.loads(app.last_report.with_suffix('.json').read_text(encoding='utf-8'))
                assert reopened==data
                assert '1 unique papers' in app.log.get('1.0','end')
                app.open_button.invoke()
                browser.assert_called_once()
                metrics=workspace/'journal-metrics.csv'
                metrics.write_text('journal,issn,impact_factor,year,source\nSynthetic Journal,1234-5678,7.5,2025,Fixture\n',encoding='utf-8')
                with patch('literature.gui.filedialog.askopenfilename',return_value=str(metrics)):
                    app.choose_metrics()
                assert 'Loaded 1 journals' in app.metrics_status.get()
                app.apply_metrics_button.invoke()
                settle(root,app)
                enriched=json.loads(app.last_report.with_suffix('.json').read_text(encoding='utf-8'))
                assert enriched['papers'][0]['journal_metric']['impact_factor']==7.5
                assert 'Impact factors: 1 of 1 journals' in app.log.get('1.0','end')
                assert json.loads(original_json.read_text(encoding='utf-8'))==data
                assert json.loads(app.preferences_path.read_text())['metrics']==str(metrics)
                app.refresh_library()
                settle(root,app)
                assert len(app.library_table.get_children())==1
                app.library_table.selection_set(app.library_table.get_children()[0])
                app.report_selection()
                settle(root,app)
                assert app.last_result['paper_count']==1
                app.refresh_history()
                settle(root,app)
                assert len(app.history_table.get_children())==3
                app.history_table.selection_set(*app.history_table.get_children())
                app.report_history()
                settle(root,app)
                assert app.last_result['paper_count']==1
                app.search_button.invoke()
                app.stop_button.invoke()
                settle(root,app)
                assert app.status.get().startswith('Stopped')
                app.refresh_history()
                settle(root,app)
                interrupted = [key for key,job in app.job_rows.items() if job['status']=='interrupted']
                assert len(interrupted)==1
                app.history_table.selection_set(interrupted[0])
                app.resume_search()
                settle(root,app)
                assert app.last_result['paper_count']==1
                app.query.delete('1.0','end')
                app.offline.set(False)
                app.limit.set('100')
                app.status.set('Ready to search')
                if args.screenshot:
                    root.deiconify()
                    root.lift()
                    root.update()
                    time.sleep(.3)
                    capture(root,Path(args.screenshot).resolve())
                assert not errors, errors
        app.close()
    print('Desktop checks passed: empty startup query, independent dot selections, search, deduplication, reports, CSV, library/history, stop, and resume.')


if __name__ == '__main__':
    main()
