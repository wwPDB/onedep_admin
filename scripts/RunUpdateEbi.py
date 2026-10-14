"""Opt-in, fail-fast release update for EBI RHEL 9 deployments (RunUpdate.py --ebi-compat)"""

import contextlib
import importlib.metadata
import importlib.util
import io
import os
import re
import shlex
import subprocess
import sys
import warnings
from urllib.parse import urlparse

try:
    from .RunUpdate import UpdateManager
except ImportError:
    # Run as a script rather than as part of the scripts package
    from RunUpdate import UpdateManager

try:
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement
    from pip._vendor.packaging.utils import canonicalize_name
    from pip._vendor.packaging.version import Version


BASE_PACKAGES_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', 'base_packages'))


MYSQLDB_PROBE = (
    'import MySQLdb\n'
    'from MySQLdb import _mysql\n'
    'assert _mysql.escape_string(b"ok") == b"ok"\n'
    'print("MYSQL_SO=" + _mysql.__file__)\n')


# The helper scripts print these and still exit 0, so their output is scanned
TAXDB_FAILURE_MARKERS = ('ERROR', 'Could not')
SCHEMA_FAILURE_MARKERS = ('ERROR', 'FAILED')
TOOL_FAILURE_MARKERS = ('ERROR', 'WARNING: Tool out of date')
NO_PLANNED_MIGRATIONS = 'No planned migration operations'
DEFAULT_TAXDB_MAXSIZE = 999999999
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class UpdateError(Exception):
    """A mandatory operation failed; phase names where"""

    def __init__(self, phase, detail):
        super().__init__(detail)
        self.phase = phase
        self.detail = detail


_URL_USERINFO = re.compile(r'(://)[^/\s@]*@')


def redact(text, secrets=()):
    """Mask credentials embedded in URLs and any literal secret values"""
    text = _URL_USERINFO.sub(r'\1***@', text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '***')
    return text


class Pin(object):
    """One active release requirement: line is the cleaned text handed to pip, name is canonical"""

    def __init__(self, line, name, requirement):
        self.line = line
        self.name = name
        self.requirement = requirement


def parse_pins(path):
    """Return the active Pins of a release requirements file, in file order"""
    pins = []
    with open(path) as fin:
        for number, raw in enumerate(fin, 1):
            line = re.sub(r'(^|\s)#.*$', '', raw).strip()
            if not line:
                continue
            if line.startswith('-'):
                # Never echo the line: index options can carry credentials
                raise UpdateError('preflight', '{} line {}: option {} is not a requirement; release pins are '
                                  'installed one at a time'.format(path, number, line.split()[0]))
            try:
                requirement = Requirement(line)
            except InvalidRequirement as e:
                raise UpdateError('preflight', '{} line {}: invalid requirement {!r}: {}'.format(
                    path, number, redact(line), redact(str(e))))
            if requirement.marker is None or requirement.marker.evaluate():
                pins.append(Pin(line, canonicalize_name(requirement.name), requirement))
    return pins


def failure_lines(output, markers):
    """Lines of a helper's output that report a failure"""
    return [line.strip() for line in output.splitlines() if any(marker in line for marker in markers)]


class RunResult(object):
    def __init__(self, returncode, output):
        self.returncode = returncode
        self.output = output


