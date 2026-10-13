"""Opt-in, fail-fast release update for EBI RHEL 9 deployments (RunUpdate.py --ebi-compat)"""

import importlib.metadata
import os
import re
import shlex
import subprocess
import sys

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

    def _run(self, phase, argv, env=None, cwd=None, mutating=True, check=True):
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
        for check in (self._check_config, self._check_pins):
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
                  'first (see docs/RunUpdate-ebi-compat.md)')
        try:
            installed = importlib.metadata.version('mysqlclient')
        except importlib.metadata.PackageNotFoundError:
            return ['mysqlclient=={} is pinned but not installed; {}'.format(wanted, remedy)]
        if Version(installed) != Version(wanted):
            return ['release pins mysqlclient=={} but {} is installed; {}'.format(wanted, installed, remedy)]
        return []

    def phase_resources(self):
        raise NotImplementedError

    def phase_remove(self):
        raise NotImplementedError

    def phase_pip(self):
        raise NotImplementedError

    def phase_webfe(self):
        raise NotImplementedError

    def phase_taxdb(self):
        raise NotImplementedError

    def phase_schema(self):
        raise NotImplementedError

    def phase_build(self):
        raise NotImplementedError

    def phase_verify(self):
        raise NotImplementedError


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
