"""Tests for RunUpdate.py --ebi-compat (scripts/RunUpdateEbi.py)

Everything external (git, pip, databases, containers) is mocked at the subprocess
and configuration boundaries; nothing here touches a deployment.
"""
import contextlib
import importlib
import io
import os
import shutil
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
        self.ro = os.path.join(self.tmp, 'resources_ro')
        os.mkdir(self.ro)
        self.site['RO_RESOURCE_PATH'] = self.ro
        self.tools = os.path.join(self.tmp, 'tools')
        os.mkdir(self.tools)
        self.site['TOOLS_PATH'] = self.tools
        self.webfe = os.path.join(self.tmp, 'onedep-webfe')
        os.makedirs(os.path.join(self.webfe, 'webapps'))
        self.site['TOP_WWPDB_WEBAPPS_DIR'] = os.path.join(self.webfe, 'webapps')

        def version(name):
            try:
                return self.installed[name]
            except KeyError:
                raise RunUpdateEbi.importlib.metadata.PackageNotFoundError(name)

        for patcher in (mock.patch.object(RunUpdateEbi, 'BASE_PACKAGES_DIR', base),
                        mock.patch.object(RunUpdateEbi.importlib.util, 'find_spec', return_value=object()),
                        mock.patch.object(RunUpdateEbi.importlib.metadata, 'version', side_effect=version),
                        mock.patch.object(sys, 'prefix', '/deploy/venv'),
                        mock.patch.object(sys, 'base_prefix', '/usr')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write_conf()

    def make_update(self, **flags):
        upd = super().make_update(**flags)
        upd._UpdateManager__ci_common = mock.Mock(get_site_packages_path=lambda: self.tools + '/packages')
        return upd

    def touch(self, path, text=''):
        with open(path, 'w') as fout:
            fout.write(text)
        return path

    def write_conf(self, **overrides):
        keys = dict(resourcestag='v6.1', webfetag='v6.1', webfeconf=self.webfe_conf, taxdbminsize='100',
                    taxdbmaxsize='200', piprequirements=self.reqs, taxuseftp='True')
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

    def test_valid_release_passes_without_changing_anything(self):
        self.assertEqual(self.problems(), '')
        changing = ('fetch', 'checkout', 'pull', 'install', 'uninstall', 'migrate', 'update')
        for call in self.procs.calls:
            self.assertFalse(set(call.argv) & set(changing), call.line)

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


class ResourcesPreflightTest(PreflightBase):
    """The resources repositories must be usable git checkouts without local changes"""

    def git_rule(self, repo, word, returncode=0, output=''):
        self.procs.on(lambda a: a[:3] == ['git', '-C', repo] and word in a, returncode, output)

    def test_unset_resources_path_is_reported(self):
        del self.site['RO_RESOURCE_PATH']
        self.assertIn('RO_RESOURCE_PATH', self.problems())

    def test_missing_directory_is_planned_for_cloning_not_an_error(self):
        os.rmdir(self.ro)
        self.assertEqual(self.problems(), '')
        self.assertFalse([c for c in self.procs.calls if 'clone' in c.argv])

    def test_missing_directory_in_an_unwritable_place_is_an_error(self):
        os.rmdir(self.ro)
        with mock.patch.object(RunUpdateEbi.os, 'access', return_value=False):
            detail = self.problems()
        self.assertIn(self.ro, detail)
        self.assertIn('not writable', detail)

    def test_directory_that_is_not_a_git_checkout_is_reported(self):
        self.git_rule(self.ro, 'rev-parse', 128)
        self.assertIn('not a git checkout', self.problems())

    def test_tracked_changes_are_reported_and_not_touched(self):
        self.git_rule(self.ro, 'status', 0, ' M version.json\n')
        detail = self.problems()
        self.assertIn(self.ro, detail)
        self.assertIn('uncommitted changes', detail)
        self.assertIn('version.json', detail)

    def test_clean_status_ignores_untracked_files(self):
        self.problems()
        status = [c for c in self.procs.calls if 'status' in c.argv][0]
        self.assertIn('--untracked-files=no', status.argv)

    def test_writable_copy_is_checked_too_and_must_be_on_a_branch(self):
        rw = os.path.join(self.tmp, 'resources_rw')
        os.mkdir(rw)
        self.site['RW_RESOURCE_PATH'] = rw
        self.git_rule(rw, 'symbolic-ref', 1)
        detail = self.problems()
        self.assertIn(rw, detail)
        self.assertIn('branch', detail)

    def test_nothing_is_checked_when_resources_are_skipped(self):
        del self.site['RO_RESOURCE_PATH']
        self.assertEqual(self.problems(skip_resources=True), '')
        self.assertFalse([c for c in self.procs.calls if self.ro in c.argv])


class ResourcesPhaseTest(PreflightBase):
    """The release tag is fetched and checked out; a detached HEAD at a tag is the normal end state"""

    def lines(self):
        return self.procs.lines()

    def test_fetch_tags_then_checkout_then_verify_and_never_pull(self):
        self.procs.on('describe --tags --exact-match', 0, 'v6.1\n')
        upd = self.make_update()
        self.quiet(upd.phase_resources)
        git = ['git -C ' + self.ro + ' ' + c for c in
               ('fetch --tags', 'rev-parse -q --verify refs/tags/v6.1', 'checkout v6.1',
                'describe --tags --exact-match')]
        self.assertEqual(self.lines(), git)

    def test_a_ref_that_is_neither_tag_nor_branch_stops_before_checkout(self):
        self.procs.on('rev-parse -q --verify', 1)
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_resources)
        self.assertEqual(ctx.exception.phase, 'resources')
        self.assertIn('v6.1 is neither a tag nor a branch', ctx.exception.detail)
        self.assertFalse([line for line in self.lines() if 'checkout' in line])

    def use_branch(self, ref='develop'):
        self.write_conf(resourcestag=ref)
        self.procs.on('refs/tags/' + ref, 1)
        self.procs.on('--abbrev-ref HEAD', 0, ref + '\n')

    def test_a_branch_is_checked_out_and_fast_forwarded(self):
        self.use_branch()
        upd = self.make_update()
        self.quiet(upd.phase_resources)
        git = ['git -C ' + self.ro + ' ' + c for c in
               ('fetch --tags', 'rev-parse -q --verify refs/tags/develop',
                'rev-parse -q --verify refs/remotes/origin/develop', 'checkout develop',
                'pull --ff-only origin develop', 'rev-parse --abbrev-ref HEAD')]
        self.assertEqual(self.lines(), git)

    def test_a_tag_wins_over_a_branch_of_the_same_name(self):
        self.procs.on('describe --tags --exact-match', 0, 'v6.1\n')
        self.procs.on('refs/remotes/origin/v6.1', 0)
        upd = self.make_update()
        self.quiet(upd.phase_resources)
        self.assertFalse([line for line in self.lines() if 'pull' in line or 'refs/remotes' in line])

    def test_a_failed_fast_forward_is_fatal(self):
        self.use_branch()
        self.procs.rules.insert(0, ('pull --ff-only', 1, 'fatal: Not possible to fast-forward\n'))
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_resources)
        self.assertEqual(ctx.exception.phase, 'resources')
        self.assertIn('pull --ff-only origin develop', ctx.exception.detail)

    def test_the_branch_must_be_the_one_checked_out(self):
        self.use_branch()
        self.procs.rules.insert(0, ('--abbrev-ref HEAD', 0, 'HEAD\n'))
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_resources)
        self.assertIn('develop', ctx.exception.detail)
        self.assertIn('HEAD', ctx.exception.detail)

    def test_a_branch_under_noop_only_prints_the_plan(self):
        self.use_branch()
        upd = self.make_update(noop=True)
        _, out = self.quiet(upd.phase_resources)
        self.assertEqual(self.procs.calls, [])
        self.assertIn('checkout develop', out)

    def test_checkout_must_land_exactly_on_the_tag(self):
        self.procs.on('describe --tags --exact-match', 0, 'v6.0\n')
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_resources)
        self.assertIn('v6.0', ctx.exception.detail)
        self.assertIn('v6.1', ctx.exception.detail)

    def test_failed_fetch_is_fatal_and_nothing_follows(self):
        self.procs.on('fetch --tags', 1)
        upd = self.make_update()
        with self.assertRaises(UpdateError):
            self.quiet(upd.phase_resources)
        self.assertEqual(len(self.lines()), 1)

    def test_writable_copy_is_fast_forwarded_when_configured(self):
        rw = os.path.join(self.tmp, 'resources_rw')
        self.site['RW_RESOURCE_PATH'] = rw
        self.procs.on('describe --tags --exact-match', 0, 'v6.1\n')
        upd = self.make_update()
        self.quiet(upd.phase_resources)
        self.assertEqual(self.lines()[-1], 'git -C {} pull --ff-only'.format(rw))

    def test_noop_runs_nothing(self):
        upd = self.make_update(noop=True)
        _, out = self.quiet(upd.phase_resources)
        self.assertEqual(self.procs.calls, [])
        self.assertIn('fetch --tags', out)
        self.assertIn('checkout v6.1', out)


    def test_a_missing_checkout_is_cloned_first(self):
        os.rmdir(self.ro)
        self.procs.on('describe --tags --exact-match', 0, 'v6.1\n')
        upd = self.make_update()
        self.quiet(upd.phase_resources)
        self.assertEqual(self.lines()[:2], ['git clone git@github.com:wwPDB/onedep-resources_ro.git ' + self.ro,
                                            'git -C ' + self.ro + ' fetch --tags'])

    def test_a_missing_writable_copy_is_cloned_then_updated(self):
        rw = os.path.join(self.tmp, 'resources_rw')
        self.site['RW_RESOURCE_PATH'] = rw
        self.procs.on('describe --tags --exact-match', 0, 'v6.1\n')
        upd = self.make_update()
        self.quiet(upd.phase_resources)
        self.assertEqual(self.lines()[-2:], ['git clone git@github.com:wwPDB/onedep-resources_rw.git ' + rw,
                                             'git -C {} pull --ff-only'.format(rw)])

    def test_existing_checkouts_are_not_cloned(self):
        self.procs.on('describe --tags --exact-match', 0, 'v6.1\n')
        upd = self.make_update()
        self.quiet(upd.phase_resources)
        self.assertFalse([line for line in self.lines() if ' clone ' in line])

    def test_a_failed_clone_is_fatal_and_nothing_follows(self):
        os.rmdir(self.ro)
        self.procs.on('clone', 128)
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_resources)
        self.assertEqual(ctx.exception.phase, 'resources')
        self.assertEqual(len(self.lines()), 1)

    def test_noop_prints_the_clone_without_running_it(self):
        os.rmdir(self.ro)
        upd = self.make_update(noop=True)
        _, out = self.quiet(upd.phase_resources)
        self.assertEqual(self.procs.calls, [])
        self.assertIn('git clone git@github.com:wwPDB/onedep-resources_ro.git ' + self.ro, out)


