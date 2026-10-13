# RunUpdate.py `--ebi-compat`

`--ebi-compat` selects a strict, fail-fast release update for EBI deployments that run on RHEL 9 with the Rocky 8 toolchain. It is opt-in and is never inferred from the operating system. Without the flag, `RunUpdate.py` behaves exactly as before; other sites should not use it.

The logic lives in `scripts/RunUpdateEbi.py`. `RunUpdate.py` only parses the options and hands over.

## Invocation

Run with the deployment's virtual environment active, so that `python` is the deployment interpreter:

```bash
python scripts/RunUpdate.py --config V6.1/V61rel.conf --ebi-compat --noop      # plan, read-only
python scripts/RunUpdate.py --config V6.1/V61rel.conf --ebi-compat
```

The version in the example is only an illustration; use the release being applied.

## What it changes

The same phases run in the same order as the stock update, each honouring its `--skip-*` option. The first failed mandatory step stops the run with exit status 1, naming the phase and the command (credentials are masked).

| Phase | Behaviour |
|---|---|
| Preflight | Read-only. Checks the release configuration and files, that the interpreter is a virtual environment, the release pins, that the installed `mysqlclient` equals its pin, that the resources, writable resources and WebFE checkouts exist, are git checkouts and have no local changes (WebFE: untracked files included), `TOOLS_PATH`, that pip is already configured for the package index, the taxonomy bounds and source, and that the DepUI Django entry point imports. Every problem is reported together and nothing has been changed. |
| Resources | `git fetch --tags`, verify `resourcestag` is a tag, `git checkout`, then `git describe --tags --exact-match` must equal it. No `git pull` on the detached HEAD. The writable resources are fast-forwarded when configured. |
| Remove | A `removepy` package is uninstalled only when the installed version is exactly the listed one; anything else is a recorded skip. |
| Pip | Base requirements first (with the constraints file and `PKG_CONFIG_PATH`), then each release pin on its own with `--no-deps`, in file order, stopping at the first failure. `mysqlclient` is never reinstalled. `pip_extra_reqs`, if set, is installed once with `--no-deps`, with the index credentials passed through the environment. Then `pip check` must be clean and `MySQLdb` must import with every shared library of its extension resolved. pip's own configuration is never rewritten. |
| WebFE | Same tag-safe checkout for `webfetag`, submodules initialised and updated, then `ManageWebFE.py check`. Untracked files are listed and never removed: move them aside first. |
| Taxonomy | Runs `ManageTaxDB.py`, scans its output for errors (it exits 0 on failure), then re-reads the row count from the status database, which must lie in `[taxdbminsize, taxdbmaxsize)`. |
| Schema | `ManageDB.py update` with its output scanned, then the DepUI Django migrations: `migrate --plan`, `migrate --noinput`, and `migrate --plan` again, which must report nothing planned. `DJANGO_SETTINGS_MODULE` is left to the deployment; `wwpdb.apps.deposit.manage` sets its default. |
| Verify | The stock tool-version and OpenEye licence checks run with their output captured, so a mismatch, a missing bundle, an unreadable licence or an exception fails the run. Then `ManageDB.py finalcheck`, the WebFE check, `pip check` and the `mysqlclient` check. `--skip-toolvers` skips the tool, licence and `finalcheck` checks as in the stock update. |

## Options

- `--noop`: preflight, the read-only reports of `ManageTaxDB.py` and `ManageDB.py`, and `migrate --plan` run; nothing else does. The final verification is not run, because nothing changed.
- `--skip-resources`, `--skip-remove`, `--skip-pip`, `--skip-webfe`, `--skip-taxdb`, `--skip-schema`, `--skip-toolvers`: as in the stock update. Preflight and the `pip check` and `mysqlclient` checks always run.
- `--build-dev` cannot be combined with `--ebi-compat`.
- `--build-container PATH` names the Rocky 8 Singularity/Apptainer image for tool builds. It has no default, because its location is a site convention, and it is required with `--build-tools`.

### Tool builds: not available yet

`--build-tools` is accepted but stops with a clear error. Building the bundles inside the Rocky 8 container is not yet implemented. Build them yourself in the container, then run without `--build-tools`; the final verification then confirms that every bundle is at its pin.

## Consequences of the strict checks

- A tool bundle that is behind its pin fails the run, including bundles the release does not rebuild. Bring it to its pin first.
- `pip check` must be clean. A release whose dependency set has known, accepted conflicts will fail until they are resolved.
- The helper scripts' error output (lines containing `ERROR` or `FAILED`, and for the taxonomy `Could not`) fails the phase, even though those scripts exit 0.

## Left to the operator

- Snapshot and rollback record before the update.
- Moving source checkouts to their release tags, and saving local edits.
- Cleaning, stashing or exporting a dirty build tree.
- Loading the container runtime module.
- Running memory-hungry steps (taxonomy load, Phenix build) through the scheduler where login nodes have memory limits.
- Backing up the DepUI Django database before the migrations.
- Package-local tool environment checks, and rebuilding `mysqlclient` against the host client library when its pin changes.
- Restarting services and acceptance testing with a real entry.
