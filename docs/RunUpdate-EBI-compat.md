# RunUpdate.py `--ebi-compat`

`--ebi-compat` selects a strict, fail-fast release update for EBI deployments that run on RHEL 9 with the Rocky 8 toolchain. It does what the stock `RunUpdate.py` does, in the same order and with the same `--skip-*` options, except where RHEL 9 compatibility requires otherwise (listed under *Differences from the stock update*). It is opt-in and is never inferred from the operating system; other sites should not use it.

The logic lives in `scripts/RunUpdateEbi.py`. `RunUpdate.py` only parses the options and hands over. Without the flag it behaves exactly as before.

## Invocation

Run with the deployment's virtual environment active, so that `python` is the deployment interpreter.

Production (release tags, wheels from the package index):

```bash
python scripts/RunUpdate.py --config V6.1/V61rel.conf --ebi-compat --noop
python scripts/RunUpdate.py --config V6.1/V61rel.conf --ebi-compat
python scripts/RunUpdate.py --config V6.1/V61rel.conf --ebi-compat --build-tools \
    --build-container /path/to/builder_rocky8.sif --build-version v-6000
```

Test servers (develop code of the wwPDB packages; set `resourcestag` and `webfetag` to the branch in the release configuration):

```bash
python scripts/RunUpdate.py --config V6.1/V61rel.conf --ebi-compat --build-dev
```

The release in the examples is only an illustration.

## Phases

A failed mandatory step stops the run with exit status 1 and names the phase and the command (credentials are masked).

| Phase | Behaviour |
|---|---|
| Preflight | Read-only. Checks the release configuration and files, that the interpreter is a virtual environment, the release pins, that the installed `mysqlclient` equals its pin, the resources, writable resources and WebFE checkouts (git checkouts without local changes; WebFE: untracked files included; a missing one is planned for cloning if its parent is writable), `TOOLS_PATH`, that pip is already configured for the package index, the taxonomy bounds and source, that the DepUI Django entry point imports, and, with `--build-dev` or `--build-tools`, their inputs. Every problem is reported together and nothing has been changed. |
| Resources | Clones `resources_ro` and `resources_rw` if missing. `resourcestag` may be a tag or a branch. A tag is checked out detached and verified with `git describe --tags --exact-match`, with no pull. A branch is checked out, fast-forwarded from `origin` and verified. A reference that is neither stops the run. `resources_rw` is fast-forwarded (`pull --ff-only`), so it must be on a branch. |
| Remove | A `removepy` package is uninstalled only when the installed version is exactly the listed one; anything else is a recorded skip. |
| Pip | Base requirements first (constraints file and `PKG_CONFIG_PATH` as in the stock update), then each release pin on its own with `--no-deps`, in file order, stopping at the first failure. `mysqlclient` is never reinstalled. `pip_extra_reqs`, if set, is installed once with `--no-deps`, with the index credentials passed through the environment. Then `pip check` must report no problem for a package of the release, and `MySQLdb` must import with every shared library of its extension resolved. With `--build-dev`, see below. |
| WebFE | Clones the checkout if missing (with submodules, creating the source directory). `webfetag` may be a tag or a branch, handled as above. Submodules are initialised and updated, then `ManageWebFE.py check` must pass. Untracked files are listed and never removed: move them aside first. |
| Taxonomy | Runs `ManageTaxDB.py`, scans its output for errors (it exits 0 on failure), then re-reads the row count from the status database, which must lie in `[taxdbminsize, taxdbmaxsize)`. |
| Schema | `ManageDB.py update` with its output scanned, then the DepUI Django migrations: `migrate --plan`, `migrate --noinput`, and `migrate --plan` again, which must report nothing planned. `DJANGO_SETTINGS_MODULE` is left to the deployment; `wwpdb.apps.deposit.manage` sets its default. |
| Build | Only with `--build-tools`. See below. |
| Verify | The stock tool-version and OpenEye licence checks run with their output captured, so a mismatch, a missing bundle, an unreadable licence or an exception fails the run. Then `ManageDB.py finalcheck`, the WebFE check, `pip check` and the `mysqlclient` check. `pip check` fails only for packages named in the release requirements; a problem reported for any other installed package (for example a site tool that pins its own versions) is printed as a note and does not fail the run. One fault is accepted by name: the OpenEye 2020.2.4 wheel asks for a platform package called `openeye-toolkits-python3-python3-linux-x64`, which no index publishes, while the installed `OpenEye-toolkits-python3-linux-x64` works. Output that names no package, or a failure without output, always fails. `--skip-toolvers` skips the tool, licence and `finalcheck` checks as in the stock update. |

### Develop build (`--build-dev`)

For test servers. As in the stock update, the wwPDB packages come from the `develop` branch of their repositories (the list is `base_packages/requirements_wwpdb_dependencies.txt`), cloned into the source directory next to the WebFE checkout and installed editable. Compared with the stock update:

- the release's non-wwPDB pins are still installed, one at a time with `--no-deps`; the wwPDB pins are replaced by the checkouts;
- the editable installs use `--no-deps`; a dependency that new develop code needs and the release does not pin shows up in `pip check`;
- an existing checkout keeps its branch, is required to have no tracked changes, and is only fast-forwarded (a detached HEAD is left as it is).