class RemovePackagesTest(PreflightBase):
    """Obsolete packages are uninstalled only when the installed version is exactly the listed one"""

    def setUp(self):
        super().setUp()
        self.write_conf(removepy='emda=1.1.4  servalcat=0.4.72 ')

    def test_exact_match_is_uninstalled_and_other_cases_are_recorded_skips(self):
        self.installed.update({'emda': '1.1.4', 'servalcat': '0.5.0'})
        upd = self.make_update()
        _, out = self.quiet(upd.phase_remove)
        self.assertEqual(self.procs.lines(), ['{} -m pip uninstall -y emda'.format(sys.executable)])
        self.assertIn('servalcat: installed 0.5.0, not 0.4.72, skipped', out)

    def test_absent_package_is_a_recorded_skip_not_an_error(self):
        upd = self.make_update()
        _, out = self.quiet(upd.phase_remove)
        self.assertEqual(self.procs.calls, [])
        self.assertIn('emda: not installed, skipped', out)

    def test_no_removepy_option_means_nothing_to_do(self):
        self.write_conf()
        upd = self.make_update()
        self.quiet(upd.phase_remove)
        self.assertEqual(self.procs.calls, [])

    def test_failed_uninstall_is_fatal(self):
        self.installed['emda'] = '1.1.4'
        self.procs.on('uninstall', 1)
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_remove)
        self.assertEqual(ctx.exception.phase, 'remove')

    def test_noop_does_not_uninstall(self):
        self.installed['emda'] = '1.1.4'
        upd = self.make_update(noop=True)
        _, out = self.quiet(upd.phase_remove)
        self.assertEqual(self.procs.calls, [])
        self.assertIn('pip uninstall -y emda', out)

    def test_malformed_entry_is_a_preflight_problem(self):
        self.write_conf(removepy='emda')
        self.assertIn("removepy entry 'emda' is not name=version", self.problems())


