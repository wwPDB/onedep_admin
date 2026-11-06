import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.modules['wwpdb.utils.config.ConfigInfo'] = MagicMock()
from scripts import RunUpdate
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

    def test_installpins_hands_each_pin_to_the_shell_unchanged(self):
        text = "tool>=1.0,<2\nlocal @ https://example.org/local-1.0.whl?token=$HOME\n"
        manager = self.manager([0, 0])
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as fout:
            fout.write(text)
        try:
            manager.installpins(fout.name, '-c constraints.txt')
            expected = release_pins(fout.name)
        finally:
            os.remove(fout.name)
        # pip is replaced by a function that prints its last argument, as the shell hands it over
        received = [subprocess.check_output(['bash', '-c', 'pip() { printf %s "${@: -1}"; }; ' + command],
                                            universal_newlines=True) for command in self.commands(manager)]
        self.assertEqual(received, expected)

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

BUILD_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts', 'BuildToolsInContainer.sh')

STUB_FUNCTIONS = r"""
pkg_build_one() { echo "build one FORCE=$FORCE_REBUILD PYTHONPATH=${PYTHONPATH-unset}" >> "$LOG"; }
pkg_build_two() { echo "build two" >> "$LOG"; }
pkg_build_bad() { echo "build bad" >> "$LOG"; return 1; }
pkg_build_copy() { mkdir -p "$WORK" && cd "$WORK" && rsync -av "$SOURCE/Dir" . >> "$LOG" 2>&1; }
pkg_build_ssl() { echo "configure --with-openssl=$PREFIX --disable-x" >> "$LOG"; }
"""


