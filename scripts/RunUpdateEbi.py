"""Opt-in, fail-fast release update for EBI RHEL 9 deployments (RunUpdate.py --ebi-compat)"""

import contextlib
import glob
import importlib.metadata
import importlib.util
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
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
PIP_CHECK_LINE = re.compile(r'^(\S+) \S+ (?:has requirement|requires) ')
# The OpenEye 2020.2.4 wheel names a platform package that no index publishes; the installed one works
OPENEYE_METADATA_FAULT = re.compile(
    r'^openeye-toolkits \S+ requires openeye-toolkits-python3-python3-linux-x64, which is not installed\.?$')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GITHUB_URL = 'git@github.com:wwPDB/{}.git'
# Build epoch of onedep-build per container distribution, as install_onedep.sh get_build_ver
BUILD_EPOCHS = {'rocky': 'v-6000', 'almalinux': 'v-6000'}
# Release option that pins the bundle a build function produces, as the stock tool-version check reads it. A
# package that is not listed here (or has no pin in the release) cannot be judged and is always built
BUNDLE_PIN_OPTIONS = {'annotation_pack': 'annotver', 'chem_comp_pack': 'cctoolsver', 'sf_valid': 'sfvalidver',
                      'mapfix': 'mapfixver', 'findgeo': 'findgeover', 'metalcoord': 'metalcoordvers'}


def bundle_pin_option(package):
    """Name of the release option that pins the bundle of a pkg_build_ function, or None"""
    if package.startswith('phenix'):
        return 'phenixver'
    return BUNDLE_PIN_OPTIONS.get(package)