class PipPreflightTest(PreflightBase):
    """The tools tree and the package index configuration are checked without echoing credentials"""

    def test_tools_path_must_be_set_and_exist(self):
        del self.site['TOOLS_PATH']
        self.assertIn('TOOLS_PATH', self.problems())
        self.site['TOOLS_PATH'] = os.path.join(self.tmp, 'absent')
        self.assertIn(os.path.join(self.tmp, 'absent'), self.problems())

    def test_tools_path_is_not_needed_when_pip_is_skipped(self):
        del self.site['TOOLS_PATH']
        self.assertEqual(self.problems(skip_pip=True), '')

    def test_index_host_must_already_be_in_the_pip_configuration(self):
        self.site['CS_URL'] = 'https://repo.example.org/pypi'
        self.procs.on('pip config list', 0, "global.extra-index-url='https://pypi.example.net/simple'\n")
        self.assertIn('repo.example.org', self.problems())

    def test_configured_index_passes_and_its_credentials_are_never_echoed(self):
        self.site['CS_URL'] = 'https://repo.example.org/pypi'
        self.site['CS_PW'] = 's3cret'
        self.procs.on('pip config list', 0,
                      "global.extra-index-url='https://u:s3cret@repo.example.org/dist/simple/'\n")
        upd = self.make_update()
        _, out = self.quiet(upd.phase_preflight)
        self.assertTrue([c for c in self.procs.calls if c.argv[-2:] == ['config', 'list']])
        self.assertNotIn('s3cret', out)
        self.assertNotIn('extra-index-url', out)

    def test_pip_configuration_is_not_read_without_a_package_index(self):
        self.problems()
        self.assertFalse([c for c in self.procs.calls if 'config' in c.argv])


class PipPhaseTest(PreflightBase):
    """Base requirements, then each release pin installed alone with --no-deps"""

    PINS = ('# pins\nfoo==1.0\nmysqlclient==2.1.1\nbar==2 ; python_version < "3.0"\n'
            'baz==3 ; python_version >= "3.0"\nqux @ file:///opt/qux\n')

    def setUp(self):
        super().setUp()
        self.constraints = os.path.join(RunUpdateEbi.BASE_PACKAGES_DIR, 'constraints.txt')
        self.base = RunUpdateEbi.BASE_PACKAGES_DIR
        self.pip = '{} -m pip'.format(sys.executable)
        self.procs.on(lambda a: a[0] == sys.executable and '-c' in a[1:3], 0, 'MYSQL_SO=/venv/MySQLdb/_mysql.so\n')

    def installs(self):
        return [line for line in self.procs.lines() if ' install ' in line]

    def test_base_requirements_then_pins_in_file_order_with_no_deps(self):
        upd = self.make_update()
        self.quiet(upd.phase_pip)
        self.assertEqual(self.installs(), [
            '{} install -c {} -r {}/pre-requirements.txt'.format(self.pip, self.constraints, self.base),
            '{} install -c {} -r {}/requirements.txt'.format(self.pip, self.constraints, self.base),
            '{} install --no-deps -c {} foo==1.0'.format(self.pip, self.constraints),
            '{} install --no-deps -c {} baz==3 ; python_version >= "3.0"'.format(self.pip, self.constraints),
            '{} install --no-deps -c {} qux @ file:///opt/qux'.format(self.pip, self.constraints)])

    def test_pkg_config_path_is_set_for_the_base_requirements(self):
        upd = self.make_update()
        self.quiet(upd.phase_pip)
        expected = '{0}/lib/pkgconfig:{0}/packages/openbabel/lib/pkgconfig'.format(self.tools)
        base = [c for c in self.procs.calls if ' install ' in c.line and '-r' in c.argv]
        self.assertEqual(len(base), 2)
        for call in base:
            self.assertEqual(call.kwargs['env']['PKG_CONFIG_PATH'], expected)

    def test_mysqlclient_is_never_reinstalled(self):
        upd = self.make_update()
        self.quiet(upd.phase_pip)
        self.assertFalse([line for line in self.installs() if 'mysqlclient' in line])

    def test_first_failing_pin_is_named_and_stops_the_phase(self):
        self.procs.on('--no-deps -c {} baz==3'.format(self.constraints), 1)
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_pip)
        self.assertEqual(ctx.exception.phase, 'pip')
        self.assertIn('baz==3', ctx.exception.detail)
        self.assertFalse([line for line in self.procs.lines() if 'qux' in line or 'pip check' in line])

    def test_failing_base_requirements_stop_before_any_pin(self):
        self.procs.on('requirements.txt', 1)
        upd = self.make_update()
        with self.assertRaises(UpdateError):
            self.quiet(upd.phase_pip)
        self.assertFalse([line for line in self.installs() if '--no-deps' in line])

    def test_pip_check_follows_the_installs_and_must_be_clean(self):
        upd = self.make_update()
        self.quiet(upd.phase_pip)
        lines = self.procs.lines()
        self.assertLess(max(i for i, line in enumerate(lines) if ' install ' in line),
                        lines.index('{} check'.format(self.pip)))
        self.procs.on('pip check', 1, 'foo 1.0 has requirement bar, but you have bar 0.1.\n')
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_pip)
        self.assertEqual(ctx.exception.phase, 'pip')
        self.assertIn('pip check', ctx.exception.detail)

    def test_extra_requirements_use_the_environment_for_credentials(self):
        extra = self.touch(os.path.join(self.tmp, 'extra.txt'), 'extra==1\n')
        self.write_conf(pip_extra_reqs=extra)
        self.site.update({'CS_USER': 'alice', 'CS_PW': 's3cret', 'CS_URL': 'https://repo.example.org/pypi'})
        upd = self.make_update()
        self.quiet(upd.phase_pip)
        call = [c for c in self.procs.calls if extra in c.argv][0]
        self.assertEqual(call.line, '{} install --no-deps -c {} -r {}'.format(self.pip, self.constraints, extra))
        env = call.kwargs['env']
        self.assertEqual((env['CS_USER'], env['CS_PW']), ('alice', 's3cret'))
        self.assertEqual((env['URL_NETLOC'], env['URL_PATH']), ('repo.example.org', '/pypi'))
        self.assertFalse([c for c in self.procs.calls if 's3cret' in c.line])

    def test_pip_configuration_is_never_rewritten_and_pip_is_always_the_active_interpreters(self):
        upd = self.make_update()
        self.quiet(upd.phase_pip)
        self.assertFalse([line for line in self.procs.lines() if ' config ' in line])
        self.assertFalse([c for c in self.procs.calls if c.argv[0] == 'pip'])

    def test_noop_installs_nothing(self):
        upd = self.make_update(noop=True)
        _, out = self.quiet(upd.phase_pip)
        self.assertEqual(self.procs.calls, [])
        self.assertIn('--no-deps -c {} foo==1.0'.format(self.constraints), out)


