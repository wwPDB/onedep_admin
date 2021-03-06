#!/usr/bin/env bash

# ----------------------------------------------------------------
# script variables
# ----------------------------------------------------------------

# internal
HOSTNAME=$(hostname)
PYTHON2="python2"
PYTHON3="python3"
ONEDEP_BUILD_VER="v-5200"
THIS_SCRIPT="${BASH_SOURCE[0]}"
CENTOS_MAJOR_VER=`cat /etc/redhat-release | cut -d' ' -f4  | cut -d'.' -f1`

# repositories
ONEDEP_BUILD_REPO_URL=git@github.com:wwPDB/onedep-build.git # scripts to build tools for OneDep
ONEDEP_ADMIN_REPO_URL=git@github.com:wwPDB/onedep_admin.git # OneDep package management
ONEDEP_MAINTENANCE_REPO_URL=git@github.com:wwPDB/onedep-maintenance.git # scripts to setup and maintain OneDep

# ----------------------------------------------------------------
# helper functions
# ----------------------------------------------------------------

#
# simple colored warning message
function show_error_message {
    echo -e "\e[31m[!] $1\e[0m"
}

#
# simple colored warning message
function show_warning_message {
    echo -e "\e[93m[!] $1\e[0m"
}

#
# simple colored info message
function show_info_message {
    echo -e "\e[32m[+] $1\e[0m"
}

function highlight_text {
    echo -ne "\e[93m$1\e[0m"
}

#
# checks for the existence of environment variables
#   $1: variable name
#   $2: is required
function check_env_variable {
    local env_var=$(declare -p "$1" 2> /dev/null) # little hack
    local required=$2

    if [[ -z "$env_var" ]]; then
        if [[ $2 ]]; then
            show_error_message "$1 environment variable not set, exiting"
            exit 1
        else
            show_warning_message "$1 environment variable not set"
        fi
    fi
    
    return 0
}

#
# downloads the file given by the url in the first argument if it can't
# find the file in the current dir
#   $1: url
function download_file {
    local url=$1
    local filename="${url##*/}"

    if [[ ! -f "$filename" ]]; then
        wget $url
    else
        echo "[*] file $(highlight_text $filename) already exists, skipping"
    fi
}

# ----------------------------------------------------------------
# arguments parsing
# ----------------------------------------------------------------

# arguments
ONEDEP_VERSION="latest"
SITE_ID="${WWPDB_SITE_ID}"
SITE_LOC="${WWPDB_SITE_LOC}"
MACHINE_ENV="production"
OPT_DO_INSTALL=false
OPT_DO_BUILD=false
OPT_PREPARE_BUILD=false
OPT_DO_RUNUPDATE=false
OPT_DO_MAINTENANCE=false
OPT_DO_APACHE=false
OPT_DO_BUILD_DEV=false
SPECIFIC_PACKAGE=''

read -r -d '' USAGE << EOM
Usage: ${THIS_SCRIPT} [--config-version] [--python3-path] [--install-base] [--build-tools] [--run-update] [--run-maintenance] [--prepare-to-build-tools] [--install-specific-package] [-install-develop-as-edit]
    --config-version:       OneDep config version, defaults to 'latest'
    --python3-path:         path to a Python interpreter, defaults to 'python3'
    --install-base:         install base packages
    --prepare-to-build-tools   install packages ready for building tools
    --build-tools:          build OneDep tools
    --run-update:           perform RunUpdate.py step
    --run-maintenance:      perform maintenance tasks
    --setup-apache:         setup the apache
    --install-develop-as-edit install onedep packages in edit mode as develop version
    --install-specific-package: install a specific package into the OneDep venv
EOM

while [[ $# > 0 ]]
do
    key="$1"
    case $key in
        # optional args
        --version)
            ONEDEP_VERSION="$2"
            shift
        ;;
        --python3-path)
            PYTHON3="$2"
            shift
        ;;
        --install-specific-package)
            SPECIFIC_PACKAGE="$2"
            shift
        ;;
        --install-base) OPT_DO_INSTALL=true;;
        --build-tools) OPT_DO_BUILD=true;;
        --run-update) OPT_DO_RUNUPDATE=true;;
        --run-maintenance) OPT_DO_MAINTENANCE=true;;
        --setup-apache) OPT_DO_APACHE=true;;
        --prepare-to-build-tools) OPT_PREPARE_BUILD=true;;
        --install-develop-as-edit) OPT_DO_BUILD_DEV=true;;
        --help)
            echo "$USAGE"
            exit 1
        ;;
        *)
            # unknown option
            echo "Unknown option '$key'"
            echo "$USAGE"
            exit 1
        ;;
    esac
    shift # past argument or value