class EbiUpdate(UpdateManager):
    """Strict counterpart of UpdateManager; every mandatory failure raises UpdateError"""

    def __init__(self, config_file, args):
        UpdateManager.__init__(self, config_file, args.noop)
        self.args = args
        self.secrets = [self.get_variable(v, environment='INSTALL_ENVIRONMENT') for v in ('CS_USER', 'CS_PW')]

    def _run(self, phase, argv, env=None, cwd=None, mutating=True, check=True, echo=True):
        """Run an argument vector, echoing its redacted output; mutating commands only print under --noop"""
        shown = redact(' '.join(shlex.quote(a) for a in argv), self.secrets)
        print('[{}] $ {}'.format(phase, shown))
        if mutating and self.args.noop:
            print('[{}] noop: not executed'.format(phase))
            return RunResult(0, '')

        full_env = dict(os.environ)
        full_env.update(env or {})
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=full_env, cwd=cwd,
                                    universal_newlines=True)
        except OSError as e:
            raise UpdateError(phase, 'cannot run {}: {}'.format(shown, redact(str(e), self.secrets)))

        lines = []
        for line in proc.stdout:
            lines.append(line)
            if echo:
                sys.stdout.write(redact(line, self.secrets))
        returncode = proc.wait()
        if check and returncode:
            raise UpdateError(phase, 'command failed (exit {}): {}'.format(returncode, shown))
        return RunResult(returncode, ''.join(lines))

    def run(self):
        """Run the selected phases in order; the first UpdateError propagates"""
        a = self.args
        phases = [('preflight', self.phase_preflight, True),
                  ('resources', self.phase_resources, not a.skip_resources),
                  ('remove', self.phase_remove, not a.skip_remove),
                  ('pip', self.phase_pip, not a.skip_pip),
                  ('webfe', self.phase_webfe, not a.skip_webfe),
                  ('taxdb', self.phase_taxdb, not a.skip_taxdb),
                  ('schema', self.phase_schema, not a.skip_schema),
                  ('build', self.phase_build, a.build_tools),
                  ('verify', self.phase_verify, True)]
        for name, phase, selected in phases:
            if selected:
                print('==== {} ===='.format(name))
                phase()

    def _opt(self, key):
        """Value of a release configuration key, or None"""
        parser = self._UpdateManager__cparser
        if not parser.has_option('DEFAULT', key):
            return None
        return parser.get('DEFAULT', key, vars=self._UpdateManager__confvars)

    def phase_preflight(self):
        """Read-only validation of the selected phases' inputs; raises one UpdateError listing every problem"""
        problems = []
        for check in (self._check_config, self._check_pins, self._check_removepy, self._check_resources,
                      self._check_pip_inputs, self._check_webfe, self._check_taxonomy, self._check_django):
            problems.extend(check())
        if problems:
            raise UpdateError('preflight', '{} problem(s) found, nothing was changed:\n  - {}'.format(
                len(problems), '\n  - '.join(problems)))
        print('preflight: ok')

    def _check_config(self):
        a = self.args
        problems = []
        needed = [('resourcestag', not a.skip_resources), ('webfetag', not a.skip_webfe),
                  ('webfeconf', not a.skip_webfe), ('taxdbminsize', not a.skip_taxdb),
                  ('piprequirements', not a.skip_pip)]
        for key, selected in needed:
            if selected and not self._opt(key):
                problems.append("release configuration has no '{}'".format(key))

        files = [('webfeconf', not a.skip_webfe), ('piprequirements', not a.skip_pip)]
        paths = [(key, self._opt(key)) for key, selected in files if selected]
        if not a.skip_pip:
            paths.extend((name, os.path.join(BASE_PACKAGES_DIR, name))
                         for name in ('constraints.txt', 'pre-requirements.txt', 'requirements.txt'))
        for key, path in paths:
            if path and not os.path.isfile(path):
                problems.append('{} is not a file: {}'.format(key, path))

        if sys.prefix == sys.base_prefix:
            problems.append('{} is not running in a virtual environment; run it with the deployment '
                            'virtual environment\'s python'.format(os.path.basename(__file__)))
        return problems

    def _removepy_entries(self):
        return (self._opt('removepy') or '').split()

    def _check_removepy(self):
        if self.args.skip_remove:
            return []
        problems = []
        for entry in self._removepy_entries():
            name, _, wanted = entry.partition('=')
            if not name or not wanted.lstrip('='):
                problems.append("removepy entry '{}' is not name=version".format(entry))
        return problems

    def _check_resources(self):
        if self.args.skip_resources:
            return []
        problems = []
        ro = self.get_variable('RO_RESOURCE_PATH')
        if ro:
            problems.extend(self._check_checkout('resources_ro', ro))
        else:
            problems.append('RO_RESOURCE_PATH is not set in the site configuration')
        rw = self.get_variable('RW_RESOURCE_PATH')
        if rw:
            problems.extend(self._check_checkout('resources_rw', rw, on_branch=True))
        return problems

    def _check_taxonomy(self):
        if self.args.skip_taxdb:
            return []
        problems = []
        for key in ('taxdbminsize', 'taxdbmaxsize'):
            value = self._opt(key)
            if value and not re.match(r'^\s*\d+\s*$', value):
                problems.append("'{}' is not an integer: {}".format(key, value))
        if self._opt('taxuseftp') is None and not self.get_variable('TAXONOMY_FILE_NAME'):
            problems.append('there is no taxonomy source: the release has no taxuseftp and TAXONOMY_FILE_NAME is '
                            'not set in the site configuration')
        return problems

    def _check_django(self):
        if self.args.skip_schema:
            return []
        try:
            found = importlib.util.find_spec('wwpdb.apps.deposit.manage')
        except (ImportError, ValueError):
            found = None
        if found is None:
            return ['wwpdb.apps.deposit.manage cannot be imported by this interpreter, so the DepUI Django '
                    'migrations cannot be applied']
        return []

    def _webfe_root(self):
        webapps = self.get_variable('TOP_WWPDB_WEBAPPS_DIR')
        return os.path.abspath(os.path.join(webapps, '..')) if webapps else None

    def _check_webfe(self):
        if self.args.skip_webfe:
            return []
        root = self._webfe_root()
        if not root:
            return ['TOP_WWPDB_WEBAPPS_DIR is not set in the site configuration']
        return self._check_checkout('webfe', root, untracked=True)

    def _check_checkout(self, label, path, on_branch=False, untracked=False):
        """Read-only: the directory must be a git checkout without local changes (nothing is cloned or reset)"""
        if not os.path.isdir(path):
            return ['{} {} does not exist; this mode never clones'.format(label, path)]
        git = ['git', '-C', path]
        if self._run('preflight', git + ['rev-parse', '--is-inside-work-tree'], mutating=False,
                     check=False).returncode:
            return ['{} {} is not a git checkout'.format(label, path)]
        problems = []
        status = self._run('preflight', git + ['status', '--porcelain'] + ([] if untracked else ['--untracked-files=no']),
                           mutating=False, check=False)
        if status.returncode:
            problems.append('cannot read the git status of {}'.format(path))
        elif status.output.strip():
            what = ('uncommitted changes; commit or stash modified files and move untracked files aside, for '
                    'example into the rollback snapshot' if untracked else
                    'uncommitted changes to tracked files; commit, stash or move them aside')
            problems.append('{} {} has {} (this mode never discards local changes):\n{}'.format(
                label, path, what, status.output.rstrip()))
        if on_branch and self._run('preflight', git + ['symbolic-ref', '-q', 'HEAD'], mutating=False,
                                   check=False).returncode:
            problems.append('{} {} is not on a branch, so it cannot be fast-forwarded'.format(label, path))
        return problems

    def _index_settings(self):
        """CS_* values of the private package index, from the install environment of the site configuration"""
        names = ('CS_USER', 'CS_PW', 'CS_URL')
        values = dict((n, self.get_variable(n, environment='INSTALL_ENVIRONMENT')) for n in names)
        if values['CS_URL']:
            url = urlparse(values['CS_URL'])
            values.update(URL_NETLOC=url.netloc, URL_PATH=url.path)
        return dict((k, v) for k, v in values.items() if v)

    def _check_pip_inputs(self):
        if self.args.skip_pip:
            return []
        problems = []
        tools = self.get_variable('TOOLS_PATH')
        if not tools:
            problems.append('TOOLS_PATH is not set in the site configuration')
        elif not os.path.isdir(tools):
            problems.append('TOOLS_PATH is not a directory: {}'.format(tools))
        index = self._index_settings()
        if 'CS_URL' in index:
            # The configuration can carry credentials, so its content is checked but never echoed
            listing = self._run('preflight', [sys.executable, '-m', 'pip', 'config', 'list'], mutating=False,
                                check=False, echo=False)
            if listing.returncode or index['URL_NETLOC'] not in listing.output:
                problems.append('pip is not configured for the package index {}; configure it first '
                                '(this mode never rewrites pip configuration)'.format(index['URL_NETLOC']))
        return problems

    def _release_pins(self):
        return parse_pins(self._opt('piprequirements'))

    def _check_pins(self):
        path = self._opt('piprequirements')
        if self.args.skip_pip or not path or not os.path.isfile(path):
            return []
        try:
            pins = self._release_pins()
        except UpdateError as e:
            return [e.detail]
        return self._check_mysqlclient_pin(pins)

    def _check_mysqlclient_pin(self, pins):
        """mysqlclient is never reinstalled, so the installed build must already be the pinned one"""
        for pin in pins:
            if pin.name == 'mysqlclient':
                break
        else:
            return []
        specs = list(pin.requirement.specifier)
        if len(specs) != 1 or specs[0].operator != '==':
            return ['mysqlclient is not pinned to an exact version in the release requirements: {}'.format(pin.line)]
        wanted = specs[0].version
        remedy = ('--ebi-compat never reinstalls it; rebuild it against the host MySQL client library '
                  'first (see docs/RunUpdate-EBI-compat.md)')
        try:
            installed = importlib.metadata.version('mysqlclient')
        except importlib.metadata.PackageNotFoundError:
            return ['mysqlclient=={} is pinned but not installed; {}'.format(wanted, remedy)]
        if Version(installed) != Version(wanted):
            return ['release pins mysqlclient=={} but {} is installed; {}'.format(wanted, installed, remedy)]
        return []

    def _checkout_release_tag(self, phase, repo, tag):
        """Fetch tags, check out tag and verify it; a detached HEAD at a tag is expected, so nothing is pulled"""
        git = ['git', '-C', repo]
        self._run(phase, git + ['fetch', '--tags'])
        if not self.args.noop and self._run(phase, git + ['rev-parse', '-q', '--verify', 'refs/tags/' + tag],
                                            mutating=False, check=False).returncode:
            raise UpdateError(phase, '{} is not a tag in {}; --ebi-compat requires a release tag'.format(tag, repo))
        self._run(phase, git + ['checkout', tag])
        if self.args.noop:
            print('[{}] noop: the checkout is not verified'.format(phase))
            return
        described = self._run(phase, git + ['describe', '--tags', '--exact-match'], mutating=False).output.strip()
        if described != tag:
            raise UpdateError(phase, '{} is at {}, expected {}'.format(repo, described, tag))

    def phase_resources(self):
        """Check out the release tag of resources_ro and fast-forward resources_rw when configured"""
        self._checkout_release_tag('resources', self.get_variable('RO_RESOURCE_PATH'), self._opt('resourcestag'))
        rw = self.get_variable('RW_RESOURCE_PATH')
        if rw:
            self._run('resources', ['git', '-C', rw, 'pull', '--ff-only'])

    def phase_remove(self):
        """Uninstall obsolete packages only where the installed version is exactly the listed one"""
        for entry in self._removepy_entries():
            name, _, wanted = entry.partition('=')
            wanted = wanted.lstrip('=')
            try:
                installed = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                print('[remove] {}: not installed, skipped'.format(name))
                continue
            if installed != wanted:
                print('[remove] {}: installed {}, not {}, skipped'.format(name, installed, wanted))
                continue
            self._run('remove', [sys.executable, '-m', 'pip', 'uninstall', '-y', name])

    def _pip(self, *args, **kwargs):
        return self._run(kwargs.pop('phase', 'pip'), [sys.executable, '-m', 'pip'] + list(args), **kwargs)

    def _pkg_config_path(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            babel = os.path.join(self._UpdateManager__ci_common.get_site_packages_path(), 'openbabel', 'lib',
                                 'pkgconfig')
        tools = self.get_variable('TOOLS_PATH')
        return '{}:{}'.format(os.path.join(tools, 'lib', 'pkgconfig'), babel) if tools else babel

    def phase_pip(self):
        """Install base requirements, then each release pin on its own with --no-deps"""
        constraints = os.path.join(BASE_PACKAGES_DIR, 'constraints.txt')
        env = {'PKG_CONFIG_PATH': self._pkg_config_path()}
        for name in ('pre-requirements.txt', 'requirements.txt'):
            self._pip('install', '-c', constraints, '-r', os.path.join(BASE_PACKAGES_DIR, name), env=env)
        for pin in self._release_pins():
            if pin.name == 'mysqlclient':
                continue
            self._pip('install', '--no-deps', '-c', constraints, pin.line)
        extra = self._opt('pip_extra_reqs')
        if extra:
            # The requirements file expands the index credentials itself, so they travel in the environment
            self._pip('install', '--no-deps', '-c', constraints, '-r', extra, env=self._index_settings())
        if self.args.noop:
            print('[pip] noop: pip check and the mysqlclient check are left to the final verification')
            return
        self._pip('check')
        self.check_mysqlclient_linkage()

    def check_mysqlclient_linkage(self):
        """MySQLdb must import and its extension module must have no unresolved shared libraries"""
        probe = self._run('mysqlclient', [sys.executable, '-c', MYSQLDB_PROBE], mutating=False, check=False)
        found = re.search(r'^MYSQL_SO=(.+)$', probe.output, re.MULTILINE)
        if probe.returncode or not found:
            raise UpdateError('mysqlclient', 'MySQLdb does not import cleanly:\n{}'.format(probe.output.strip()))
        linkage = self._run('mysqlclient', ['ldd', found.group(1)], mutating=False, check=False)
        missing = [line.strip() for line in linkage.output.splitlines() if 'not found' in line]
        if missing:
            raise UpdateError('mysqlclient', '{} has unresolved shared libraries:\n{}'.format(
                found.group(1), '\n'.join(missing)))

    def phase_webfe(self):
        """Check out the release tag, update submodules and check the web root against the release"""
        root = self._webfe_root()
        self._checkout_release_tag('webfe', root, self._opt('webfetag'))
        self._run('webfe', ['git', '-C', root, 'submodule', 'update', '--init', '--recursive'])
        checker = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ManageWebFE.py')
        self._run('webfe', [sys.executable, checker, '--webroot', root, 'check', '-r', self._opt('webfeconf')])

    def _taxonomy_row_count(self):
        """Rows in the status taxonomy table, counted as ManageTaxDB.py counts them"""
        from wwpdb.utils.db.MyConnectionBase import MyConnectionBase
        from wwpdb.utils.db.MyDbUtil import MyDbQuery
        db = MyConnectionBase()
        db.setResource(resourceName='STATUS')
        if not db.openConnection():
            raise UpdateError('taxdb', 'cannot open the status database to count the taxonomy table')
        try:
            rows = MyDbQuery(dbcon=db._dbCon).selectRows(queryString='select count(ordinal) from taxonomy ')
        finally:
            db.closeConnection()
        return rows[0][0]

    def phase_taxdb(self):
        """Load the taxonomy when it is out of range, then verify the table rather than trust the exit status"""
        minimum = int(self._opt('taxdbminsize'))
        maximum = int(self._opt('taxdbmaxsize') or DEFAULT_TAXDB_MAXSIZE)
        argv = [sys.executable, os.path.join(SCRIPT_DIR, 'ManageTaxDB.py')]
        if self.args.noop:
            argv.append('--noop')
        argv.extend(['--maxsize', str(maximum), '--taxdbsize', str(minimum)])
        if self._opt('taxuseftp') is not None:
            argv.append('--ftpload')
        # Under --noop the helper only reports what it would do, so it is always run
        result = self._run('taxdb', argv, mutating=False)
        failures = failure_lines(result.output, TAXDB_FAILURE_MARKERS)
        if failures:
            raise UpdateError('taxdb', 'ManageTaxDB.py reported:\n' + '\n'.join(failures))
        if self.args.noop:
            return
        count = self._taxonomy_row_count()
        if not minimum <= count < maximum:
            raise UpdateError('taxdb', 'the taxonomy table has {} rows after the load; expected at least {} and '
                              'fewer than {}'.format(count, minimum, maximum))
        print('[taxdb] taxonomy has {} rows'.format(count))

    def phase_schema(self):
        """ManageDB.py update, then the DepUI Django migrations, which it does not cover"""
        argv = [sys.executable, os.path.join(SCRIPT_DIR, 'ManageDB.py')]
        if self.args.noop:
            argv.append('--noop')
        result = self._run('schema', argv + ['update'], mutating=False)
        failures = failure_lines(result.output, SCHEMA_FAILURE_MARKERS)
        if failures:
            raise UpdateError('schema', 'ManageDB.py reported:\n' + '\n'.join(failures))

        # manage.py sets DJANGO_SETTINGS_MODULE itself unless the deployment already has it
        migrate = [sys.executable, '-m', 'wwpdb.apps.deposit.manage', 'migrate']
        planned = self._run('schema', migrate + ['--plan'], mutating=False).output
        if NO_PLANNED_MIGRATIONS in planned:
            return
        self._run('schema', migrate + ['--noinput'])
        if self.args.noop:
            return
        remaining = self._run('schema', migrate + ['--plan'], mutating=False).output
        if NO_PLANNED_MIGRATIONS not in remaining:
            raise UpdateError('schema', 'migrations are still planned after migrate:\n' + remaining.strip())

    def phase_build(self):
        raise UpdateError('build', '--build-tools is not implemented yet for --ebi-compat; build the tool bundles '
                          'in the Rocky 8 container yourself and re-run without --build-tools')

    def _capture_legacy_check(self, phase, check):
        """Run a stock check, which only prints, and turn its failure messages into an UpdateError"""
        out = io.StringIO()
        try:
            with warnings.catch_warnings(), contextlib.redirect_stdout(out):
                warnings.simplefilter('ignore')
                check()
        except Exception as e:
            raise UpdateError(phase, '{}: {}'.format(check.__name__, e))
        sys.stdout.write(out.getvalue())
        failures = failure_lines(out.getvalue(), TOOL_FAILURE_MARKERS)
        if failures:
            raise UpdateError(phase, '\n'.join(failures))

    def phase_verify(self):
        """Final checks; unlike the stock script, any failure here fails the run"""
        a = self.args
        if a.noop:
            print('[verify] noop: nothing was changed, so the final checks are not run')
            return
        if not a.skip_toolvers:
            self._capture_legacy_check('verify', self.checktoolvers)
            self._capture_legacy_check('verify', self.checkoelicense)
            result = self._run('verify', [sys.executable, os.path.join(SCRIPT_DIR, 'ManageDB.py'), 'finalcheck'],
                               mutating=False)
            failures = failure_lines(result.output, SCHEMA_FAILURE_MARKERS)
            if failures:
                raise UpdateError('verify', 'ManageDB.py finalcheck reported:\n' + '\n'.join(failures))
        if not a.skip_webfe:
            self._run('verify', [sys.executable, os.path.join(SCRIPT_DIR, 'ManageWebFE.py'), '--webroot',
                                 self._webfe_root(), 'check', '-r', self._opt('webfeconf')], mutating=False)
        self._pip('check', mutating=False, phase='verify')
        self.check_mysqlclient_linkage()


def run_ebi_update(config_file, args):
    """Run the update selected by args and return the process exit status"""
    config_file = os.path.abspath(config_file)
    updater = EbiUpdate(config_file, args)
    try:
        updater.run()
    except UpdateError as e:
        print('FAILED [{}]: {}'.format(e.phase, redact(e.detail, updater.secrets)))
        return 1
    print('ebi-compat update completed')
    return 0