class MysqlclientLinkageTest(PreflightBase):
    """The built extension must import and resolve every shared library"""

    LDD_OK = ('\tlibmysqlclient.so.21 => /usr/lib64/mysql/libmysqlclient.so.21 (0x1)\n'
              '\tlibc.so.6 => /lib64/libc.so.6 (0x2)\n')

    def setUp(self):
        super().setUp()
        self.is_probe = lambda a: a[0] == sys.executable and '-c' in a[1:3]
        self.procs.on(self.is_probe, 0, 'MYSQL_SO=/venv/MySQLdb/_mysql.so\n')
        self.procs.on('ldd', 0, self.LDD_OK)

    def test_import_and_escape_string_then_ldd_of_the_extension(self):
        upd = self.make_update()
        self.quiet(upd.check_mysqlclient_linkage)
        probe, ldd = self.procs.calls
        self.assertEqual(probe.argv[:2], [sys.executable, '-c'])
        self.assertIn('import MySQLdb', probe.argv[2])
        self.assertIn('escape_string', probe.argv[2])
        self.assertEqual(ldd.argv, ['ldd', '/venv/MySQLdb/_mysql.so'])

    def test_unresolved_library_is_fatal_and_named(self):
        self.procs.rules.insert(0, ('ldd', 0, '\tlibmysqlclient.so.21 => not found\n'))
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.check_mysqlclient_linkage)
        self.assertEqual(ctx.exception.phase, 'mysqlclient')
        self.assertIn('libmysqlclient.so.21', ctx.exception.detail)
        self.assertIn('not found', ctx.exception.detail)

    def test_import_failure_is_fatal_and_ldd_is_not_attempted(self):
        self.procs.rules.insert(0, (self.is_probe, 1, 'ImportError: GLIBCXX_3.4.29 not found\n'))
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.check_mysqlclient_linkage)
        self.assertEqual(ctx.exception.phase, 'mysqlclient')
        self.assertIn('GLIBCXX_3.4.29', ctx.exception.detail)
        self.assertFalse([line for line in self.procs.lines() if line.startswith('ldd')])

    def test_runs_even_under_noop_because_it_only_reads(self):
        upd = self.make_update(noop=True)
        self.quiet(upd.check_mysqlclient_linkage)
        self.assertEqual(len(self.procs.calls), 2)

    def test_no_preload_workaround_is_applied(self):
        upd = self.make_update()
        self.quiet(upd.check_mysqlclient_linkage)
        for call in self.procs.calls:
            self.assertEqual(call.kwargs['env'].get('LD_PRELOAD'), os.environ.get('LD_PRELOAD'))