done

# ----------------------------------------------------------------
# checking required environment settings
# some new ones are created based on them (e.g. ONEDEP_PATH)
# ----------------------------------------------------------------

check_env_variable WWPDB_SITE_ID true
check_env_variable WWPDB_SITE_LOC true
check_env_variable ONEDEP_PATH true

echo -e "----------------------------------------------------------------"
echo -e "[*] $(highlight_text WWPDB_SITE_ID) is set to $(highlight_text $WWPDB_SITE_ID)"
echo -e "[*] $(highlight_text WWPDB_SITE_LOC) is set to $(highlight_text $WWPDB_SITE_LOC)"
echo -e "[*] $(highlight_text ONEDEP_PATH) is set to $(highlight_text $ONEDEP_PATH)"

export SITE_CONFIG_DIR=$ONEDEP_PATH/site-config
export TOP_WWPDB_SITE_CONFIG_DIR=$ONEDEP_PATH/site-config

echo -e "[*] $(highlight_text SITE_CONFIG_DIR) is set to $(highlight_text $SITE_CONFIG_DIR)"
echo -e "[*] $(highlight_text TOP_WWPDB_SITE_CONFIG_DIR) is set to $(highlight_text $TOP_WWPDB_SITE_CONFIG_DIR)"
echo -e "[*] using $(highlight_text $PYTHON3) as Python 3 interpreter"
echo -e "----------------------------------------------------------------"

# ----------------------------------------------------------------
# install required packages
#
# the packages seem different if we choose not to compile the tools,
# got the package list from Confluence, links below
# ----------------------------------------------------------------

# checking for the existence of the root dir of installation
if [[ ! -d $ONEDEP_PATH ]]; then
    show_error_message "path $ONEDEP_PATH does not exist, exiting..."
    exit 1
fi

cd $ONEDEP_PATH

if [[ ( $OPT_DO_INSTALL == true || $OPT_DO_BUILD == true || $OPT_DO_RUNUPDATE == true || $OPT_PREPARE_BUILD == true ) && ! -d "onedep-build" ]]; then
    show_info_message "cloning onedep-build repository"
    git clone $ONEDEP_BUILD_REPO_URL
fi

if [[ $OPT_DO_INSTALL == true ]]; then
    show_info_message "installing required packages"
    command=''

    if [[ $OPT_DO_BUILD == true || $OPT_PREPARE_BUILD == true ]]; then
        show_info_message "installing packages and compiling tools"
        command=onedep-build/install-base/centos-7-build-packages.sh
    else
        show_info_message "installing packages without compiling"
        if [[ $CENTOS_MAJOR_VER == 8 ]]; then
          command=onedep-build/install-base/centos-8-host-packages.sh
        else
          command=onedep-build/install-base/centos-7-host-packages.sh
        fi
    fi
    show_warning_message "running command: $command"
    chmod +x $command
    $command
else
    show_warning_message "skipping installation of required packages"
fi

# ----------------------------------------------------------------
# setting up directories used by onedep and python venv
# ----------------------------------------------------------------

show_info_message "setting up Python virtual env"

# delete if it already exists
if [[ -d "/tmp/venv" ]]; then
    rm -rf /tmp/venv
fi

unset PYTHONHOME
$PYTHON3 -m venv /tmp/venv
source /tmp/venv/bin/activate

# ----------------------------------------------------------------
# setting up directories used by OneDep and python venv
# ----------------------------------------------------------------


show_info_message "updating setuptools"
pip install --no-cache-dir --upgrade setuptools==40.8.0 pip

show_info_message "installing wheel"
pip install --no-cache-dir wheel

show_info_message "installing wwpdb.utils.config"
pip install --no-cache-dir PyYaml==3.10 wwpdb.utils.config

show_info_message "compiling site-config for the new site"
ConfigInfoFileExec --siteid $WWPDB_SITE_ID --locid $WWPDB_SITE_LOC --writecache

deactivate
rm -rf /tmp/venv

cd $ONEDEP_PATH

