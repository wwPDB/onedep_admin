"""Opt-in, fail-fast release update for EBI RHEL 9 deployments (RunUpdate.py --ebi-compat)"""

import re
import subprocess
import sys

try:
    from .RunUpdate import UpdateManager
except ImportError:
    # Run as a script rather than as part of the scripts package
    from RunUpdate import UpdateManager


class UpdateError(Exception):
    """A mandatory operation failed; phase names where"""

    def __init__(self, phase, detail):
        super().__init__(detail)
        self.phase = phase
        self.detail = detail


def redact(text, secrets=()):
    """Mask credentials embedded in URLs and any literal secret values"""
    raise NotImplementedError


class RunResult(object):
    def __init__(self, returncode, output):
        self.returncode = returncode
        self.output = output


class EbiUpdate(UpdateManager):
    """Strict counterpart of UpdateManager; every mandatory failure raises UpdateError"""

    def __init__(self, config_file, args):
        raise NotImplementedError

    def _run(self, phase, argv, env=None, cwd=None, mutating=True, check=True):
        raise NotImplementedError

    def run(self):
        raise NotImplementedError

    def phase_preflight(self):
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
    raise NotImplementedError
