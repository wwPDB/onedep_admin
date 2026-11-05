import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.modules['wwpdb.utils.config.ConfigInfo'] = MagicMock()
from scripts.RunUpdate import UpdateManager, get_latest_version_filepath, release_pins

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


class ReleasePinsTest(unittest.TestCase):
    """Tests for reading the release pins of a requirements file
    """

    def pins(self, text):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as fout:
            fout.write(text)
        try:
            return release_pins(fout.name)
        finally:
            os.remove(fout.name)

    def test_skips_comments_and_blank_lines(self):
        self.assertEqual(self.pins("# a comment\n\nasgiref==3.8.1\n   \n  # indented comment\nbcrypt==5.0.0  # trailing\n"),
                         ['asgiref==3.8.1', 'bcrypt==5.0.0'])

    def test_skips_mysqlclient(self):
        self.assertEqual(self.pins("asgiref==3.8.1\nmysqlclient==2.2.7\nMySQLclient==2.1.1\nbcrypt==5.0.0\n"),
                         ['asgiref==3.8.1', 'bcrypt==5.0.0'])

    def test_evaluates_environment_markers(self):
        self.assertEqual(self.pins('cffi==2.0.0 ; python_version >= "3"\ncffi==1.17.1 ; python_version < "3"\n'),
                         ['cffi==2.0.0'])

    def test_marker_is_not_part_of_the_pin(self):
        self.assertEqual(self.pins('cffi==2.0.0  ; python_version >= "3"\n'), ['cffi==2.0.0'])


class UpdateManagerPinsAndMigrationsTest(unittest.TestCase):
    """Tests for the pin by pin install and the DepUI migrations of UpdateManager
    """

    def manager(self, results):
        manager = UpdateManager.__new__(UpdateManager)
        manager._UpdateManager__exec = MagicMock(side_effect=results)
        return manager

    def commands(self, manager):
        return [call[0][0] for call in manager._UpdateManager__exec.call_args_list]

    def test_installpins_installs_each_pin_without_dependencies(self):
        manager = self.manager([0, 0])
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as fout:
            fout.write("asgiref==3.8.1\nbcrypt==5.0.0\n")
        try:
            manager.installpins(fout.name, '-c constraints.txt')
        finally:
            os.remove(fout.name)
        self.assertEqual(self.commands(manager), ['pip install --no-deps -c constraints.txt "asgiref==3.8.1"',
                                                  'pip install --no-deps -c constraints.txt "bcrypt==5.0.0"'])

    def test_installpins_stops_at_the_first_failure(self):
        manager = self.manager([0, 1, 0])
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as fout:
            fout.write("asgiref==3.8.1\nbcrypt==5.0.0\nchardet==5.2.0\n")
        try:
            with self.assertRaises(SystemExit) as raised:
                manager.installpins(fout.name, '-c constraints.txt')
        finally:
            os.remove(fout.name)
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(self.commands(manager)), 2)

    def test_updatedjango_shows_the_plan_before_and_after_migrating(self):
        manager = self.manager([0, 0, 0])
        manager.updatedjango()
        commands = self.commands(manager)
        self.assertEqual(len(commands), 3)
        self.assertIn('django migrate --plan', commands[0])
        self.assertIn('django migrate --noinput', commands[1])
        self.assertIn('django migrate --plan', commands[2])
        for command in commands:
            self.assertIn('DJANGO_SETTINGS_MODULE=', command)

    def test_updatedjango_stops_when_the_migration_fails(self):
        manager = self.manager([0, 1, 0])
        with self.assertRaises(SystemExit) as raised:
            manager.updatedjango()
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(self.commands(manager)), 2)


class UpdateManagerFetchTagsTest(unittest.TestCase):
    """Tests for the opt-in git fetch --tags of the resources and WebFE updates
    """

    def manager(self, root):
        manager = UpdateManager.__new__(UpdateManager)
        manager._UpdateManager__exec = MagicMock(return_value=0)
        manager._UpdateManager__cparser = MagicMock()
        manager._UpdateManager__cparser.get.return_value = 'v6.1'
        manager._UpdateManager__resources_ro_path = root
        manager._UpdateManager__resources_rw_path = None
        manager._UpdateManager__web_apps_path = os.path.join(root, 'webapps')
        return manager

    def commands(self, manager):
        return [call[0][0] for call in manager._UpdateManager__exec.call_args_list]

    def test_updateresources_fetches_tags_before_checking_out_when_asked(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.manager(root)
            manager.updateresources(fetch_tags=True)
        command = self.commands(manager)[0]
        self.assertIn('git fetch --tags', command)
        self.assertLess(command.index('git fetch --tags'), command.index('git checkout v6.1'))

    def test_updateresources_does_not_fetch_tags_by_default(self):
        with tempfile.TemporaryDirectory() as root:
            manager = self.manager(root)
            manager.updateresources()
        self.assertNotIn('git fetch', self.commands(manager)[0])

    def test_updatewebfe_fetches_tags_before_checking_out_when_asked(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'webapps'))
            manager = self.manager(root)
            manager.updatewebfe(fetch_tags=True)
        command = self.commands(manager)[0]
        self.assertIn('git fetch --tags', command)
        self.assertLess(command.index('git fetch --tags'), command.index('git checkout v6.1'))

    def test_updatewebfe_does_not_fetch_tags_by_default(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'webapps'))
            manager = self.manager(root)
            manager.updatewebfe()
        self.assertNotIn('git fetch', self.commands(manager)[0])

if __name__ == '__main__':
    unittest.main()