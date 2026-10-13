"""Tests for RunUpdate.py --ebi-compat (scripts/RunUpdateEbi.py)

Everything external (git, pip, databases, containers) is mocked at the subprocess
and configuration boundaries; nothing here touches a deployment.
"""
import contextlib
import importlib
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
        importlib.import_module('wwpdb.utils.config.ConfigInfo')
        importlib.import_module('wwpdb.utils.config.ConfigInfoApp')
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
    """Option parsing and dispatch"""

    def test_help_lists_new_options(self):
        status, out, _ = self.run_main('--help')
        self.assertEqual(status, 0)
        self.assertIn('--ebi-compat', out)
        self.assertIn('--build-container', out)

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

    def test_build_dev_cannot_be_combined(self):
        with mock.patch('scripts.RunUpdateEbi.run_ebi_update') as ebi:
            status, _, err = self.run_main('--ebi-compat', '--build-dev')
        self.assertEqual(status, 2)
        self.assertIn('--build-dev cannot be combined with --ebi-compat', err)
        ebi.assert_not_called()

    def test_build_container_requires_flag(self):
        status, _, err = self.run_main('--build-container', '/x/y.sif')
        self.assertEqual(status, 2)
        self.assertIn('--build-container requires --ebi-compat', err)

    def test_build_tools_requires_container(self):
        with mock.patch('scripts.RunUpdateEbi.run_ebi_update') as ebi:
            status, _, err = self.run_main('--ebi-compat', '--build-tools')
        self.assertEqual(status, 2)
        self.assertIn('--build-tools with --ebi-compat requires --build-container', err)
        ebi.assert_not_called()

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


from scripts import RunUpdateEbi  # noqa: E402
from scripts.RunUpdateEbi import EbiUpdate, UpdateError, parse_pins, redact, run_ebi_update  # noqa: E402

PHASES = ['phase_preflight', 'phase_resources', 'phase_remove', 'phase_pip', 'phase_webfe',
          'phase_taxdb', 'phase_schema', 'phase_build', 'phase_verify']


def make_args(**overrides):
    """Namespace as RunUpdate.main() would build it for --ebi-compat"""
    values = dict(config='latest', noop=False, skip_pip=False, skip_remove=False, skip_resources=False,
                  skip_webfe=False, skip_taxdb=False, skip_schema=False, skip_toolvers=False,
                  build_tools=False, build_version='v-5200', build_dev=False, get_latest_version=False,
                  ebi_compat=True, build_container=None)
    values.update(overrides)
    return types.SimpleNamespace(**values)


class _FakeProcess(object):
    def __init__(self, returncode, output):
        self.returncode = returncode
        self.stdout = iter(output.splitlines(True))

    def wait(self):
        return self.returncode


class Procs(object):
    """Scripted stand-in for subprocess.Popen: the first matching rule wins, default is exit 0"""

    def __init__(self):
        self.rules = []
        self.calls = []

    def on(self, match, returncode=0, output=''):
        """match is a substring of the space-joined argv, or a callable taking the argv list"""
        self.rules.append((match, returncode, output))

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(types.SimpleNamespace(argv=argv, kwargs=kwargs, line=' '.join(argv)))
        for match, returncode, output in self.rules:
            hit = match(argv) if callable(match) else match in ' '.join(argv)
            if hit:
                return _FakeProcess(returncode, output)
        return _FakeProcess(0, '')

    def lines(self):
        return [c.line for c in self.calls]