class BuildToolsInContainerScriptTest(unittest.TestCase):
    """Tests for the script that runs the onedep-build functions inside the build container, against a stub build tree
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.log = os.path.join(self.root, 'log.txt')
        self.tree = os.path.join(self.root, 'onedep-build')
        self.prefix = os.path.join(self.root, 'tools')
        self.distrib = os.path.join(self.root, 'distrib')
        self.source = os.path.join(self.root, 'staged')
        for path in (os.path.join(self.tree, 'utils'), os.path.join(self.tree, 'v-6000', 'packages'), self.prefix,
                     self.distrib, os.path.join(self.source, 'Dir')):
            os.makedirs(path)
        with open(os.path.join(self.source, 'Dir', 'file.txt'), 'w') as fout:
            fout.write('staged')
        with open(os.path.join(self.distrib, 'cached.tar.gz'), 'w') as fout:
            fout.write('cached')
        self.env_sh = os.path.join(self.root, 'env.sh')
        self.write(self.env_sh, 'echo "env.sh $*" >> "$LOG"\necho "env.sh PYTHONPATH=${PYTHONPATH-unset}" >> "$LOG"\n')
        self.write(os.path.join(self.tree, 'utils', 'pkg-utils-v2.sh'),
                   'get_environment() {\n  export PREFIX="%s" DISTRIB_DIR="%s"\n  echo get_environment >> "$LOG"\n}\n'
                   % (self.prefix, self.distrib))
        self.write(os.path.join(self.tree, 'v-6000', 'packages', 'all-packages.sh'), STUB_FUNCTIONS)
        self.os_release = os.path.join(self.root, 'os-release')
        self.write(self.os_release, 'ID="rocky"\nVERSION_ID="8.10"\n')

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, path, text):
        with open(path, 'w') as fout:
            fout.write(text)

    def logged(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fin:
            return fin.read().splitlines()

    def built(self):
        return [line for line in self.logged() if line.startswith('build')]

    def run_script(self, packages, epoch='v-6000', path=None):
        env = {'PATH': path or os.environ['PATH'], 'LOG': self.log, 'WORK': os.path.join(self.root, 'work'),
               'SOURCE': self.source, 'OS_RELEASE': self.os_release, 'PYTHONPATH': '/venv/site-packages'}
        return subprocess.run([shutil.which('bash'), '--noprofile', '--norc', BUILD_SCRIPT, self.env_sh, 'SITE_ID', 'loc',
                               self.tree, epoch] + packages, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True)

    def path_without_rsync(self):
        """A PATH with the few programs the script itself needs and no rsync"""
        bin_dir = os.path.join(self.root, 'bin')
        os.makedirs(bin_dir)
        for name in ('mv', 'find', 'mkdir', 'date', 'cp', 'cat'):
            os.symlink(shutil.which(name), os.path.join(bin_dir, name))
        return bin_dir

    def test_sources_the_site_environment_and_builds_the_packages_in_order(self):
        result = self.run_script(['one', 'two'])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.logged()[0], 'env.sh --siteid SITE_ID --locid loc --nocache')
        self.assertEqual(self.built(), ['build one FORCE=YES PYTHONPATH=unset', 'build two'])

    def test_unsets_pythonpath_after_the_site_environment_and_before_the_first_build(self):
        self.run_script(['one'])
        lines = self.logged()
        self.assertIn('env.sh PYTHONPATH=/venv/site-packages', lines)
        self.assertIn('build one FORCE=YES PYTHONPATH=unset', lines)

    def test_stops_at_the_first_package_that_fails(self):
        result = self.run_script(['one', 'bad', 'two'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.built(), ['build one FORCE=YES PYTHONPATH=unset', 'build bad'])
        self.assertIn('ERROR: pkg_build_bad failed', result.stdout)

    def test_builds_nothing_when_a_function_is_not_defined(self):
        result = self.run_script(['one', 'missing'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.built(), [])
        self.assertIn('pkg_build_missing is not defined', result.stdout)

    def test_moves_the_download_cache_aside_instead_of_deleting_it(self):
        self.run_script(['one'])
        self.assertEqual(os.listdir(self.distrib), [])
        asides = [name for name in os.listdir(self.root) if name.startswith('distrib.aside.')]
        self.assertEqual(len(asides), 1)
        self.assertTrue(os.path.exists(os.path.join(self.root, asides[0], 'cached.tar.gz')))

    def test_copies_with_cp_when_rsync_is_not_installed(self):
        result = self.run_script(['copy'], path=self.path_without_rsync())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.root, 'work', 'Dir', 'file.txt')))

    def test_uses_rsync_when_it_is_installed(self):
        bin_dir = self.path_without_rsync()
        self.write(os.path.join(bin_dir, 'rsync'), '#!/bin/sh\necho real-rsync >> "$LOG"\n')
        os.chmod(os.path.join(bin_dir, 'rsync'), 0o755)
        self.run_script(['copy'], path=bin_dir)
        self.assertIn('real-rsync', self.logged())
        self.assertFalse(os.path.exists(os.path.join(self.root, 'work', 'Dir')))

    def test_leaves_out_with_openssl_when_the_tools_tree_has_no_openssl_headers(self):
        result = self.run_script(['ssl', 'one'])
        self.assertIn('configure  --disable-x', self.logged())
        self.assertIn('note: pkg_build_ssl runs without --with-openssl=$PREFIX', result.stdout)
        self.assertNotIn('pkg_build_one runs without', result.stdout)

    def test_keeps_with_openssl_when_the_tools_tree_has_openssl_headers(self):
        os.makedirs(os.path.join(self.prefix, 'include', 'openssl'))
        self.write(os.path.join(self.prefix, 'include', 'openssl', 'ssl.h'), '')
        self.run_script(['ssl'])
        self.assertIn('configure --with-openssl=%s --disable-x' % self.prefix, self.logged())

    def test_refuses_an_epoch_that_does_not_match_the_container_distribution(self):
        result = self.run_script(['one'], epoch='v-5200')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pass --build-version v-6000', result.stdout)
        self.assertEqual(self.built(), [])

    def test_accepts_the_epoch_of_a_centos_container(self):
        os.makedirs(os.path.join(self.tree, 'v-5200', 'packages'))
        self.write(os.path.join(self.tree, 'v-5200', 'packages', 'all-packages.sh'),
                   'pkg_build_one() { echo "build one 5200" >> "$LOG"; }\n')
        self.write(self.os_release, 'ID="centos"\nVERSION_ID="7"\n')
        result = self.run_script(['one'], epoch='v-5200')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.built(), ['build one 5200'])

    def run_check(self, epoch):
        env = {'PATH': os.environ['PATH'], 'LOG': self.log, 'OS_RELEASE': self.os_release}
        return subprocess.run([shutil.which('bash'), '--noprofile', '--norc', BUILD_SCRIPT, '--check-epoch', epoch],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

    def test_check_epoch_succeeds_for_the_epoch_of_the_container_and_touches_nothing(self):
        result = self.run_check('v-6000')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.logged(), [])
        self.assertEqual(os.listdir(self.distrib), ['cached.tar.gz'])

    def test_check_epoch_refuses_another_epoch(self):
        result = self.run_check('v-5200')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pass --build-version v-6000', result.stdout)

    def test_refuses_an_unknown_distribution(self):
        self.write(self.os_release, 'ID="ubuntu"\nVERSION_ID="22.04"\n')
        result = self.run_script(['one'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.built(), [])


class BuildToolsTestBase(unittest.TestCase):
    """What the tests of the tool build share
    """

    SITE = {'TOP_WWPDB_SITE_CONFIG_DIR': '/site-config', 'TOOLS_PATH': '/tools', 'WWPDB_ONEDEP_BUILD': '/onedep-build',
            'WWPDB_SITE_ID': 'SITE_ID', 'WWPDB_SITE_LOC': 'loc'}

    def manager(self, site=None, buildupdates='annotation_pack chem_comp_pack', result=0, **pins):
        values = dict(self.SITE if site is None else site)
        options = dict(pins, buildupdates=buildupdates)
        manager = UpdateManager.__new__(UpdateManager)
        manager._UpdateManager__exec = MagicMock(return_value=result)
        manager._UpdateManager__configfile = '/admin/V6.1/V61rel.conf'
        manager._UpdateManager__noop = False
        manager._UpdateManager__cparser = MagicMock()
        manager._UpdateManager__cparser.has_option.side_effect = lambda section, name: options.get(name) is not None
        manager._UpdateManager__cparser.get.side_effect = lambda section, name: options[name]
        manager.get_variable = lambda name, environment=None: values.get(name)
        manager.bundleatpin = MagicMock(return_value=False)
        return manager

    def commands(self, manager):
        return [call[0][0] for call in manager._UpdateManager__exec.call_args_list]

    def build_in_container(self, manager, **kwargs):
        with patch('scripts.RunUpdate.shutil.which',
                   side_effect=lambda name: '/usr/bin/' + name if name == 'apptainer' else None):
            manager.buildtools('v-6000', '/images/builder.sif', **kwargs)


class UpdateManagerBuildToolsTest(BuildToolsTestBase):
    """Tests for building the release's tools, in the build container when asked
    """

    def test_buildtools_without_a_container_runs_buildtools_py_as_before(self):
        manager = self.manager()
        manager.buildtools('v-5200')
        commands = self.commands(manager)
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0].startswith('python '))
        self.assertTrue(commands[0].endswith('BuildTools.py --config /admin/V6.1/V61rel.conf --build-version v-5200'))

    def test_buildtools_in_a_container_runs_the_script_once_with_the_packages_in_release_order(self):
        manager = self.manager()
        self.build_in_container(manager)
        commands = self.commands(manager)
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertTrue(command.startswith('/usr/bin/apptainer exec '))
        script = os.path.abspath(os.path.join(os.path.dirname(RunUpdate.__file__), 'BuildToolsInContainer.sh'))
        self.assertIn('/images/builder.sif bash --noprofile --norc {}'.format(script), command)
        self.assertTrue(command.endswith(
            ' /site-config/init/env.sh SITE_ID loc /onedep-build v-6000 annotation_pack chem_comp_pack'))

    def test_buildtools_in_a_container_binds_what_the_build_needs(self):
        manager = self.manager()
        self.build_in_container(manager, build_bind=['/extra', '/tools'])
        command = self.commands(manager)[0]
        admin = os.path.abspath(os.path.join(os.path.dirname(RunUpdate.__file__), '..'))
        for path in (admin, '/site-config', '/tools', '/onedep-build', sys.prefix, '/extra'):
            self.assertIn('--bind {} '.format(path), command)
        self.assertEqual(command.count('--bind /tools '), 1)

    def test_buildtools_in_a_container_gives_the_site_environment_the_deployment_site_packages(self):
        import sysconfig
        manager = self.manager()
        self.build_in_container(manager)
        self.assertIn('--env PYTHONPATH={} '.format(sysconfig.get_paths()['purelib']), self.commands(manager)[0])

    def test_buildtools_in_a_container_uses_singularity_when_there_is_no_apptainer(self):
        manager = self.manager()
        with patch('scripts.RunUpdate.shutil.which',
                   side_effect=lambda name: '/usr/bin/singularity' if name == 'singularity' else None):
            manager.buildtools('v-6000', '/images/builder.sif')
        self.assertTrue(self.commands(manager)[0].startswith('/usr/bin/singularity exec '))

    def test_buildtools_in_a_container_stops_when_there_is_no_container_runtime(self):
        manager = self.manager()
        with patch('scripts.RunUpdate.shutil.which', return_value=None):
            with self.assertRaises(SystemExit) as raised:
                manager.buildtools('v-6000', '/images/builder.sif')
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(self.commands(manager), [])

    def test_buildtools_in_a_container_stops_when_the_site_configuration_lacks_a_value(self):
        for name in self.SITE:
            site = dict(self.SITE)
            del site[name]
            manager = self.manager(site=site)
            with self.assertRaises(SystemExit, msg=name) as raised:
                self.build_in_container(manager)
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(self.commands(manager), [])

    def test_buildtools_in_a_container_stops_when_the_container_run_fails(self):
        manager = self.manager(result=1)
        with self.assertRaises(SystemExit) as raised:
            self.build_in_container(manager)
        self.assertEqual(raised.exception.code, 1)

    def test_buildtools_in_a_container_has_nothing_to_do_without_buildupdates(self):
        for buildupdates in (None, '  '):
            manager = self.manager(buildupdates=buildupdates)
            self.build_in_container(manager)
            self.assertEqual(self.commands(manager), [])

    def test_buildtools_in_a_container_quotes_paths_for_the_shell(self):
        manager = self.manager()
        self.build_in_container(manager, build_bind=['/with space'])
        self.assertIn("--bind '/with space' ", self.commands(manager)[0])


class UpdateManagerBuildSkipTest(BuildToolsTestBase):
    """Tests for leaving out the packages whose bundle is already at its pin
    """

    PINS = dict(annotver='2026-08-30:13-25', cctoolsver='2026-08-31:20-58', mapfixver='2024-08-07:07-14',
                phenixver='2.2-6143')

    def build(self, manager, **kwargs):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.build_in_container(manager, **kwargs)
        return stdout.getvalue()

    def packages(self, manager):
        return self.commands(manager)[0].split(' v-6000 ')[1].split()

    def test_packages_at_their_pin_are_left_out(self):
        manager = self.manager(buildupdates='annotation_pack chem_comp_pack mapfix', **self.PINS)
        manager.bundleatpin.side_effect = lambda option: option in ('annotver', 'cctoolsver')
        output = self.build(manager)
        self.assertEqual(self.packages(manager), ['mapfix'])
        self.assertIn('skipping pkg_build_annotation_pack', output)
        self.assertIn('skipping pkg_build_chem_comp_pack', output)
        self.assertNotIn('skipping pkg_build_mapfix', output)

    def test_nothing_is_run_when_every_package_is_at_its_pin(self):
        manager = self.manager(buildupdates='annotation_pack chem_comp_pack', **self.PINS)
        manager.bundleatpin.return_value = True
        output = self.build(manager)
        self.assertEqual(self.commands(manager), [])
        self.assertIn('nothing to build', output)

    def test_force_build_builds_every_package_without_looking_at_the_bundles(self):
        manager = self.manager(buildupdates='annotation_pack chem_comp_pack', **self.PINS)
        manager.bundleatpin.return_value = True
        self.build(manager, force_build=True)
        self.assertEqual(self.packages(manager), ['annotation_pack', 'chem_comp_pack'])
        manager.bundleatpin.assert_not_called()

    def test_each_package_is_judged_by_the_pin_the_release_gives_its_bundle(self):
        manager = self.manager(buildupdates='annotation_pack chem_comp_pack sf_valid mapfix findgeo metalcoord phenix_22',
                               annotver='a', cctoolsver='c', sfvalidver='s', mapfixver='m', findgeover='f',
                               metalcoordvers='mc', phenixver='p')
        self.build(manager)
        self.assertEqual([call[0][0] for call in manager.bundleatpin.call_args_list],
                         ['annotver', 'cctoolsver', 'sfvalidver', 'mapfixver', 'findgeover', 'metalcoordvers', 'phenixver'])

    def test_a_package_the_check_does_not_know_is_built(self):
        manager = self.manager(buildupdates='annotation_pack other_pack', **self.PINS)
        manager.bundleatpin.return_value = True
        self.build(manager)
        self.assertEqual(self.packages(manager), ['other_pack'])

    def test_a_package_without_a_pin_in_the_release_is_built(self):
        manager = self.manager(buildupdates='annotation_pack mapfix', annotver='a')
        manager.bundleatpin.return_value = True
        self.build(manager)
        self.assertEqual(self.packages(manager), ['mapfix'])


class UpdateManagerBundleAtPinTest(unittest.TestCase):
    """Tests for judging one bundle by the stock tool version check, run for its single pin
    """

    def manager(self, prints=None, raises=None):
        manager = UpdateManager.__new__(UpdateManager)
        manager._UpdateManager__noop = False
        manager._UpdateManager__cparser = MagicMock()
        manager._UpdateManager__cparser.get.return_value = '2026-08-30:13-25 100%'
        self.seen = []

        def checktoolvers():
            if raises:
                raise raises
            if prints:
                print(prints)

        def single(config_file, noop):
            with open(config_file) as fin:
                self.seen.append(fin.read())
            instance = MagicMock()
            instance.checktoolvers = checktoolvers
            return instance

        self.single = single
        return manager

    def at_pin(self, manager):
        stdout = io.StringIO()
        with patch('scripts.RunUpdate.UpdateManager', side_effect=self.single), contextlib.redirect_stdout(stdout):
            result = manager.bundleatpin('annotver')
        self.assertEqual(stdout.getvalue(), '')
        return result

    def test_a_check_that_prints_nothing_means_the_bundle_is_at_its_pin(self):
        self.assertTrue(self.at_pin(self.manager()))

    def test_the_check_is_run_for_that_one_pin_only(self):
        self.at_pin(self.manager())
        self.assertEqual(self.seen, ['[DEFAULT]\nannotver = 2026-08-30:13-25 100%%\n'])

    def test_a_version_mismatch_means_the_bundle_is_not_at_its_pin(self):
        self.assertFalse(self.at_pin(self.manager(prints='***ERROR: Version mismatch a != b in /x')))

    def test_a_missing_bundle_means_the_bundle_is_not_at_its_pin(self):
        self.assertFalse(self.at_pin(self.manager(prints='WARNING: Tool out of date. /x not found')))

    def test_a_check_that_cannot_run_means_the_bundle_is_not_at_its_pin(self):
        self.assertFalse(self.at_pin(self.manager(raises=KeyError('Version'))))


class UpdateManagerCheckBuildContainerTest(BuildToolsTestBase):
    """Tests for checking, before anything is updated, that the container can build
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.site = {'TOP_WWPDB_SITE_CONFIG_DIR': os.path.join(self.root, 'site-config'),
                     'TOOLS_PATH': os.path.join(self.root, 'tools'),
                     'WWPDB_ONEDEP_BUILD': os.path.join(self.root, 'onedep-build'),
                     'WWPDB_SITE_ID': 'SITE_ID', 'WWPDB_SITE_LOC': 'loc'}
        os.makedirs(os.path.join(self.site['TOP_WWPDB_SITE_CONFIG_DIR'], 'init'))
        os.makedirs(self.site['WWPDB_ONEDEP_BUILD'])
        os.makedirs(self.site['TOOLS_PATH'])
        self.env_sh = os.path.join(self.site['TOP_WWPDB_SITE_CONFIG_DIR'], 'init', 'env.sh')
        self.image = os.path.join(self.root, 'builder.sif')
        self.extra = os.path.join(self.root, 'extra')
        os.makedirs(self.extra)
        for path in (self.env_sh, self.image):
            open(path, 'w').close()

    def tearDown(self):
        shutil.rmtree(self.root)

    def check(self, manager, **kwargs):
        kwargs.setdefault('build_container', self.image)
        with patch('scripts.RunUpdate.shutil.which',
                   side_effect=lambda name: '/usr/bin/' + name if name == 'apptainer' else None):
            manager.checkbuildcontainer('v-6000', **kwargs)

    def test_runs_the_container_once_in_check_only_mode_even_under_noop(self):
        manager = self.manager(site=self.site)
        self.check(manager, build_bind=[self.extra])
        self.assertEqual(manager._UpdateManager__exec.call_count, 1)
        command = self.commands(manager)[0]
        self.assertTrue(command.startswith('/usr/bin/apptainer exec '))
        self.assertIn('--bind {} '.format(self.extra), command)
        script = os.path.abspath(os.path.join(os.path.dirname(RunUpdate.__file__), 'BuildToolsInContainer.sh'))
        self.assertIn(' {} bash '.format(self.image), command)
        self.assertTrue(command.endswith(' bash --noprofile --norc {} --check-epoch v-6000'.format(script)))
        self.assertTrue(manager._UpdateManager__exec.call_args[1].get('overridenoop'))

    def test_stops_when_the_container_check_fails(self):
        manager = self.manager(site=self.site, result=1)
        with self.assertRaises(SystemExit) as raised:
            self.check(manager)
        self.assertEqual(raised.exception.code, 1)

    def stops_without_running_the_container(self, manager, **kwargs):
        with self.assertRaises(SystemExit) as raised:
            self.check(manager, **kwargs)
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(self.commands(manager), [])

    def test_stops_when_there_is_no_container_runtime(self):
        manager = self.manager(site=self.site)
        with patch('scripts.RunUpdate.shutil.which', return_value=None):
            with self.assertRaises(SystemExit) as raised:
                manager.checkbuildcontainer('v-6000', self.image)
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(self.commands(manager), [])

    def test_stops_when_the_site_configuration_lacks_a_value(self):
        for name in self.site:
            site = dict(self.site)
            del site[name]
            self.stops_without_running_the_container(self.manager(site=site))

    def test_stops_when_the_image_is_not_a_file(self):
        self.stops_without_running_the_container(self.manager(site=self.site), build_container=self.root)

    def test_stops_when_a_bind_is_not_a_directory(self):
        self.stops_without_running_the_container(self.manager(site=self.site), build_bind=[self.image])

    def test_stops_when_the_build_tree_is_not_a_directory(self):
        os.rmdir(self.site['WWPDB_ONEDEP_BUILD'])
        self.stops_without_running_the_container(self.manager(site=self.site))

    def test_stops_when_the_site_environment_script_is_missing(self):
        os.remove(self.env_sh)
        self.stops_without_running_the_container(self.manager(site=self.site))


