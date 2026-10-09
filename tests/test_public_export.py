import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.prepare_github import audit_archive, check_text


class PublicExportTests(unittest.TestCase):
    def test_compressed_portable_with_real_metric_table_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive=Path(directory)/'portable.zip'
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as file:
                file.writestr('journal-metrics.csv','journal,impact_factor,year,source\n')
            with self.assertRaises(ValueError):
                audit_archive(archive)

    def test_nested_private_state_in_source_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive=Path(directory)/'source.tar.gz'
            with tarfile.open(archive,'w:gz') as file:
                item=tarfile.TarInfo('project/.literature/catalog.sqlite3')
                item.size=3
                file.addfile(item,io.BytesIO(b'data'))
            with self.assertRaises(ValueError):
                audit_archive(archive)

    def test_machine_path_is_rejected_but_generic_home_code_is_allowed(self):
        private=str(Path.home()/'saved-settings.json')
        with self.assertRaises(ValueError):
            check_text('README.md',private.encode())
        check_text('source.py',b"directory = Path.home() / 'LiteratureExplorer'")


if __name__ == '__main__':
    unittest.main()