class EbiTestBase(unittest.TestCase):
    """An EbiUpdate over a throwaway release configuration, with every subprocess scripted"""

    CONF = '[DEFAULT]\n'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.conf = os.path.join(self.tmp, 'rel.conf')
        with open(self.conf, 'w') as fout:
            fout.write(self.CONF)
        self.site = {}
        self.procs = Procs()
        for patcher in (
                mock.patch.object(RunUpdate.UpdateManager, 'get_variable',
                                  side_effect=lambda name, environment=None: self.site.get(name)),
                mock.patch.object(RunUpdateEbi.subprocess, 'Popen', self.procs)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_update(self, **flags):
        self.args = make_args(**flags)
        return EbiUpdate(self.conf, self.args)

    def quiet(self, fn, *a, **kw):
        """Call fn with stdout captured; returns (result, stdout)"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*a, **kw)
        return result, out.getvalue()


class RedactTest(unittest.TestCase):
    """Credentials never reach logs or error messages"""

    def test_url_userinfo_is_masked(self):
        text = redact('pip install --index-url https://alice:s3cret@host.example/simple/ pkg')
        self.assertNotIn('s3cret', text)
        self.assertNotIn('alice', text)
        self.assertIn('https://***@host.example/simple/', text)

    def test_literal_secret_is_masked(self):
        self.assertEqual(redact('token=abc123 done', ['abc123']), 'token=*** done')

    def test_empty_and_missing_secrets_are_ignored(self):
        self.assertEqual(redact('plain text', ['', None]), 'plain text')


class RunCoreTest(EbiTestBase):
    """Argument-vector execution, failure propagation and noop"""

    def test_runs_argument_vector_without_a_shell(self):
        upd = self.make_update()
        self.quiet(upd._run, 'resources', ['git', 'fetch', '--tags'], cwd='/some/repo')
        call = self.procs.calls[0]
        self.assertEqual(call.argv, ['git', 'fetch', '--tags'])
        self.assertFalse(call.kwargs.get('shell'))
        self.assertEqual(call.kwargs['cwd'], '/some/repo')

    def test_extra_environment_is_merged_over_the_inherited_one(self):
        upd = self.make_update()
        with mock.patch.dict(os.environ, {'KEEP_ME': '1'}):
            self.quiet(upd._run, 'pip', ['pip'], env={'ADDED': '2'})
        env = self.procs.calls[0].kwargs['env']
        self.assertEqual((env['KEEP_ME'], env['ADDED']), ('1', '2'))

    def test_returns_exit_status_and_output(self):
        self.procs.on('git describe', 0, 'v6.1\n')
        upd = self.make_update()
        result, _ = self.quiet(upd._run, 'resources', ['git', 'describe'], mutating=False)
        self.assertEqual((result.returncode, result.output), (0, 'v6.1\n'))

    def test_failure_names_phase_and_exit_status(self):
        self.procs.on('git checkout', 3, 'error: pathspec\n')
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd._run, 'resources', ['git', 'checkout', 'v9'])
        self.assertEqual(ctx.exception.phase, 'resources')
        self.assertIn('exit 3', ctx.exception.detail)
        self.assertIn('git checkout v9', ctx.exception.detail)

    def test_failure_can_be_tolerated_by_the_caller(self):
        self.procs.on('pip show', 1)
        upd = self.make_update()
        result, _ = self.quiet(upd._run, 'remove', ['pip', 'show', 'x'], mutating=False, check=False)
        self.assertEqual(result.returncode, 1)

    def test_missing_executable_is_reported_as_an_error(self):
        upd = self.make_update()
        with mock.patch.object(RunUpdateEbi.subprocess, 'Popen', side_effect=FileNotFoundError('no such file')):
            with self.assertRaises(UpdateError) as ctx:
                self.quiet(upd._run, 'build', ['singularity', 'exec'])
        self.assertEqual(ctx.exception.phase, 'build')
        self.assertIn('singularity exec', ctx.exception.detail)

    def test_credentials_are_redacted_in_log_error_and_echoed_output(self):
        self.site.update({'CS_USER': 'alice', 'CS_PW': 's3cret'})
        self.procs.on('pip', 1, 'Looking in https://alice:s3cret@host/simple\n')
        upd = self.make_update()
        out = io.StringIO()
        with self.assertRaises(UpdateError) as ctx, contextlib.redirect_stdout(out):
            upd._run('pip', ['pip', 'install', '--index-url', 'https://alice:s3cret@host/simple', 'x'])
        self.assertNotIn('s3cret', ctx.exception.detail)
        self.assertNotIn('s3cret', out.getvalue())
        self.assertIn('pip install', out.getvalue())

    def test_noop_logs_but_does_not_run_mutating_commands(self):
        upd = self.make_update(noop=True)
        result, out = self.quiet(upd._run, 'resources', ['git', 'checkout', 'v6.1'])
        self.assertEqual(self.procs.calls, [])
        self.assertEqual(result.returncode, 0)
        self.assertIn('git checkout v6.1', out)

    def test_noop_still_runs_read_only_commands(self):
        upd = self.make_update(noop=True)
        self.quiet(upd._run, 'preflight', ['git', 'status', '--porcelain'], mutating=False)
        self.assertEqual(self.procs.lines(), ['git status --porcelain'])


class OrchestrationTest(EbiTestBase):
    """Phase order, skip flags and exit status of run_ebi_update"""

    def setUp(self):
        super().setUp()
        self.order = []
        for name in PHASES:
            patcher = mock.patch.object(EbiUpdate, name, side_effect=lambda n=name: self.order.append(n))
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_update(self, **flags):
        return self.quiet(run_ebi_update, self.conf, make_args(**flags))

    def test_default_phase_order_has_no_tool_build(self):
        status, _ = self.run_update()
        self.assertEqual(status, 0)
        self.assertEqual(self.order, ['phase_preflight', 'phase_resources', 'phase_remove', 'phase_pip',
                                      'phase_webfe', 'phase_taxdb', 'phase_schema', 'phase_verify'])

    def test_build_tools_adds_the_build_phase_before_verification(self):
        self.run_update(build_tools=True, build_container='/x/y.sif')
        self.assertEqual(self.order[-2:], ['phase_build', 'phase_verify'])

    def test_each_skip_flag_removes_exactly_its_phase(self):
        cases = {'skip_resources': 'phase_resources', 'skip_remove': 'phase_remove', 'skip_pip': 'phase_pip',
                 'skip_webfe': 'phase_webfe', 'skip_taxdb': 'phase_taxdb', 'skip_schema': 'phase_schema'}
        for flag, phase in cases.items():
            del self.order[:]
            self.run_update(**{flag: True})
            expected = ['phase_preflight', 'phase_resources', 'phase_remove', 'phase_pip', 'phase_webfe',
                        'phase_taxdb', 'phase_schema', 'phase_verify']
            expected.remove(phase)
            self.assertEqual(self.order, expected, flag)

    def test_verification_and_preflight_run_even_when_everything_else_is_skipped(self):
        self.run_update(skip_resources=True, skip_remove=True, skip_pip=True, skip_webfe=True,
                        skip_taxdb=True, skip_schema=True, skip_toolvers=True)
        self.assertEqual(self.order, ['phase_preflight', 'phase_verify'])

    def test_noop_still_runs_every_phase_so_each_can_print_its_plan(self):
        self.run_update(noop=True)
        self.assertEqual(len(self.order), 8)

    def test_first_failure_stops_the_run_with_status_1(self):
        EbiUpdate.phase_pip.side_effect = UpdateError('pip', 'pin foo==1 failed (exit 1)')
        status, out = self.run_update()
        self.assertEqual(status, 1)
        self.assertEqual(self.order, ['phase_preflight', 'phase_resources', 'phase_remove'])
        self.assertIn('FAILED [pip]: pin foo==1 failed (exit 1)', out)

    def test_failed_preflight_prevents_any_change(self):
        EbiUpdate.phase_preflight.side_effect = UpdateError('preflight', 'no such file')
        status, _ = self.run_update(noop=True)
        self.assertEqual(status, 1)
        self.assertEqual(self.order, [])

    def test_failure_message_never_shows_credentials(self):
        self.site.update({'CS_PW': 's3cret'})
        EbiUpdate.phase_pip.side_effect = UpdateError('pip', 'failed on https://u:s3cret@h/simple')
        _, out = self.run_update()
        self.assertNotIn('s3cret', out)


class ParsePinsTest(unittest.TestCase):
    """Release requirements files, including markers, comments and direct references"""

    def parse(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'rel')
            with open(path, 'w') as fout:
                fout.write(text)
            return parse_pins(path)

    def test_order_comments_and_blank_lines(self):
        pins = self.parse('# header\n\nfoo==1.0\n  \nBar_Baz==2.1  # trailing note\nqux==3\n')
        self.assertEqual([p.line for p in pins], ['foo==1.0', 'Bar_Baz==2.1', 'qux==3'])
        self.assertEqual([p.name for p in pins], ['foo', 'bar-baz', 'qux'])

    def test_markers_are_evaluated_for_this_interpreter(self):
        pins = self.parse('old==1 ; python_version < "3.0"\nnew==2 ; python_version >= "3.0"\nplain==3\n')
        self.assertEqual([p.name for p in pins], ['new', 'plain'])
        self.assertEqual(pins[0].line, 'new==2 ; python_version >= "3.0"')

    def test_direct_reference_is_kept(self):
        pins = self.parse('pkg @ file:///opt/src/pkg\n')
        self.assertEqual([p.name for p in pins], ['pkg'])
        self.assertEqual(pins[0].line, 'pkg @ file:///opt/src/pkg')

    def test_option_lines_are_rejected_with_their_line_number(self):
        for text in ('a==1\n-r other.txt\n', 'a==1\n\n--extra-index-url https://u:p@h/\n', '-e .\n'):
            with self.assertRaises(UpdateError) as ctx:
                self.parse(text)
            self.assertEqual(ctx.exception.phase, 'preflight')
            self.assertRegex(ctx.exception.detail, r'line \d+')
            self.assertNotIn('u:p', ctx.exception.detail)

    def test_invalid_requirement_is_rejected_with_its_line_number(self):
        with self.assertRaises(UpdateError) as ctx:
            self.parse('good==1\nthis is not a requirement\n')
        self.assertIn('line 2', ctx.exception.detail)


class PreflightBase(EbiTestBase):
    """A release whose inputs are all valid; tests break one thing at a time"""

    PINS = 'foo==1.0\nmysqlclient==2.1.1\n'

    def setUp(self):
        super().setUp()
        base = os.path.join(self.tmp, 'base_packages')
        os.mkdir(base)
        for name in ('constraints.txt', 'pre-requirements.txt', 'requirements.txt'):
            self.touch(os.path.join(base, name))
        self.reqs = self.touch(os.path.join(self.tmp, 'rel-pins'), self.PINS)
        self.webfe_conf = self.touch(os.path.join(self.tmp, 'webfe.rel'), 'x==1\n')
        self.installed = {'mysqlclient': '2.1.1'}

        def version(name):
            try:
                return self.installed[name]
            except KeyError:
                raise RunUpdateEbi.importlib.metadata.PackageNotFoundError(name)

        for patcher in (mock.patch.object(RunUpdateEbi, 'BASE_PACKAGES_DIR', base),
                        mock.patch.object(RunUpdateEbi.importlib.metadata, 'version', side_effect=version),
                        mock.patch.object(sys, 'prefix', '/deploy/venv'),
                        mock.patch.object(sys, 'base_prefix', '/usr')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write_conf()

    def touch(self, path, text=''):
        with open(path, 'w') as fout:
            fout.write(text)
        return path

    def write_conf(self, **overrides):
        keys = dict(resourcestag='v6.1', webfetag='v6.1', webfeconf=self.webfe_conf, taxdbminsize='100',
                    taxdbmaxsize='200', piprequirements=self.reqs)
        keys.update(overrides)
        self.touch(self.conf, '[DEFAULT]\n' + ''.join('{} = {}\n'.format(k, v) for k, v in keys.items()
                                                      if v is not None))

    def problems(self, **flags):
        """Message of the UpdateError raised by preflight, or '' if it passes"""
        upd = self.make_update(**flags)
        try:
            self.quiet(upd.phase_preflight)
        except UpdateError as e:
            self.assertEqual(e.phase, 'preflight')
            return e.detail
        return ''


class PreflightConfigTest(PreflightBase):
    """Release configuration, interpreter and requirements files"""

    def test_valid_release_passes_without_running_anything(self):
        self.assertEqual(self.problems(), '')
        self.assertEqual(self.procs.calls, [])

    def test_missing_keys_are_named(self):
        for key in ('resourcestag', 'webfetag', 'webfeconf', 'taxdbminsize', 'piprequirements'):
            self.write_conf(**{key: None})
            self.assertIn(key, self.problems(), key)

    def test_missing_files_are_named(self):
        self.write_conf(piprequirements=os.path.join(self.tmp, 'nope'))
        self.assertIn(os.path.join(self.tmp, 'nope'), self.problems())
        self.write_conf(webfeconf=os.path.join(self.tmp, 'nada'))
        self.assertIn(os.path.join(self.tmp, 'nada'), self.problems())

    def test_missing_base_package_file_is_named(self):
        os.remove(os.path.join(RunUpdateEbi.BASE_PACKAGES_DIR, 'constraints.txt'))
        self.assertIn('constraints.txt', self.problems())

    def test_interpreter_must_be_a_virtualenv(self):
        with mock.patch.object(sys, 'prefix', '/usr'):
            self.assertIn('virtual environment', self.problems())

    def test_every_problem_is_reported_together(self):
        self.write_conf(webfetag=None, taxdbminsize=None)
        with mock.patch.object(sys, 'prefix', '/usr'):
            detail = self.problems()
        for expected in ('webfetag', 'taxdbminsize', 'virtual environment'):
            self.assertIn(expected, detail)

    def test_skipped_phases_do_not_require_their_inputs(self):
        self.write_conf(resourcestag=None, webfetag=None, webfeconf=None, taxdbminsize=None,
                        piprequirements=None)
        self.assertEqual(self.problems(skip_resources=True, skip_webfe=True, skip_taxdb=True, skip_pip=True), '')

    def test_release_pins_that_cannot_be_installed_one_by_one_fail_early(self):
        self.touch(self.reqs, 'foo==1.0\n-r more.txt\n')
        self.assertIn('line 2', self.problems())


class PreflightMysqlclientTest(PreflightBase):
    """The installed mysqlclient must already be the release's, since it is never reinstalled"""

    def test_matching_version_passes(self):
        self.assertEqual(self.problems(), '')

    def test_different_version_fails_with_both_versions_and_the_remedy(self):
        self.installed['mysqlclient'] = '2.2.0'
        detail = self.problems()
        self.assertIn('mysqlclient', detail)
        self.assertIn('2.1.1', detail)
        self.assertIn('2.2.0', detail)
        self.assertIn('rebuild', detail)

    def test_not_installed_fails(self):
        del self.installed['mysqlclient']
        self.assertIn('not installed', self.problems())

    def test_release_without_a_mysqlclient_pin_passes(self):
        self.touch(self.reqs, 'foo==1.0\n')
        self.installed['mysqlclient'] = '9.9'
        self.assertEqual(self.problems(), '')

    def test_pin_that_is_not_exact_fails(self):
        self.touch(self.reqs, 'mysqlclient>=2.1\n')
        self.assertIn('exact', self.problems())

    def test_ignored_when_pip_phase_is_skipped(self):
        self.installed['mysqlclient'] = '2.2.0'
        self.assertEqual(self.problems(skip_pip=True), '')


if __name__ == '__main__':
    unittest.main()
