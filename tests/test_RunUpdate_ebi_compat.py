"""Tests for RunUpdate.py --ebi-compat (scripts/RunUpdateEbi.py)

Everything external (git, pip, databases, containers) is mocked at the subprocess
and configuration boundaries; nothing here touches a deployment.
"""
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock


def _install_wwpdb_stubs():
    """Make scripts.RunUpdate importable where the wwpdb packages are not installed"""
    try:
        import wwpdb.utils.config.ConfigInfo  # noqa: F401
        import wwpdb.utils.config.ConfigInfoApp  # noqa: F401
        return
    except ImportError:
        pass
    modules = {}
    for name in ('wwpdb', 'wwpdb.utils', 'wwpdb.utils.config', 'wwpdb.utils.config.ConfigInfo',
                 'wwpdb.utils.config.ConfigInfoApp'):
        modules[name] = types.ModuleType(name)
    modules['wwpdb.utils.config.ConfigInfo'].ConfigInfo = mock.MagicMock
    modules['wwpdb.utils.config.ConfigInfoApp'].ConfigInfoAppCommon = mock.MagicMock
    modules['wwpdb.utils.config.ConfigInfoApp'].ConfigInfoAppValidation = mock.MagicMock
    for name, mod in modules.items():
        sys.modules.setdefault(name, mod)


_install_wwpdb_stubs()

from scripts import RunUpdate  # noqa: E402


class MainTestBase(unittest.TestCase):
    """Runs RunUpdate.main() with a throwaway release configuration file"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conf = os.path.join(tmp.name, 'rel.conf')
        with open(self.conf, 'w') as fout:
            fout.write('[DEFAULT]\n')

    def run_main(self, *argv):
        """Returns (exit status or None, stdout, stderr)"""
        out, err = io.StringIO(), io.StringIO()
        status = None
        with mock.patch.object(sys, 'argv', ['RunUpdate.py', '--config', self.conf] + list(argv)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    RunUpdate.main()
                except SystemExit as e:
                    status = e.code
        return status, out.getvalue(), err.getvalue()


class EbiCompatCliTest(MainTestBase):
    """Slice 1: option parsing and dispatch"""

    @unittest.expectedFailure
    def test_help_lists_new_options(self):
        status, out, _ = self.run_main('--help')
        self.assertEqual(status, 0)
        self.assertIn('--ebi-compat', out)
        self.assertIn('--build-container', out)

    @unittest.expectedFailure
    def test_flag_dispatches_to_ebi_update_and_returns_its_status(self):
        with mock.patch('scripts.RunUpdateEbi.run_ebi_update', return_value=3) as ebi, \
                mock.patch.object(RunUpdate, 'UpdateManager') as legacy:
            status, _, _ = self.run_main('--ebi-compat', '--noop', '--skip-taxdb')
        self.assertEqual(status, 3)
        legacy.assert_not_called()
        config_file, args = ebi.call_args[0]
        self.assertEqual(config_file, self.conf)
        self.assertTrue(args.ebi_compat)
        self.assertTrue(args.noop)
        self.assertTrue(args.skip_taxdb)
        self.assertFalse(args.skip_pip)

    @unittest.expectedFailure
    def test_build_dev_cannot_be_combined(self):
        with mock.patch('scripts.RunUpdateEbi.run_ebi_update') as ebi:
            status, _, err = self.run_main('--ebi-compat', '--build-dev')
        self.assertEqual(status, 2)
        self.assertIn('--build-dev cannot be combined with --ebi-compat', err)
        ebi.assert_not_called()

    @unittest.expectedFailure
    def test_build_container_requires_flag(self):
        status, _, err = self.run_main('--build-container', '/x/y.sif')
        self.assertEqual(status, 2)
        self.assertIn('--build-container requires --ebi-compat', err)

    @unittest.expectedFailure
    def test_build_tools_requires_container(self):
        with mock.patch('scripts.RunUpdateEbi.run_ebi_update') as ebi:
            status, _, err = self.run_main('--ebi-compat', '--build-tools')
        self.assertEqual(status, 2)
        self.assertIn('--build-tools with --ebi-compat requires --build-container', err)
        ebi.assert_not_called()

    @unittest.expectedFailure
    def test_build_tools_with_container_is_accepted(self):
        with mock.patch('scripts.RunUpdateEbi.run_ebi_update', return_value=0) as ebi:
            status, _, _ = self.run_main('--ebi-compat', '--build-tools', '--build-container', '/x/y.sif',
                                         '--build-version', 'v-6000')
        self.assertEqual(status, 0)
        args = ebi.call_args[0][1]
        self.assertEqual(args.build_container, '/x/y.sif')
        self.assertEqual(args.build_version, 'v-6000')


class LegacyDispatchTest(MainTestBase):
    """Without --ebi-compat the original sequence runs and the new module is never loaded"""

    def test_legacy_sequence_unchanged_and_ebi_module_not_imported(self):
        with mock.patch.dict(sys.modules):
            sys.modules.pop('scripts.RunUpdateEbi', None)
            with mock.patch.object(RunUpdate, 'UpdateManager') as legacy:
                status, _, _ = self.run_main()
                self.assertNotIn('scripts.RunUpdateEbi', sys.modules)
        self.assertIsNone(status)
        um = legacy.return_value
        self.assertEqual(legacy.call_args[0], (self.conf, False))
        names = [c[0] for c in um.method_calls]
        self.assertEqual(names, ['updateresources', 'removepyenv', 'updatepyenv', 'updatewebfe',
                                 'updatetaxdb', 'updateschema', 'checktoolvers', 'checkoelicense',
                                 'postflightdbcheck', 'checkwebfe'])
        um.updatepyenv.assert_called_once_with(False)
        um.checkwebfe.assert_called_once_with(overridenoop=True)

    def test_legacy_rejects_build_container(self):
        # Not a legacy option: the flag is only meaningful together with --ebi-compat
        with mock.patch.object(RunUpdate, 'UpdateManager') as legacy:
            status, _, _ = self.run_main('--build-container', '/x/y.sif')
        self.assertEqual(status, 2)
        legacy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