class WebfePreflightTest(PreflightBase):
    """The WebFE checkout must be clean, untracked files included; nothing is removed on the operator's behalf"""

    def git_rule(self, word, returncode=0, output=''):
        self.procs.on(lambda a: a[:3] == ['git', '-C', self.webfe] and word in a, returncode, output)

    def test_unset_webapps_dir_is_reported(self):
        del self.site['TOP_WWPDB_WEBAPPS_DIR']
        self.assertIn('TOP_WWPDB_WEBAPPS_DIR', self.problems())

    def test_missing_checkout_is_planned_for_cloning_not_an_error(self):
        shutil.rmtree(self.webfe)
        self.assertEqual(self.problems(), '')
        self.assertFalse([c for c in self.procs.calls if 'clone' in c.argv])

    def test_missing_checkout_in_an_unwritable_place_is_an_error(self):
        shutil.rmtree(self.webfe)
        with mock.patch.object(RunUpdateEbi.os, 'access', return_value=False):
            detail = self.problems()
        self.assertIn(self.webfe, detail)
        self.assertIn('not writable', detail)

    def test_directory_that_is_not_a_git_checkout_is_reported(self):
        self.git_rule('rev-parse', 128)
        self.assertIn('not a git checkout', self.problems())

    def test_untracked_files_are_listed_with_instructions_and_left_alone(self):
        self.git_rule('status', 0, '?? webapps/htdocs/report.html\n?? webapps/htdocs/reports/\n')
        detail = self.problems()
        self.assertIn('webapps/htdocs/report.html', detail)
        self.assertIn('move', detail)
        status = [c for c in self.procs.calls if 'status' in c.argv and self.webfe in c.argv][0]
        self.assertNotIn('--untracked-files=no', status.argv)
        self.assertFalse([c for c in self.procs.calls if set(c.argv) & {'clean', 'stash', 'reset', 'rm'}])

    def test_tracked_modifications_are_rejected(self):
        self.git_rule('status', 0, ' M webapps/htdocs/server_depui.wsgi\n')
        self.assertIn('server_depui.wsgi', self.problems())

    def test_nothing_is_checked_when_webfe_is_skipped(self):
        del self.site['TOP_WWPDB_WEBAPPS_DIR']
        self.assertEqual(self.problems(skip_webfe=True), '')
        self.assertFalse([c for c in self.procs.calls if self.webfe in c.argv])


class WebfePhaseTest(PreflightBase):
    """Tag-safe checkout, submodules, then ManageWebFE.py check; every failure is fatal"""

    def setUp(self):
        super().setUp()
        self.git = 'git -C {} '.format(self.webfe)
        self.checker = '{} {} --webroot {} check -r {}'.format(
            sys.executable, os.path.join(os.path.dirname(os.path.abspath(RunUpdateEbi.__file__)), 'ManageWebFE.py'),
            self.webfe, self.webfe_conf)
        self.procs.on('describe --tags --exact-match', 0, 'v6.1\n')

    def test_full_sequence(self):
        upd = self.make_update()
        self.quiet(upd.phase_webfe)
        self.assertEqual(self.procs.lines(), [
            self.git + 'fetch --tags',
            self.git + 'rev-parse -q --verify refs/tags/v6.1',
            self.git + 'checkout v6.1',
            self.git + 'describe --tags --exact-match',
            self.git + 'submodule update --init --recursive',
            self.checker])

    def test_a_branch_is_checked_out_and_fast_forwarded_before_the_submodules(self):
        self.write_conf(webfetag='develop')
        self.procs.on('refs/tags/develop', 1)
        self.procs.on('--abbrev-ref HEAD', 0, 'develop\n')
        upd = self.make_update()
        self.quiet(upd.phase_webfe)
        self.assertEqual(self.procs.lines(), [
            self.git + 'fetch --tags',
            self.git + 'rev-parse -q --verify refs/tags/develop',
            self.git + 'rev-parse -q --verify refs/remotes/origin/develop',
            self.git + 'checkout develop',
            self.git + 'pull --ff-only origin develop',
            self.git + 'rev-parse --abbrev-ref HEAD',
            self.git + 'submodule update --init --recursive',
            self.checker])

    def test_a_ref_that_is_neither_tag_nor_branch_stops_before_checkout(self):
        self.procs.on('rev-parse -q --verify', 1)
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_webfe)
        self.assertEqual(ctx.exception.phase, 'webfe')
        self.assertIn('v6.1 is neither a tag nor a branch', ctx.exception.detail)
        self.assertFalse([line for line in self.procs.lines() if 'checkout' in line])

    def test_failed_submodule_update_is_fatal_and_the_check_does_not_run(self):
        self.procs.on('submodule update', 1)
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_webfe)
        self.assertEqual(ctx.exception.phase, 'webfe')
        self.assertNotIn(self.checker, self.procs.lines())

    def test_failed_check_is_fatal(self):
        self.procs.on('ManageWebFE.py', 1, 'Mismatch htdocs/wfm   6.1 != 6.0\n')
        upd = self.make_update()
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_webfe)
        self.assertEqual(ctx.exception.phase, 'webfe')
        self.assertIn('ManageWebFE.py', ctx.exception.detail)

    def test_noop_changes_and_checks_nothing(self):
        upd = self.make_update(noop=True)
        _, out = self.quiet(upd.phase_webfe)
        self.assertEqual(self.procs.calls, [])
        self.assertIn('checkout v6.1', out)
        self.assertIn('submodule update --init --recursive', out)

    def test_never_pulls_cleans_stashes_or_resets(self):
        upd = self.make_update()
        self.quiet(upd.phase_webfe)
        for call in self.procs.calls:
            self.assertFalse(set(call.argv) & {'pull', 'clean', 'stash', 'reset', 'rm'}, call.line)


    def test_a_missing_checkout_is_cloned_with_submodules_first(self):
        shutil.rmtree(self.webfe)
        upd = self.make_update()
        self.quiet(upd.phase_webfe)
        self.assertEqual(self.procs.lines()[0],
                         'git clone --recurse-submodules git@github.com:wwPDB/onedep-webfe.git ' + self.webfe)
        self.assertEqual(self.procs.lines()[1], self.git + 'fetch --tags')

    def source_directory_missing(self):
        src = os.path.join(self.tmp, 'src')
        root = os.path.join(src, 'onedep-webfe')
        self.site['TOP_WWPDB_WEBAPPS_DIR'] = os.path.join(root, 'webapps')
        return src, root

    def test_the_source_directory_is_created_when_it_is_missing(self):
        src, root = self.source_directory_missing()
        upd = self.make_update()
        self.quiet(upd.phase_webfe)
        self.assertTrue(os.path.isdir(src))
        self.assertIn('git clone --recurse-submodules git@github.com:wwPDB/onedep-webfe.git ' + root,
                      self.procs.lines())

    def test_noop_creates_nothing_and_prints_the_clone(self):
        src, root = self.source_directory_missing()
        upd = self.make_update(noop=True)
        _, out = self.quiet(upd.phase_webfe)
        self.assertFalse(os.path.exists(src))
        self.assertEqual(self.procs.calls, [])
        self.assertIn('git clone --recurse-submodules git@github.com:wwPDB/onedep-webfe.git ' + root, out)

    def test_an_existing_checkout_is_not_cloned(self):
        upd = self.make_update()
        self.quiet(upd.phase_webfe)
        self.assertFalse([line for line in self.procs.lines() if ' clone ' in line])


