#!/bin/bash
# Builds packages with the onedep-build functions from inside the Rocky 8 build container.
# RunUpdate.py --build-tools --build-container starts it; it follows the sequence BuildTools.py uses for each
# package, which cannot find the build tree through the site configuration from inside the container.
#
# usage: BuildToolsInContainer.sh ENV_SCRIPT SITE_ID LOCATION_ID BUILD_TREE EPOCH PACKAGE...
#        BuildToolsInContainer.sh --check-epoch EPOCH
#
# --check-epoch only checks that EPOCH is the one of the container's distribution, and builds nothing.
#
# PYTHONPATH has to name the deployment's site-packages when this starts: the container's own Python is
# otherwise too bare to run the site environment scripts.

# The epoch has to be the one of the container's distribution (as get_build_ver in install_onedep.sh)
check_epoch() {
  local epoch=$1 distro want
  distro=$(. "${OS_RELEASE:-/etc/os-release}" && echo "$ID")
  case $distro in
    rocky|almalinux) want=v-6000;;
    centos) want=v-5200;;
    *) echo "ERROR: cannot choose a build epoch for a '$distro' container"; exit 1;;
  esac
  if [ "$epoch" != "$want" ]; then
    echo "ERROR: a $distro container builds with $want, not $epoch; pass --build-version $want"
    exit 1
  fi
}

if [ "$1" = "--check-epoch" ]; then
  check_epoch "$2"
  exit 0
fi

env_script=$1
site_id=$2
location_id=$3
build_tree=$4
epoch=$5
shift 5

check_epoch "$epoch"

. "$env_script" --siteid "$site_id" --locid "$location_id" --nocache || exit 1
export WWPDB_ONEDEP_BUILD=$build_tree
cd "$build_tree" || exit 1
. utils/pkg-utils-v2.sh || exit 1
get_environment
export FORCE_REBUILD=YES
. "$epoch/packages/all-packages.sh" || exit 1

# PYTHONPATH only served the site environment scripts above. Left set, the Python that a build function builds
# or uses would take the deployment's virtual environment as its own, and its pip would upgrade and remove the
# packages in it.
unset PYTHONPATH

# Nothing is built unless every function exists
for package; do
  declare -F "pkg_build_$package" >/dev/null || { echo "ERROR: pkg_build_$package is not defined in $build_tree/$epoch"; exit 1; }
done

[ -n "$DISTRIB_DIR" ] || { echo "ERROR: DISTRIB_DIR is not defined"; exit 1; }
# BuildTools.py deletes the download cache so that every file is fetched again; it is moved aside here instead
aside="$DISTRIB_DIR.aside.$(date +%Y%m%d%H%M%S)"
mkdir -p "$aside" && find "$DISTRIB_DIR" -mindepth 1 -maxdepth 1 -exec mv {} "$aside"/ \; || exit 1

# The container has no rsync, and the build functions only use it to copy a staged directory into place
command -v rsync >/dev/null || rsync() {
  local arg source= target=
  for arg; do
    case $arg in
      -*) ;;
      *) if [ -z "$source" ]; then source=$arg; else target=$arg; fi;;
    esac
  done
  cp -a "$source" "$target"
}

# Without OpenSSL headers in the tools tree, --with-openssl=$PREFIX builds a Python without the ssl module, and
# its pip then reaches no index. The functions that pass the flag run without it, so that the container's own
# OpenSSL is used. The functions in the build tree are not modified.
if [ ! -f "$PREFIX/include/openssl/ssl.h" ]; then
  for package; do
    body=$(declare -f "pkg_build_$package")
    case $body in
      *'--with-openssl=$PREFIX'*)
        echo "note: pkg_build_$package runs without --with-openssl=\$PREFIX, $PREFIX has no OpenSSL headers"
        eval "${body//--with-openssl=\$PREFIX/}";;
    esac
  done
fi

for package; do
  echo "==== pkg_build_$package"
  "pkg_build_$package" || { echo "ERROR: pkg_build_$package failed"; exit 1; }
done
