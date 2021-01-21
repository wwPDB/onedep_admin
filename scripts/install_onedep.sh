#!/usr/bin/env bash

# ----------------------------------------------------------------
# script variables
# ----------------------------------------------------------------

# internal
PYTHON2="python2"
THIS_SCRIPT="${BASH_SOURCE[0]}"

# repositories
SITE_CONFIG_REPO_URL=https://gitlab.ebi.ac.uk/pdbe/onedep-site-config.git
# SITE_CONFIG_REPO_URL=git@gitlab.ebi.ac.uk:pdbe/onedep-site-config.git
ONEDEP_BUILD_REPO_URL=git@github.com:wwPDB/onedep-build.git # scripts to build tools for OneDep
ONEDEP_ADMIN_REPO_URL=git@github.com:wwPDB/onedep_admin.git # OneDep package management
ONEDEP_MAINTENANCE_REPO_URL=git@github.com:wwPDB/onedep-maintenance.git # scripts to setup and maintain OneDep
WWPDB_UTILS_CONFIG=git@github.com:wwPDB/py-wwpdb_utils_config.git

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
SITE_LOC="${LOC_ID}"
MACHINE_ENV="development"
OPT_COMPILE_TOOLS=true
OPT_SKIP_INSTALL=false
OPT_SKIP_BUILD=false

USAGE="Usage: ${THIS_SCRIPT} [--version] [--no-compile] [--skip-install] [--skip-build] [-e|--env] "

while [[ $# > 0 ]]
do
    key="$1"
    case $key in
        -e|--env)
        if [[ "$2" != "development" && "$2" != "production" ]]; then
            show_error_message "env option must be set to either 'development' or 'production'"
            exit
        fi
        MACHINE_ENV="$2"
        shift # past argument
        ;;
        # optional args
        --version) ONEDEP_VERSION="$2";;
        --no-compile) OPT_COMPILE_TOOLS=false;;
        --skip-install) OPT_SKIP_INSTALL=true;;
        --skip-build) OPT_SKIP_BUILD=true;;
        --help)
            echo ${USAGE}
            OK=1
        ;;
        *)
            # unknown option
            echo ${USAGE}
            OK=1
        ;;
    esac
    shift # past argument or value
done

# ----------------------------------------------------------------
# checking required environment settings
# some new ones are created based on them (e.g. ONEDEP_PATH)
# ----------------------------------------------------------------

check_env_variable WWPDB_SITE_ID true
check_env_variable LOC_ID true
check_env_variable ONEDEP_PATH true
check_env_variable DA_TOP true

echo -e "----------------------------------------------------------------"
echo -e "[*] $(highlight_text WWPDB_SITE_ID) is set to $(highlight_text $WWPDB_SITE_ID)"
echo -e "[*] $(highlight_text LOC_ID) is set to $(highlight_text $LOC_ID)"
echo -e "[*] $(highlight_text ONEDEP_PATH) is set to $(highlight_text $ONEDEP_PATH)"
echo -e "[*] $(highlight_text DA_TOP) is set to $(highlight_text $DA_TOP)"

export SITE_CONFIG_DIR=$ONEDEP_PATH/site-config
export TOP_WWPDB_SITE_CONFIG_DIR=$ONEDEP_PATH/site-config

echo -e "[*] $(highlight_text SITE_CONFIG_DIR) is set to $(highlight_text $SITE_CONFIG_DIR)"
echo -e "[*] $(highlight_text TOP_WWPDB_SITE_CONFIG_DIR) is set to $(highlight_text $TOP_WWPDB_SITE_CONFIG_DIR)"
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

if [[ "$OPT_SKIP_INSTALL" == false ]]; then
    show_info_message "installing required packages"

    # installing python3
    sudo yum -y install python3

    # we clone the onedep-build to have access to the installation
    # scripts
    git clone ONEDEP_BUILD_REPO_URL

    if [[ $OPT_COMPILE_TOOLS ]]; then
        show_info_message "installing packages and compiling tools"
        sudo onedep-build/install-base/centos-7-build-packages.sh
    else
        show_info_message "installing packages without compiling"
        sudo onedep-build/install-base/centos-7-host-packages.sh
    fi
else
    show_warning_message "skipping installation of required packages"
fi

# ----------------------------------------------------------------
# setting up directories used by onedep and python venv
# ----------------------------------------------------------------