class DatabasePreflightTest(PreflightBase):
    """Taxonomy bounds, the taxonomy source and the DepUI Django entry point"""

    def test_bounds_must_be_integers(self):
        self.write_conf(taxdbminsize='lots')
        self.assertIn("'taxdbminsize'", self.problems())
        self.write_conf(taxdbmaxsize='many')
        self.assertIn("'taxdbmaxsize'", self.problems())

    def test_taxonomy_file_is_required_unless_the_ftp_load_is_configured(self):
        self.write_conf(taxuseftp=None)
        self.assertIn('TAXONOMY_FILE_NAME', self.problems())
        self.write_conf(taxuseftp='True')
        self.assertEqual(self.problems(), '')
        self.write_conf(taxuseftp=None)
        self.site['TAXONOMY_FILE_NAME'] = '/some/tax.csv'
        self.assertEqual(self.problems(), '')

    def test_taxonomy_inputs_are_not_needed_when_skipped(self):
        self.write_conf(taxdbminsize=None)
        self.assertEqual(self.problems(skip_taxdb=True), '')

    def test_depui_django_entry_point_must_be_importable(self):
        with mock.patch.object(RunUpdateEbi.importlib.util, 'find_spec', return_value=None):
            self.assertIn('wwpdb.apps.deposit.manage', self.problems())
        with mock.patch.object(RunUpdateEbi.importlib.util, 'find_spec', side_effect=ImportError('no wwpdb')):
            self.assertIn('wwpdb.apps.deposit.manage', self.problems())

    def test_django_entry_point_is_not_needed_when_schema_is_skipped(self):
        with mock.patch.object(RunUpdateEbi.importlib.util, 'find_spec', return_value=None):
            self.assertEqual(self.problems(skip_schema=True), '')


class TaxonomyRowCountTest(unittest.TestCase):
    """The row count is read the way ManageTaxDB.py reads it"""

    def fake_db(self, opens=True, rows=((5200000,),)):
        db = types.SimpleNamespace(queries=[], closed=False)

        class Connection(object):
            _dbCon = object()

            def setResource(self, resourceName):
                db.resource = resourceName

            def openConnection(self):
                return opens

            def closeConnection(self):
                db.closed = True

        class Query(object):
            def __init__(self, dbcon):
                pass

            def selectRows(self, queryString):
                db.queries.append(queryString)
                return list(rows)

        modules = {'wwpdb.utils.db': types.ModuleType('wwpdb.utils.db'),
                   'wwpdb.utils.db.MyConnectionBase': types.SimpleNamespace(MyConnectionBase=Connection),
                   'wwpdb.utils.db.MyDbUtil': types.SimpleNamespace(MyDbQuery=Query)}
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        return db

    def test_counts_rows_of_the_status_taxonomy_table(self):
        db = self.fake_db()
        upd = EbiUpdate.__new__(EbiUpdate)
        self.assertEqual(upd._taxonomy_row_count(), 5200000)
        self.assertEqual(db.resource, 'STATUS')
        self.assertIn('taxonomy', db.queries[0])
        self.assertTrue(db.closed)

    def test_unreachable_database_is_an_error(self):
        self.fake_db(opens=False)
        upd = EbiUpdate.__new__(EbiUpdate)
        with self.assertRaises(UpdateError) as ctx:
            upd._taxonomy_row_count()
        self.assertEqual(ctx.exception.phase, 'taxdb')


class TaxonomyPhaseTest(PreflightBase):
    """A zero exit status from ManageTaxDB.py proves nothing; the resulting row count decides"""

    def setUp(self):
        super().setUp()
        self.helper = os.path.join(os.path.dirname(os.path.abspath(RunUpdateEbi.__file__)), 'ManageTaxDB.py')
        self.write_conf(taxuseftp='True')
        self.count = 150
        patcher = mock.patch.object(EbiUpdate, '_taxonomy_row_count', side_effect=lambda: self.count)
        self.row_count = patcher.start()
        self.addCleanup(patcher.stop)

    def run_phase(self, **flags):
        upd = self.make_update(**flags)
        return self.quiet(upd.phase_taxdb)

    def test_helper_is_run_with_the_configured_bounds_and_the_ftp_load(self):
        self.run_phase()
        self.assertEqual(self.procs.lines(), ['{} {} --maxsize 200 --taxdbsize 100 --ftpload'.format(
            sys.executable, self.helper)])

    def test_without_ftp_load_and_without_max_the_legacy_default_applies(self):
        self.site['TAXONOMY_FILE_NAME'] = '/some/tax.csv'
        self.count = 150
        self.write_conf(taxdbmaxsize=None, taxuseftp=None)
        self.run_phase()
        self.assertEqual(self.procs.lines(), ['{} {} --maxsize 999999999 --taxdbsize 100'.format(
            sys.executable, self.helper)])

    def test_row_count_within_bounds_passes(self):
        for count in (100, 150, 199):
            self.count = count
            self.run_phase()

    def test_row_count_outside_bounds_fails_although_the_helper_exited_zero(self):
        for count in (0, 99, 200, 5000):
            self.count = count
            with self.assertRaises(UpdateError) as ctx:
                self.run_phase()
            self.assertEqual(ctx.exception.phase, 'taxdb')
            self.assertIn(str(count), ctx.exception.detail)
            self.assertIn('100', ctx.exception.detail)
            self.assertIn('200', ctx.exception.detail)

    def test_helper_error_output_fails_even_with_exit_zero_and_an_in_range_count(self):
        self.procs.on('ManageTaxDB.py', 0, 'ERROR: Could not open status db\n')
        with self.assertRaises(UpdateError) as ctx:
            self.run_phase()
        self.assertIn('Could not open status db', ctx.exception.detail)
        self.row_count.assert_not_called()

    def test_helper_failure_status_is_fatal(self):
        self.procs.on('ManageTaxDB.py', 1)
        with self.assertRaises(UpdateError):
            self.run_phase()

    def test_noop_lets_the_helper_report_and_skips_the_count(self):
        self.run_phase(noop=True)
        self.assertEqual(self.procs.lines(), ['{} {} --noop --maxsize 200 --taxdbsize 100 --ftpload'.format(
            sys.executable, self.helper)])
        self.row_count.assert_not_called()


