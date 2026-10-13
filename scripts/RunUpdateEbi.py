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
    raise NotImplementedError


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

    def phase_preflight(self):
        """Read-only validation of the selected phases' inputs; raises one UpdateError listing every problem"""
        raise NotImplementedError

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