# Runs inside the build container; the positional arguments avoid any quoting of paths. It follows the
# sequence BuildTools.py runs for each package (BuildTools.py itself cannot find the build tree from inside
# the container): site environment, pkg-utils, get_environment, forced rebuild, the epoch's package
# functions. Arguments: environment script, site id, location id, build tree, epoch, "noop" or "", packages
BUILD_SCRIPT = r'''. "$1" --siteid "$2" --locid "$3" --nocache || exit 1
export WWPDB_ONEDEP_BUILD="$4"
epoch=$5
noop=$6
shift 6
cd "$WWPDB_ONEDEP_BUILD" || exit 1
. utils/pkg-utils-v2.sh || exit 1
get_environment
export FORCE_REBUILD=YES
. "$epoch/packages/all-packages.sh" || exit 1
# PYTHONPATH only serves the site environment scripts above; the image's own Python would otherwise treat the
# deployment's virtual environment as its own and let a build function upgrade or remove its packages
unset PYTHONPATH
for p; do
  declare -F "pkg_build_$p" >/dev/null || { echo "ERROR: pkg_build_$p is not defined in $WWPDB_ONEDEP_BUILD/$epoch"; exit 1; }
done
if [ -n "$noop" ]; then
  for p; do echo "noop: would build pkg_build_$p"; done
  exit 0
fi
[ -n "$DISTRIB_DIR" ] || { echo "ERROR: DISTRIB_DIR is not defined"; exit 1; }
# The stock build clears the download cache so that every file is fetched again; it is moved aside here instead
aside="$DISTRIB_DIR.aside.$(date +%Y%m%d%H%M%S)"
mkdir -p "$aside" && find "$DISTRIB_DIR" -mindepth 1 -maxdepth 1 -exec mv {} "$aside"/ \; || exit 1
# The build image has no rsync; the build functions only use it to copy a staged directory into place
command -v rsync >/dev/null || rsync() {
  local a src= dst=
  for a; do case $a in -*) ;; *) if [ -z "$src" ]; then src=$a; else dst=$a; fi;; esac; done
  cp -a "$src" "$dst"
}
# A tools tree without OpenSSL headers makes `--with-openssl=$PREFIX` build a Python with no ssl module (pip then
# reaches no index); the functions that pass the flag are redefined without it, so the image's own OpenSSL is used
if [ ! -f "$PREFIX/include/openssl/ssl.h" ]; then
  for p; do
    body=$(declare -f "pkg_build_$p")
    case $body in
      *'--with-openssl=$PREFIX'*)
        echo "note: pkg_build_$p runs without --with-openssl=\$PREFIX, $PREFIX has no OpenSSL headers"
        eval "${body//--with-openssl=\$PREFIX/}";;
    esac
  done
fi
for p; do
  echo "==== pkg_build_$p"
  "pkg_build_$p" || { echo "ERROR: pkg_build_$p failed"; exit 1; }
done
'''


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
                      self._check_pip_inputs, self._check_dev_checkouts, self._check_webfe, self._check_taxonomy,
                      self._check_django, self._check_build):
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
                  ('piprequirements', True)]
        for key, selected in needed:
            if selected and not self._opt(key):
                problems.append("release configuration has no '{}'".format(key))

        # The final verification reads the release requirements whichever phases are skipped
        files = [('webfeconf', not a.skip_webfe), ('piprequirements', True)]
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
            ancestor = os.path.dirname(path)
            while not os.path.isdir(ancestor):
                ancestor = os.path.dirname(ancestor)
            if not os.access(ancestor, os.W_OK):
                return ['{} {} does not exist and cannot be cloned: {} is not writable'.format(label, path, ancestor)]
            print('[preflight] {} {} does not exist; it will be cloned'.format(label, path))
            return []
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

    def _buildupdates(self):
        return (self._opt('buildupdates') or '').split()

    def _container_runtime(self):
        return shutil.which('apptainer') or shutil.which('singularity')

    def _check_build(self):
        a = self.args
        if not a.build_tools or not self._buildupdates():
            return []
        problems = []
        runtime = self._container_runtime()
        if not runtime:
            problems.append('neither apptainer nor singularity is on PATH; load the site module that provides one')
        if not a.build_container or not os.path.isfile(a.build_container):
            problems.append('--build-container is not a file: {}'.format(a.build_container))
        elif runtime:
            problems.extend(self._check_container_system(runtime))
        problems.extend('--build-bind is not a directory: {}'.format(path)
                        for path in a.build_bind or [] if not os.path.isdir(path))
        problems.extend(self._check_build_tree())
        config_dir = self.get_variable('TOP_WWPDB_SITE_CONFIG_DIR')
        if not config_dir:
            problems.append('TOP_WWPDB_SITE_CONFIG_DIR is not set in the site configuration')
        elif not os.path.isfile(os.path.join(config_dir, 'init', 'env.sh')):
            problems.append('the site environment script is not a file: {}'.format(
                os.path.join(config_dir, 'init', 'env.sh')))
        problems.extend('{} is not set in the site configuration'.format(name)
                        for name in ('WWPDB_SITE_ID', 'WWPDB_SITE_LOC') if not self.get_variable(name))
        return problems

    def _check_container_system(self, runtime):
        image = self.args.build_container
        result = self._run('preflight', [runtime, 'exec', image, 'cat', '/etc/os-release'], mutating=False,
                           check=False, echo=False)
        if result.returncode:
            lines = result.output.strip().splitlines()
            return ['cannot run the build container {}: {}'.format(image, lines[-1] if lines else 'no output')]
        fields = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', result.output, re.MULTILINE))
        distro, version = fields.get('ID', ''), fields.get('VERSION_ID', '')
        if distro not in BUILD_EPOCHS or not version.startswith('8.'):
            return ['the build container is {} {}, not a Rocky Linux or AlmaLinux 8 image'.format(distro, version)]
        if self.args.build_version != BUILD_EPOCHS[distro]:
            return ['--build-version is {} but a {} container builds with {}; pass --build-version {}'.format(
                self.args.build_version, distro, BUILD_EPOCHS[distro], BUILD_EPOCHS[distro])]
        return []

    def _check_build_tree(self):
        tree = self.get_variable('WWPDB_ONEDEP_BUILD')
        if not tree:
            return ['WWPDB_ONEDEP_BUILD is not set in the site configuration']
        if not os.path.isdir(tree):
            return ['WWPDB_ONEDEP_BUILD is not a directory: {}'.format(tree)]
        problems = self._check_checkout('onedep-build', tree)
        epoch = os.path.join(tree, self.args.build_version, 'packages')
        text = ''.join(open(path).read() for path in sorted(glob.glob(os.path.join(epoch, '*.sh'))))
        for pack in self._buildupdates():
            if not re.search(r'^\s*(function\s+)?pkg_build_{}\s*\(\)'.format(re.escape(pack)), text, re.MULTILINE):
                problems.append('pkg_build_{} is not defined in {}'.format(pack, epoch))
        return problems

    def _dev_repos_file(self):
        return os.path.join(BASE_PACKAGES_DIR, 'requirements_wwpdb_dependencies.txt')

    def _dev_repos(self):
        with open(self._dev_repos_file()) as fin:
            return [line.strip() for line in fin if line.strip()]

    def _dev_source_dir(self):
        return os.path.dirname(self._webfe_root())

    def _check_dev_checkouts(self):
        if not (self.args.build_dev and not self.args.skip_pip):
            return []
        if not self._webfe_root():
            return ['TOP_WWPDB_WEBAPPS_DIR is not set in the site configuration, so the source directory '
                    'of the develop checkouts is unknown']
        if not os.path.isfile(self._dev_repos_file()):
            return ['the list of develop repositories is not a file: {}'.format(self._dev_repos_file())]
        problems = []
        for repo in self._dev_repos():
            problems.extend(self._check_checkout(repo, os.path.join(self._dev_source_dir(), repo)))
        return problems

    def _release_pins(self, phase='pip'):
        """The release's pins; every failure is an UpdateError of the given phase"""
        path = self._opt('piprequirements')
        if not path:
            raise UpdateError(phase, "release configuration has no 'piprequirements'")
        try:
            return parse_pins(path)
        except OSError as e:
            raise UpdateError(phase, 'cannot read the release requirements {}: {}'.format(path, e))
        except UpdateError as e:
            raise UpdateError(phase, e.detail)

    def _check_pins(self):
        path = self._opt('piprequirements')
        if not path or not os.path.isfile(path):
            return []
        try:
            pins = self._release_pins('preflight')
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

    def _checkout_release_ref(self, phase, repo, ref):
        """Check out a release tag (detached, so nothing is pulled) or fast-forward a branch, then verify it"""
        git = ['git', '-C', repo]
        self._run(phase, git + ['fetch', '--tags'])

        def exists(name):
            return self._run(phase, git + ['rev-parse', '-q', '--verify', name], mutating=False,
                             check=False).returncode == 0

        is_tag = False
        if not self.args.noop:
            is_tag = exists('refs/tags/' + ref)
            if not is_tag and not exists('refs/remotes/origin/' + ref):
                raise UpdateError(phase, '{} is neither a tag nor a branch in {}'.format(ref, repo))
        self._run(phase, git + ['checkout', ref])
        if self.args.noop:
            print('[{}] noop: a branch is then fast-forwarded; nothing is verified'.format(phase))
            return
        if is_tag:
            described = self._run(phase, git + ['describe', '--tags', '--exact-match'],
                                  mutating=False).output.strip()
            if described != ref:
                raise UpdateError(phase, '{} is at {}, expected {}'.format(repo, described, ref))
            return
        self._run(phase, git + ['pull', '--ff-only', 'origin', ref])
        current = self._run(phase, git + ['rev-parse', '--abbrev-ref', 'HEAD'], mutating=False).output.strip()
        if current != ref:
            raise UpdateError(phase, '{} is on {}, expected branch {}'.format(repo, current, ref))

    def _make_directory(self, phase, path):
        if os.path.isdir(path):
            return
        print('[{}] mkdir -p {}'.format(phase, path))
        if self.args.noop:
            print('[{}] noop: not executed'.format(phase))
            return
        os.makedirs(path)

    def _clone_if_missing(self, phase, repo, path, *options):
        if not os.path.isdir(path):
            self._run(phase, ['git', 'clone'] + list(options) + [GITHUB_URL.format(repo), path])

    def phase_resources(self):
        """Check out the release tag or branch of resources_ro and fast-forward resources_rw when configured"""
        ro = self.get_variable('RO_RESOURCE_PATH')
        self._clone_if_missing('resources', 'onedep-resources_ro', ro)
        self._checkout_release_ref('resources', ro, self._opt('resourcestag'))
        rw = self.get_variable('RW_RESOURCE_PATH')
        if rw:
            self._clone_if_missing('resources', 'onedep-resources_rw', rw)
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

    def _pip_check(self, phase, mutating=True):
        """pip check; only problems reported for a package of the release fail, the others are noted"""
        result = self._pip('check', phase=phase, mutating=mutating, check=False)
        if not result.returncode:
            return
        release = set(pin.name for pin in self._release_pins(phase))
        problems = []
        for line in filter(None, (l.strip() for l in result.output.splitlines())):
            match = PIP_CHECK_LINE.match(line)
            if not match:
                problems.append(line)
            elif OPENEYE_METADATA_FAULT.match(line):
                print('[{}] note: accepted, the OpenEye wheel metadata names a package that does not exist: '
                      '{}'.format(phase, line))
            elif canonicalize_name(match.group(1)) in release:
                problems.append(line)
            else:
                print('[{}] note: {} is not part of the release, not failing on: {}'.format(
                    phase, match.group(1), line))
        if problems or not result.output.strip():
            raise UpdateError(phase, 'pip check reported problems for the release packages: {}'.format(
                ' | '.join(problems) or 'exit {} without output'.format(result.returncode)))

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
        # A develop build installs the wwPDB packages from their checkouts instead of their release pins
        provided = set(canonicalize_name(repo[3:] if repo.startswith('py-') else repo)
                       for repo in self._dev_repos()) if self.args.build_dev else set()
        for pin in self._release_pins():
            if pin.name == 'mysqlclient' or pin.name in provided:
                continue
            self._pip('install', '--no-deps', '-c', constraints, pin.line)
        extra = self._opt('pip_extra_reqs')
        if extra:
            # The requirements file expands the index credentials itself, so they travel in the environment
            self._pip('install', '--no-deps', '-c', constraints, '-r', extra, env=self._index_settings())
        if self.args.build_dev:
            self._install_dev_checkouts(constraints)
        if self.args.noop:
            print('[pip] noop: pip check and the mysqlclient check are left to the final verification')
            return
        self._pip_check('pip')
        self.check_mysqlclient_linkage()

    def _install_dev_checkouts(self, constraints):
        """Develop checkouts of the wwPDB packages, installed editable; an existing checkout keeps its branch"""
        source = self._dev_source_dir()
        self._make_directory('pip', source)
        for repo in self._dev_repos():
            path = os.path.join(source, repo)
            if not os.path.isdir(path):
                self._clone_if_missing('pip', repo, path, '--recursive')
                self._run('pip', ['git', '-C', path, 'checkout', 'develop'])
            elif self._run('pip', ['git', '-C', path, 'symbolic-ref', '-q', 'HEAD'], mutating=False,
                           check=False).returncode == 0:
                self._run('pip', ['git', '-C', path, 'pull', '--ff-only'])
            else:
                print('[pip] {} is on a detached HEAD; left as it is'.format(path))
            self._pip('install', '--no-deps', '-c', constraints, '-e', path)

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
        if not os.path.isdir(root):
            self._make_directory('webfe', os.path.dirname(root))
            self._clone_if_missing('webfe', 'onedep-webfe', root, '--recurse-submodules')
        self._checkout_release_ref('webfe', root, self._opt('webfetag'))
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
        """Run the release's build functions in the Rocky 8 container, then check the bundles they should produce"""
        a = self.args
        if not self._buildupdates():
            print('[build] nothing to build: the release lists no buildupdates')
            return
        packages = self._packages_to_build()
        if not packages:
            print('[build] nothing to build: every bundle is already at its pin')
            return
        binds = []
        for path in ([os.path.dirname(SCRIPT_DIR), self.get_variable('TOP_WWPDB_SITE_CONFIG_DIR'),
                       self.get_variable('TOOLS_PATH'), self.get_variable('WWPDB_ONEDEP_BUILD'), sys.prefix] +
                      list(a.build_bind or [])):
            if path and path not in binds:
                binds.append(path)
        argv = [self._container_runtime(), 'exec']
        for path in binds:
            argv.extend(['--bind', path])
        argv.extend(['--env', 'PYTHONPATH=' + sysconfig.get_paths()['purelib'], a.build_container,
                     'bash', '--noprofile', '--norc', '-c', BUILD_SCRIPT, 'bash',
                     os.path.join(self.get_variable('TOP_WWPDB_SITE_CONFIG_DIR'), 'init', 'env.sh'),
                     self.get_variable('WWPDB_SITE_ID'), self.get_variable('WWPDB_SITE_LOC'),
                     self.get_variable('WWPDB_ONEDEP_BUILD'), a.build_version, 'noop' if a.noop else ''] +
                    packages)
        # Under --noop the script itself only prints its plan, so the container is still run
        self._run('build', argv, mutating=False)
        if not a.noop and not a.skip_toolvers:
            # A build function can fail without failing the sequence, so the bundles are the evidence
            self._capture_legacy_check('build', self.checktoolvers)

    def _packages_to_build(self):
        """The release's packages minus those whose bundle the stock check finds at its pin"""
        packages = self._buildupdates()
        if self.args.force_build or self.args.skip_toolvers:
            return packages
        todo = []
        for package in packages:
            option = bundle_pin_option(package)
            if option and self._bundle_at_pin(option):
                print('[build] skipping pkg_build_{}: its bundle is already at its pin ({} = {}); '
                      '--force-build rebuilds it'.format(package, option, self._opt(option)))
            else:
                todo.append(package)
        return todo

    def _bundle_at_pin(self, option):
        """True only when the stock check, run for this one pin, finds the installed bundle at it"""
        pin = self._opt(option)
        if not pin:
            return False
        with tempfile.TemporaryDirectory() as tmp:
            conf = os.path.join(tmp, 'pin.conf')
            with open(conf, 'w') as fout:
                fout.write('[DEFAULT]\n{} = {}\n'.format(option, pin.replace('%', '%%')))
            single = EbiUpdate(conf, self.args)
            try:
                output = single._legacy_check_output('build', single.checktoolvers)
            except UpdateError:
                return False
        return not failure_lines(output, TOOL_FAILURE_MARKERS)

    def _legacy_check_output(self, phase, check):
        """Run a stock check, which only prints, and return what it printed"""
        out = io.StringIO()
        try:
            with warnings.catch_warnings(), contextlib.redirect_stdout(out):
                warnings.simplefilter('ignore')
                check()
        except Exception as e:
            raise UpdateError(phase, '{}: {}'.format(check.__name__, e))
        return out.getvalue()

    def _capture_legacy_check(self, phase, check):
        """Run a stock check, which only prints, and turn its failure messages into an UpdateError"""
        output = self._legacy_check_output(phase, check)
        sys.stdout.write(output)
        failures = failure_lines(output, TOOL_FAILURE_MARKERS)
        if failures:
            raise UpdateError(phase, '\n'.join(failures))

    def _verify_finalcheck(self):
        result = self._run('verify', [sys.executable, os.path.join(SCRIPT_DIR, 'ManageDB.py'), 'finalcheck'],
                           mutating=False)
        failures = failure_lines(result.output, SCHEMA_FAILURE_MARKERS)
        if failures:
            raise UpdateError('verify', 'ManageDB.py finalcheck reported:\n' + '\n'.join(failures))

    def _verify_mysqlclient_pin(self):
        """mysqlclient is never reinstalled; the installed build must be the pinned one when the run ends"""
        problems = self._check_mysqlclient_pin(self._release_pins('verify'))
        if problems:
            raise UpdateError('verify', '\n'.join(problems))

    def _verify_webfe(self):
        self._run('verify', [sys.executable, os.path.join(SCRIPT_DIR, 'ManageWebFE.py'), '--webroot',
                             self._webfe_root(), 'check', '-r', self._opt('webfeconf')], mutating=False)

    def phase_verify(self):
        """Final checks; any failure fails the run, except under --noop where they are only reported"""
        a = self.args
        checks = []
        if not a.skip_toolvers:
            checks.extend([lambda: self._capture_legacy_check('verify', self.checktoolvers),
                           lambda: self._capture_legacy_check('verify', self.checkoelicense),
                           self._verify_finalcheck])
        if not a.skip_webfe:
            checks.append(self._verify_webfe)
        checks.extend([lambda: self._pip_check('verify', mutating=False), self._verify_mysqlclient_pin,
                       self.check_mysqlclient_linkage])
        for check in checks:
            try:
                check()
            except UpdateError as e:
                if not a.noop:
                    raise
                print('[verify] noop: would fail: {}'.format(redact(e.detail, self.secrets)))


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