class MainBuildContainerOptionsTest(unittest.TestCase):
    """Tests for the container options of the command line
    """

    def run_main(self, *args):
        config = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'base_packages', 'constraints.txt')
        manager = MagicMock()
        with patch.object(RunUpdate, 'UpdateManager', return_value=manager), \
                patch.object(sys, 'argv', ['RunUpdate.py', '--config', config] + list(args)):
            RunUpdate.main()
        return manager

    def test_the_container_and_binds_are_passed_to_buildtools(self):
        manager = self.run_main('--build-tools', '--build-version', 'v-6000', '--build-container', '/images/b.sif',
                                '--build-bind', '/a', '--build-bind', '/b')
        manager.buildtools.assert_called_once_with('v-6000', '/images/b.sif', ['/a', '/b'], False)

    def test_buildtools_is_called_without_a_container_by_default(self):
        manager = self.run_main('--build-tools')
        manager.buildtools.assert_called_once_with('v-5200', None, None, False)

    def test_a_container_without_build_tools_is_an_error(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_main('--build-container', '/images/b.sif')
        self.assertEqual(raised.exception.code, 2)

    def test_the_container_is_checked_before_anything_is_updated(self):
        manager = self.run_main('--build-tools', '--build-version', 'v-6000', '--build-container', '/images/b.sif',
                                '--build-bind', '/a')
        manager.checkbuildcontainer.assert_called_once_with('v-6000', '/images/b.sif', ['/a'])
        names = [call[0] for call in manager.method_calls]
        self.assertEqual(names[0], 'checkbuildcontainer')
        self.assertLess(names.index('checkbuildcontainer'), names.index('updateresources'))

    def test_no_container_check_without_a_container_build(self):
        manager = self.run_main('--build-tools')
        manager.checkbuildcontainer.assert_not_called()

    def test_force_build_is_passed_to_buildtools(self):
        manager = self.run_main('--build-tools', '--build-container', '/images/b.sif', '--force-build')
        manager.buildtools.assert_called_once_with('v-5200', '/images/b.sif', None, True)

    def test_force_build_without_a_container_is_an_error(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_main('--build-tools', '--force-build')
        self.assertEqual(raised.exception.code, 2)

    def test_a_bind_without_a_container_is_an_error(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_main('--build-tools', '--build-bind', '/a')
        self.assertEqual(raised.exception.code, 2)

if __name__ == '__main__':
    unittest.main()

class ImportWithoutPackagingTest(unittest.TestCase):
    """RunUpdate is imported by every update, so it must not need packaging until a pin file is read
    """

    def test_import_does_not_need_packaging(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        code = ("import sys\nfrom unittest.mock import MagicMock\n"
                "sys.modules['wwpdb.utils.config.ConfigInfo'] = MagicMock()\n"
                "sys.modules['packaging'] = None\n"
                "from scripts import RunUpdate\n")
        result = subprocess.run([sys.executable, '-c', code], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stdout)