class SchemaPhaseTest(PreflightBase):
    """ManageDB.py update, then the DepUI Django migrations that it does not cover"""

    DJANGO = '{} -m wwpdb.apps.deposit.manage migrate'.format(sys.executable)
    PLANNED = 'Planned operations:\ndepui.0004_alter_depositionstatus_type3\n    Alter field type3\n'
    EMPTY = 'No planned migration operations.\n'

    def setUp(self):
        super().setUp()
        self.helper = os.path.join(os.path.dirname(os.path.abspath(RunUpdateEbi.__file__)), 'ManageDB.py')
        self.plans = [self.PLANNED, self.EMPTY]
        scripted_procs = RunUpdateEbi.subprocess.Popen

        def scripted(argv, **kwargs):
            process = scripted_procs(argv, **kwargs)
            if list(argv)[-2:] == ['migrate', '--plan'] and self.plans:
                process.stdout = iter(self.plans.pop(0).splitlines(True))
            return process

        patcher = mock.patch.object(RunUpdateEbi.subprocess, 'Popen', scripted)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_phase(self, **flags):
        upd = self.make_update(**flags)
        return self.quiet(upd.phase_schema)

    def test_manage_db_update_then_plan_apply_and_verify(self):
        self.run_phase()
        self.assertEqual(self.procs.lines(), [
            '{} {} update'.format(sys.executable, self.helper),
            self.DJANGO + ' --plan',
            self.DJANGO + ' --noinput',
            self.DJANGO + ' --plan'])

    def test_nothing_is_applied_when_nothing_is_planned(self):
        self.plans[:] = [self.EMPTY]
        self.run_phase()
        self.assertEqual(self.procs.lines()[1:], [self.DJANGO + ' --plan'])

    def test_remaining_migrations_after_the_apply_are_fatal(self):
        self.plans[:] = [self.PLANNED, self.PLANNED]
        with self.assertRaises(UpdateError) as ctx:
            self.run_phase()
        self.assertEqual(ctx.exception.phase, 'schema')
        self.assertIn('still planned', ctx.exception.detail)

    def test_manage_db_error_output_is_fatal_despite_exit_zero_and_stops_before_django(self):
        self.procs.on('ManageDB.py', 0, 'ERROR UPDATING SCHEMA ALTER TABLE `deposition`\n')
        with self.assertRaises(UpdateError) as ctx:
            self.run_phase()
        self.assertIn('ERROR UPDATING SCHEMA', ctx.exception.detail)
        self.assertFalse([line for line in self.procs.lines() if 'migrate' in line])

    def test_manage_db_failed_marker_and_failure_status_are_fatal(self):
        self.procs.on('ManageDB.py', 0, 'FAILED\n')
        with self.assertRaises(UpdateError):
            self.run_phase()
        del self.procs.rules[:]
        self.procs.on('ManageDB.py', 1)
        with self.assertRaises(UpdateError):
            self.run_phase()

    def test_failing_plan_or_apply_is_fatal(self):
        self.procs.on('migrate --noinput', 1)
        with self.assertRaises(UpdateError) as ctx:
            self.run_phase()
        self.assertIn('migrate --noinput', ctx.exception.detail)

    def test_noop_reads_the_plan_but_applies_nothing(self):
        self.plans[:] = [self.PLANNED]
        self.run_phase(noop=True)
        self.assertEqual(self.procs.lines(), [
            '{} {} --noop update'.format(sys.executable, self.helper),
            self.DJANGO + ' --plan'])

    def test_django_settings_are_left_to_the_deployment(self):
        self.run_phase()
        for call in self.procs.calls[1:]:
            self.assertEqual(call.kwargs['env'].get('DJANGO_SETTINGS_MODULE'),
                             os.environ.get('DJANGO_SETTINGS_MODULE'))