# checking if there is already a site-config folder
# assuming ssh keys exist and are added to the ssh-agent
# I think we could a better check here
if [[ ! -d "$ONEDEP_PATH/site-config" ]]; then
    show_warning_message "site-config does not seem to exist, cloning from gitlab..."
    git clone $SITE_CONFIG_REPO_URL site-config
else
    cd $ONEDEP_PATH/site-config
    git pull
    cd $ONEDEP_PATH
fi

show_info_message "setting up Python virtual env"

# delete if it already exists
if [[ -d "/tmp/venv" ]]; then
    rm -rf /tmp/venv
    unset PYTHONHOME
    python3 -m venv /tmp/venv
fi

# ----------------------------------------------------------------
# setting up directories used by onedep and python venv
# ----------------------------------------------------------------

source /tmp/venv/bin/activate

show_info_message "updating setuptools"
pip install --upgrade setuptools==40.8.0 pip

show_info_message "installing wwpdb.utils.config"
pip install wwpdb.utils.config
 
show_info_message "compiling site-config for the new site"
ConfigInfoFileExec --siteid $WWPDB_SITE_ID --locid $LOC_ID --writecache

deactivate

cd $ONEDEP_PATH

show_info_message "activating the new configuration"

# 
# there was a problem here. If I called 'env.sh', the env variables
# set by ConfigInfoShellExec.py would be set only in the subshell hosting
# 'env.sh'. The solution for now is to directly execute the same commands
# from 'env.sh'
#
# before: $SITE_CONFIG_DIR/init/env.sh --siteid $WWPDB_SITE_ID --location $LOC_ID
# ----------------------------------------------------------------
COMMAND="${PYTHON2} -E $SITE_CONFIG_DIR/init/ConfigInfoShellExec.py -v --configpath=${TOP_WWPDB_SITE_CONFIG_DIR} --locid=${SITE_LOC} --siteid=${SITE_ID} --shell"
echo $COMMAND

while IFS= read -r line; do
    eval $line
done <<< "$($COMMAND)"
# ----------------------------------------------------------------

# now checking if DEPLOY_DIR has been set
show_info_message "checking if everything went ok..."

echo "[*] $(highlight_text DEPLOY_DIR) is set to $(highlight_text $DEPLOY_DIR)"
check_env_variable DEPLOY_DIR true

show_info_message "cloning onedep repositories"

if [[ ! -d "onedep-build" ]]; then
    git clone $ONEDEP_BUILD_REPO_URL
fi

if [[ ! -d "onedep_admin" ]]; then
    git clone $ONEDEP_ADMIN_REPO_URL
    
    cd onedep_admin

    git checkout master
    git pull

    cd ..
fi

if [[ ! -d "onedep-maintenance" ]]; then
    git clone $ONEDEP_MAINTENANCE_REPO_URL
fi

show_info_message "creating 'resources' folder"

mkdir -p $DEPLOY_DIR/resources

if [[ ! $OPT_SKIP_BUILD ]]; then
    show_info_message "now building, this may take a while"

    cd $ONEDEP_PATH/onedep-build/v-5200/build-centos-7 # maybe I should put the build version in a variable
    ./BUILD.sh |& tee build.log
else
    show_warning_message "skipping build"
fi

# ----------------------------------------------------------------
# setting up onedep virtual env
# ----------------------------------------------------------------

show_info_message "setting up onedep virtual environment"

cd $ONEDEP_PATH
unset PYTHONHOME

if [[ -z "$VENV_PATH" ]]; then
    # we need to run the commands from env.sh
    # maybe I should put this inside a function
    COMMAND="${PYTHON2} -E $SITE_CONFIG_DIR/init/ConfigInfoShellExec.py -v --configpath=${TOP_WWPDB_SITE_CONFIG_DIR} --locid=${SITE_LOC} --siteid=${SITE_ID} --shell"
    echo $COMMAND

    while IFS= read -r line; do
        eval $line
    done <<< "$($COMMAND)"
fi

python3 -m venv $VENV_PATH

show_info_message "checking for updates in onedep_admin"

cd $ONEDEP_PATH/onedep_admin
git checkout master
git pull

show_info_message "running RunUpdate.py step"

pip install wwpdb.utils.config
python $ONEDEP_PATH/onedep_admin/scripts/RunUpdate.py --config $ONEDEP_PATH/onedep_admin/V5.3/V53rel.conf --build-tools --build-version v-5200
pip list

# ----------------------------------------------------------------
# maintenance tasks
# according to the Confluence doc, there are many more maintenance tasks
# so keeping this to the minimum
# ----------------------------------------------------------------

