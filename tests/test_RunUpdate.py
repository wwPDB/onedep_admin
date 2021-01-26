import os
import sys
import unittest
from unittest.mock import MagicMock

sys.modules['wwpdb.utils.config.ConfigInfo'] = MagicMock()
from scripts.RunUpdate import get_latest_version_filepath

class RunUpdateTest(unittest.TestCase):
    """Tests for the RunUpdate module
    """
    onedep_admin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    def test_config_version(self):
        """
            Tests the latest version feature.
        """
        os.mkdir(os.path.join(self.onedep_admin_path, 'V999'))
        latest = get_latest_version_filepath()
        self.assertEqual(latest, os.path.join(self.onedep_admin_path, 'V999', 'V999rel.conf'))

        os.mkdir(os.path.join(self.onedep_admin_path, 'V999.2.10'))
        latest = get_latest_version_filepath()
        self.assertEqual(latest, os.path.join(self.onedep_admin_path, 'V999.2.10', 'V999210rel.conf'))

        os.mkdir(os.path.join(self.onedep_admin_path, 'V998.500'))
        latest = get_latest_version_filepath()
        self.assertEqual(latest, os.path.join(self.onedep_admin_path, 'V999.2.10', 'V999210rel.conf'))

    @classmethod
    def tearDownClass(self):
        os.rmdir(os.path.join(self.onedep_admin_path, 'V999'))
        os.rmdir(os.path.join(self.onedep_admin_path, 'V999.2.10'))
        os.rmdir(os.path.join(self.onedep_admin_path, 'V998.500'))

if __name__ == '__main__':
    unittest.main()