show_info_message "activating the new configuration"
# activate_configuration
. site-config/init/env.sh --siteid $WWPDB_SITE_ID --location $WWPDB_SITE_LOC

# now checking if DEPLOY_DIR has been set
show_info_message "checking if everything went ok..."

echo "[*] $(highlight_text DEPLOY_DIR) is set to $(highlight_text $DEPLOY_DIR)"
check_env_variable DEPLOY_DIR true

show_info_message "cloning onedep repositories"

if [[ ! -d "onedep_admin" ]]; then
    git clone $ONEDEP_ADMIN_REPO_URL
    
    cd onedep_admin

    git checkout master
    git pull

    cd ..
fi

if [[ $OPT_DO_MAINTENANCE == true && ! -d "onedep-maintenance" ]]; then
    git clone $ONEDEP_MAINTENANCE_REPO_URL
fi

#show_info_message "creating 'resources' folder"

#mkdir -p $DEPLOY_DIR/resources

if [[ $OPT_DO_BUILD == true ]]; then
    show_info_message "now building, this may take a while"
    cd $ONEDEP_PATH/onedep-build/$ONEDEP_BUILD_VER/build-centos-$CENTOS_MAJOR_VER # maybe I should put the build version in a variable
    ./BUILD.sh |& tee build.log
else
    show_warning_message "skipping build"
fi

# ----------------------------------------------------------------
# setting up OneDep virtual env
# ----------------------------------------------------------------

show_info_message "setting up OneDep virtual environment"

cd $ONEDEP_PATH
unset PYTHONHOME

if [[ -z "$VENV_PATH" ]]; then
    VENV_PATH=$(echo $PYTHONPATH | cut -d":" -f1)
fi

if [[ -z "$VENV_PATH" ]]; then
    show_error_message "VENV_PATH not set, quitting..."
    exit 1
fi

show_info_message "setting up OneDep virtual environment in $(highlight_text $VENV_PATH)"

$PYTHON3 -m venv $VENV_PATH
source $VENV_PATH/bin/activate

# adding pip config file
show_info_message "creating pip configuration file"

if [[ ! -z "$CS_HOST_BASE" && ! -z "$CS_USER" && ! -z "$CS_PW" && ! -z "$CS_DISTRIB_URL" ]]; then
    pip config --site set global.trusted-host $CS_HOST_BASE
    pip config --site set global.extra-index-url "http://${CS_USER}:${CS_PW}@${CS_DISTRIB_URL} https://pypi.anaconda.org/OpenEye/simple"
    pip config --site set global.no-cache-dir false
else
    show_warning_message "some of the environment variables for the private RCSB Python repository are not set"
fi

show_info_message "install some base packages"
pip install wheel

pip install wwpdb.utils.config

show_info_message "checking for updates in onedep_admin"

cd $ONEDEP_PATH/onedep_admin
git checkout master
git pull

show_info_message "running RunUpdate.py step"

if [[ $OPT_DO_RUNUPDATE == true && $OPT_DO_BUILD_DEV == true ]]; then
    python $ONEDEP_PATH/onedep_admin/scripts/RunUpdate.py --config $ONEDEP_VERSION --build-tools --build-dev --build-version v-5200
elif [[ $OPT_DO_RUNUPDATE == true ]]; then
    python $ONEDEP_PATH/onedep_admin/scripts/RunUpdate.py --config $ONEDEP_VERSION --build-tools --build-version v-5200
else
    show_warning_message "skipping RunUpdate step"
fi

if [[ ! -z $SPECIFIC_PACKAGE ]]; then
  show_info_message "installing package $(highlight_text $SPECIFIC_PACKAGE)"
  pip install $SPECIFIC_PACKAGE
fi

pip list

# ----------------------------------------------------------------
# maintenance tasks
# according to the Confluence doc, there are many more maintenance tasks
# so keeping this to the minimum
# ----------------------------------------------------------------