# to checkout / update the mmCIF dictionary
# using https://github.com/wwPDB/onedep-maintenance/blob/master/common/update_mmcif_dictionary.sh

show_info_message "checking out / updating mmcif dictionary"

if [[ ! -d $SITE_PDBX_DICT_PATH ]]; then
    mkdir -p $SITE_PDBX_DICT_PATH
    cd $SITE_PDBX_DICT_PATH
    svn co --no-auth-cache --username $SVN_USER --password $SVN_PASS https://svn-dev.wwpdb.org/svn-test/data-dictionary/trunk .
else
    cd $SITE_PDBX_DICT_PATH
    svn up --no-auth-cache --username $SVN_USER --password $SVN_PASS
fi

if [[ $? != 0 ]]; then show_error_message "step 'checking out / updating mmcif dictionary' failed with exit code $?"; fi

# to checkout / update the taxonomy for annotation
# using https://github.com/wwPDB/onedep-maintenance/blob/master/common/update_taxonomy.sh

show_info_message "checking out / updating taxonomy"

if [[ ! -d $SITE_TAXDUMP_PATH ]]; then
    mkdir -p $SITE_TAXDUMP_PATH
    cd $SITE_TAXDUMP_PATH
    svn co --username $SVN_USER --password $SVN_PASS https://svn-dev.wwpdb.org/svn-test/data-taxdump/trunk .
else
    cd $SITE_TAXDUMP_PATH
    svn up --username $SVN_USER --password $SVN_PASS
fi

if [[ $? != 0 ]]; then show_error_message "step 'checking out / updating taxonomy' failed with exit code $?"; fi

# to checkout/ update  the chemical component dictionary (CCD) and PRD - this step can take a while
# using installed modules

show_info_message "checking out / updating CCD and PRD"

python -m wwpdb.apps.chem_ref_data.utils.ChemRefDataDbExec -v --checkout --db CC --load
if [[ $? != 0 ]]; then show_error_message "step 'checking out CC' failed with exit code $?"; fi

python -m wwpdb.apps.chem_ref_data.utils.ChemRefDataDbExec -v --checkout --db PRD --load
if [[ $? != 0 ]]; then show_error_message "step 'checking out PRD' failed with exit code $?"; fi

python -m wwpdb.apps.chem_ref_data.utils.ChemRefDataDbExec -v --update
if [[ $? != 0 ]]; then show_error_message "step 'updating taxonomy' failed with exit code $?"; fi

# checkout / update sequences in OneDep - it now runs from anywhere using RunRemote
# using https://github.com/wwPDB/onedep-maintenance/blob/master/common/Update-reference-sequences.sh

show_info_message "checking out / updating sequences in OneDep"

SCRIPT_PATH="${BASH_SOURCE[0]}";
if ([ -h "${SCRIPT_PATH}" ]);
then
  while([ -h "${SCRIPT_PATH}" ]);
   do SCRIPT_PATH=`readlink "${SCRIPT_PATH}"`;
   done
fi

pushd . > /dev/null
cd `dirname ${SCRIPT_PATH}` > /dev/null
SCRIPT_PATH=`pwd`;

${SCRIPT_PATH}/sequence/Fetch-db-unp.sh
if [[ $? != 0 ]]; then show_error_message "script 'Fetch-db-unp.sh' in step 'checking out / updating sequences in OneDep' failed with exit code $?"; fi

${SCRIPT_PATH}/sequence/Fetch-db-gb.sh
if [[ $? != 0 ]]; then show_error_message "script 'Fetch-db-gb.sh' in step 'checking out / updating sequences in OneDep' failed with exit code $?"; fi

${SCRIPT_PATH}/sequence/Format-db.sh
if [[ $? != 0 ]]; then show_error_message "script 'Format-db.sh' in step 'checking out / updating sequences in OneDep' failed with exit code $?"; fi

# get the taxonomy information for the depUI and load it into the OneDep database

# PDBe specific instruction -
# python -m wwpdb.apps.deposit.depui.taxonomy.getData

# sync taxonomy data from PDBe...
show_info_message "loading taxonomy information into OneDep db"
python -m wwpdb.apps.deposit.depui.taxonomy.loadData
if [[ $? != 0 ]]; then show_error_message "step 'loading taxonomy information into OneDep db' failed with exit code $?"; fi

# ----------------------------------------------------------------
# apache setup
# ----------------------------------------------------------------