class VerifyPhaseTest(PreflightBase):
    """Final verification: every check affects the exit status"""

    def setUp(self):
        super().setUp()
        here = os.path.dirname(os.path.abspath(RunUpdateEbi.__file__))
        self.finalcheck = '{} {} finalcheck'.format(sys.executable, os.path.join(here, 'ManageDB.py'))
        self.webfe_check = '{} {} --webroot {} check -r {}'.format(
            sys.executable, os.path.join(here, 'ManageWebFE.py'), self.webfe, self.webfe_conf)
        self.pip_check = '{} -m pip check'.format(sys.executable)
        self.versions = mock.patch.object(EbiUpdate, 'checktoolvers', autospec=True)
        self.licence = mock.patch.object(EbiUpdate, 'checkoelicense', autospec=True)
        self.versions_mock = self.versions.start()
        self.licence_mock = self.licence.start()
        self.addCleanup(self.versions.stop)
        self.addCleanup(self.licence.stop)
        self.procs.on(lambda a: a[0] == sys.executable and '-c' in a[1:3], 0, 'MYSQL_SO=/venv/_mysql.so\n')

    def run_phase(self, **flags):
        upd = self.make_update(**flags)
        return self.quiet(upd.phase_verify)

    def fails_with(self, text, **flags):
        with self.assertRaises(UpdateError) as ctx:
            self.run_phase(**flags)
        self.assertEqual(ctx.exception.phase, 'verify')
        self.assertIn(text, ctx.exception.detail)

    def test_everything_passing_runs_every_check_in_order(self):
        self.run_phase()
        lines = self.procs.lines()
        self.assertEqual([lines[0], lines[1], lines[2]], [self.finalcheck, self.webfe_check, self.pip_check])
        self.versions_mock.assert_called_once()
        self.licence_mock.assert_called_once()
        self.assertTrue(lines[3].startswith(sys.executable + ' -c '))
        self.assertTrue(lines[4].startswith('ldd '))

    def test_version_mismatch_is_fatal(self):
        self.versions_mock.side_effect = lambda s: print('***ERROR: Version mismatch 1 != 2 in /tools/x')
        self.fails_with('Version mismatch 1 != 2')

    def test_missing_bundle_is_fatal_not_a_warning(self):
        self.versions_mock.side_effect = lambda s: print('WARNING: Tool out of date. /tools/x/BUNDLEVERSION not found')
        self.fails_with('/tools/x/BUNDLEVERSION not found')

    def test_licence_expiry_mismatch_and_unreadable_licence_are_fatal(self):
        self.licence_mock.side_effect = lambda s: print('ERROR: Openeye Licence expiration wrong  2026 01 01 vs 2027 07 22')
        self.fails_with('Openeye Licence expiration wrong')
        self.licence_mock.side_effect = lambda s: print('***ERROR: Cannot determine open eye license from config')
        self.fails_with('Cannot determine open eye license')

    def test_exception_in_a_legacy_check_is_fatal(self):
        self.versions_mock.side_effect = TypeError('expected str, bytes or os.PathLike object, not NoneType')
        self.fails_with('NoneType')

    def test_manage_db_finalcheck_error_output_is_fatal(self):
        self.procs.on('finalcheck', 0, 'ERROR: You need to load latest views\n')
        self.fails_with('You need to load latest views')

    def test_webfe_pip_and_mysqlclient_failures_are_fatal(self):
        self.procs.on('ManageWebFE.py', 1)
        self.fails_with('ManageWebFE.py')
        del self.procs.rules[:]
        self.procs.on('pip check', 1, 'foo requires bar\n')
        self.fails_with('pip check')

    def test_skip_toolvers_keeps_the_environment_and_webfe_checks(self):
        self.run_phase(skip_toolvers=True)
        self.versions_mock.assert_not_called()
        self.licence_mock.assert_not_called()
        self.assertEqual(self.procs.lines()[:2], [self.webfe_check, self.pip_check])
        self.assertNotIn(self.finalcheck, self.procs.lines())

    def test_skip_webfe_omits_only_the_webfe_check(self):
        self.run_phase(skip_webfe=True)
        self.assertNotIn(self.webfe_check, self.procs.lines())
        self.assertIn(self.pip_check, self.procs.lines())

    def test_noop_runs_every_check_and_reports_problems_without_failing(self):
        self.versions_mock.side_effect = lambda s: print('***ERROR: Version mismatch 1 != 2 in /tools/x')
        self.procs.on('pip check', 1, 'foo requires bar\n')
        _, out = self.run_phase(noop=True)
        lines = self.procs.lines()
        for expected in (self.finalcheck, self.webfe_check, self.pip_check):
            self.assertIn(expected, lines)
        self.assertTrue([line for line in lines if line.startswith('ldd ')])
        self.licence_mock.assert_called_once()
        self.assertIn('noop: would fail', out)
        self.assertIn('Version mismatch 1 != 2', out)
        self.assertIn('pip check', out)

    def test_noop_with_nothing_wrong_reports_nothing(self):
        _, out = self.run_phase(noop=True)
        self.assertNotIn('would fail', out)
        self.assertEqual(len(self.procs.calls), 5)

    def test_noop_only_reads(self):
        self.run_phase(noop=True)
        for call in self.procs.calls:
            self.assertFalse(set(call.argv) & {'install', 'checkout', 'fetch', 'pull', 'update', 'migrate'},
                             call.line)

    def test_the_failure_markers_still_match_the_messages_printed_by_the_stock_checks(self):
        source = open(RunUpdate.__file__.replace('.pyc', '.py')).read()
        for printed in ('***ERROR: Version mismatch', 'WARNING: Tool out of date', 'ERROR: Openeye Licence expiration wrong',
                        '***ERROR: Cannot determine open eye license'):
            self.assertIn(printed, source)
        for marker in RunUpdateEbi.TOOL_FAILURE_MARKERS:
            self.assertTrue([line for line in source.splitlines() if 'print(' in line and marker in line], marker)


class BuildPhaseTest(PreflightBase):
    """The containerised tool build is not available yet: asking for it must fail cleanly"""

    def test_build_tools_fails_with_a_clear_error_instead_of_a_traceback(self):
        upd = self.make_update(build_tools=True, build_container='/x/y.sif')
        with self.assertRaises(UpdateError) as ctx:
            self.quiet(upd.phase_build)
        self.assertEqual(ctx.exception.phase, 'build')
        self.assertIn('not implemented', ctx.exception.detail)
        self.assertEqual(self.procs.calls, [])


if __name__ == '__main__':
    unittest.main()