### Tool build (`--build-tools`)

The release's `buildupdates` are built by their `pkg_build_*` functions inside the Rocky 8 Singularity/Apptainer image given with `--build-container` (required, no default, because its location is a site convention). `BuildTools.py` is not used, because from inside the image it cannot find the build tree through the site configuration. Instead the same sequence it runs is followed: the site environment is sourced (`--nocache`), `utils/pkg-utils-v2.sh`, `get_environment`, a forced rebuild, the epoch's `all-packages.sh`, then each function in the order the release lists them, stopping at the first one that fails. `PYTHONPATH`, which the site environment scripts need to read the configuration, is unset before the first build function, so the image's own Python cannot see the deployment's virtual environment (a build function that runs `pip` would otherwise upgrade and remove packages in it). Three differences from the stock build: the download cache (`$DISTRIB_DIR`) is moved aside to `$DISTRIB_DIR.aside.<timestamp>` instead of being deleted; because the image has no `rsync`, a plain copy stands in for it when `rsync` is missing; and when `$PREFIX/include/openssl/ssl.h` does not exist, the build functions being run that pass `--with-openssl=$PREFIX` to Python's `configure` (it would otherwise build without the `ssl` module, so that `pip` reaches no index) run without that flag, and the image's own OpenSSL is used. The functions in the build tree are not modified, and a note names each function run this way.

A package whose bundle is already at the release pin is skipped, and the run says so. The decision uses the stock tool-version check, run for that one pin, so a bundle counts as current only when the check finds it at the pin; a package is built when the release has no pin for it, when the bundle is missing or at another version, when the check cannot run, when the package is not one the check knows (anything other than the annotation, chemical component, mapFix, sf-valid, FindGeo, metalcoord and Phenix bundles), or with `--skip-toolvers`. `--force-build` builds every listed package regardless. When all are current, the container is not run.

The build tree is the one `WWPDB_ONEDEP_BUILD` names (set in the site configuration or exported before the run). The admin, site-config, tools, build-tree and virtual-environment directories are bound in; add others with `--build-bind PATH` (repeatable). The preflight checks that the runtime is on `PATH`, that the image reports a Rocky or AlmaLinux 8 system, that `--build-version` is the epoch of that distribution (`v-6000`; the stock default `v-5200` is rejected with a message), that the `onedep-build` tree has no tracked changes and defines every `pkg_build_*` function the release lists, and that the site environment resolves. A build function can fail without failing the sequence, so the bundle versions are checked against the release pins afterwards (unless `--skip-toolvers`); a bundle that is behind its pin therefore fails the run, including bundles an earlier release should have built.

## Options

- `--noop`: the preflight runs, as do the read-only reports of `ManageTaxDB.py` and `ManageDB.py`, `migrate --plan` and the build script's `--noop` plan (it checks that every build function exists); nothing else is executed. The read-only final checks also run, and each problem is printed as `noop: would fail`; they do not change the exit status.
- `--skip-resources`, `--skip-remove`, `--skip-pip`, `--skip-webfe`, `--skip-taxdb`, `--skip-schema`, `--skip-toolvers`: as in the stock update. The preflight and the `pip check` and `mysqlclient` checks always run.
- `--build-container PATH`, `--build-bind PATH`: only with `--ebi-compat`; the container is required with `--build-tools`.
- `--force-build`: only with `--ebi-compat`; rebuild every package the release lists, including those whose bundle is already at its pin.

## Differences from the stock update

- Every mandatory failure stops the run with a nonzero exit status; the stock update only prints.
- Release pins are installed one at a time with `--no-deps`, never as one `pip install -U -r`, and `mysqlclient` is kept; a blanket reinstall would upgrade unrequested packages and rebuild `mysqlclient` against the wrong library.
- pip's configuration and credentials are not rewritten; pip must already be configured for the package index.
- A tag is checked out detached and never pulled; a branch is fast-forwarded only.
- The DepUI Django migrations are applied.
- Tools are built in the container, not on the host, with the `rsync` and OpenSSL adjustments described under *Tool build*.
- Packages whose bundle is already at its pin are not rebuilt (`--force-build` overrides).
- The taxonomy load and the helper scripts' results are verified, because those scripts exit 0 after printing errors.

## Host prerequisites enforced by the strict checks

These must be true on the host; the checks are not waived:

- every tool bundle is at the version the release pins (including bundles the release does not rebuild, such as FindGeo and metalcoord);
- `pip check` reports no problem for a package of the release;
- `resources_rw` is on a branch;
- the installed `mysqlclient` equals the release pin and its extension has no unresolved libraries;
- the WebFE checkout has no untracked files.

## Left to the operator

- Snapshot and rollback record before the update.
- Moving source checkouts to their release tags (production), and saving local edits.
- Cleaning, stashing or exporting a dirty build tree: the script never touches it.
- Loading the container runtime module.
- Running memory-hungry steps (taxonomy load, Phenix build) through the scheduler where login nodes have memory limits.
- Backing up the DepUI Django database before the migrations.
- Package-local tool environment checks, and rebuilding `mysqlclient` against the host client library when its pin changes.
- Restarting services and acceptance testing with a real entry.