if [[ $OPT_DO_MAINTENANCE == true ]]; then

    show_info_message "checking out / updating mmcif dictionary"

    python $ONEDEP_PATH/onedep-maintenance/common/update_mmcif_dictionary.py

    if [[ $? != 0 ]]; then show_error_message "step 'checking out / updating mmcif dictionary' failed with exit code $?"; fi

    show_info_message "checking out / updating taxonomy"

    python $ONEDEP_PATH/onedep-maintenance/common/update_taxonomy_files.py

    if [[ $? != 0 ]]; then show_error_message "step 'checking out / updating taxonomy' failed with exit code $?"; fi

    # to checkout/ update  the chemical component dictionary (CCD) and PRD - this step can take a while
    # using installed modules

    show_info_message "checking out / updating CCD and PRD"

    python -m wwpdb.apps.chem_ref_data.utils.ChemRefDataDbExec -v --checkout --db CC --load
    if [[ $? != 0 ]]; then show_error_message "step 'checking out CC' failed with exit code $?"; fi

    python -m wwpdb.apps.chem_ref_data.utils.ChemRefDataDbExec -v --checkout --db PRD --load
    if [[ $? != 0 ]]; then show_error_message "step 'checking out PRD' failed with exit code $?"; fi

    python -m wwpdb.apps.chem_ref_data.utils.ChemRefDataDbExec -v --update
    if [[ $? != 0 ]]; then show_error_message "step 'compiling CCD and PRD data files' failed with exit code $?"; fi

    # checkout / update sequences in OneDep - it now runs from anywhere using RunRemote
    # using https://github.com/wwPDB/onedep-maintenance/blob/master/common/Update-reference-sequences.sh

    show_info_message "checking out / updating sequences in OneDep"

    SCRIPT_PATH=${ONEDEP_PATH}/onedep-maintenance/common/sequence

    ${SCRIPT_PATH}/Fetch-db-unp.sh
    if [[ $? != 0 ]]; then show_error_message "script 'Fetch-db-unp.sh' in step 'checking out / updating sequences in OneDep' failed with exit code $?"; fi

    ${SCRIPT_PATH}/Fetch-db-gb.sh
    if [[ $? != 0 ]]; then show_error_message "script 'Fetch-db-gb.sh' in step 'checking out / updating sequences in OneDep' failed with exit code $?"; fi

    ${SCRIPT_PATH}/Format-db.sh
    if [[ $? != 0 ]]; then show_error_message "script 'Format-db.sh' in step 'checking out / updating sequences in OneDep' failed with exit code $?"; fi

    # get the taxonomy information for the depUI and load it into the OneDep database
    show_info_message "loading taxonomy information into OneDep db"
    python -m wwpdb.apps.deposit.depui.taxonomy.loadTaxonomyFromFTP.py --write_sql

    if [[ $? != 0 ]]; then show_error_message "step 'loading taxonomy information into OneDep db' failed with exit code $?"; fi
else
    show_warning_message "skipping maintenance tasks"
fi

# ----------------------------------------------------------------
# apache setup
# ----------------------------------------------------------------
if [[ $OPT_DO_APACHE == true ]]; then
    show_info_message "copying httpd.conf"

    cd $APACHE_PREFIX_DIR/conf
    mv httpd.conf httpd.conf.safe
    ln -s $SITE_CONFIG_DIR/apache_config/httpd.conf httpd.conf
else
  show_warning_message "skipping setting up the apache"
fi

#show_info_message "setting up csd"

#ln -s $ONEDEP_PATH/resources/csds/latest $ONEDEP_PATH/resources/csd

# ----------------------------------------------------------------
# service startup
# ----------------------------------------------------------------

#show_info_message "restarting wfe service"
#python $ONEDEP_PATH/onedep-maintenance/common/restart_services.py --restart_wfe   # aliased to restart_wfe

#show_info_message "restarting apache service"
#python $ONEDEP_PATH/onedep-maintenance/common/restart_services.py --restart_apache   # aliased to restart_apache

start_service_command="python $ONEDEP_PATH/onedep-maintenance/common/restart_services.py"
startWFE="$start_service_command --restart_wfe"
startApache="$start_service_command --restart_apache"

activate_onedep_command=". ${ONEDEP_PATH}/site-config/init/env.sh --siteid ${WWPDB_SITE_ID} --location ${WWPDB_SITE_LOC}"

show_info_message "done..."
echo "[*] activate OneDep: $(highlight_text $activate_onedep_command)"
echo "[*] wfm url: $(highlight_text http://$HOSTNAME/wfm)"
echo "[*] deposition url: $(highlight_text http://$HOSTNAME/deposition)"
echo "[*] start wfe service: $(highlight_text $startWFE)"
echo "[*] start apache service: $(highlight_text $startApache)"

