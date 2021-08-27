#!/usr/bin/env bash
(

# ----------------------------------------------------------------
# script variables
# ----------------------------------------------------------------

# internal
HOSTNAME=$(hostname)
PYTHON3="python3"
ONEDEP_BUILD_VER="v-5200"
THIS_SCRIPT="${BASH_SOURCE[0]}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CENTOS_MAJOR_VER=`cat /etc/redhat-release | cut -d' ' -f4  | cut -d'.' -f1`

# repositories
ONEDEP_BUILD_REPO_URL=git@github.com:wwPDB/onedep-build.git # scripts to build tools for OneDep
ONEDEP_ADMIN_REPO_URL=git@github.com:wwPDB/onedep_admin.git # OneDep package management
ONEDEP_MAINTENANCE_REPO_URL=git@github.com:wwPDB/onedep-maintenance.git # scripts to setup and maintain OneDep

# download links
MYSQL_COMMUNITY_SERVER=https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-boost-8.0.11.tar.gz

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

    retval=$filename
}

#
# get the value in site-config for the given variable key. I'm pretty sure
# there's a better way of doing this...
#   $1: variable key
function get_config_var {
    local key=$1
    local value=$(python -c "from wwpdb.utils.config.ConfigInfo import ConfigInfo; cI=ConfigInfo('$WWPDB_SITE_ID'); print(cI.get('$key'))")

    retval=$value
}

#
# function that downloads and builds mysql server
# in the current directory
function build_mysql {
    local DATABASE_DIR=$1

    show_info_message "downloading mysql server"

    download_file $MYSQL_COMMUNITY_SERVER
    local mysql_version=$retval

    show_info_message "building $mysql_version"

    mkdir -p mysql-source
    tar -xvf ./$mysql_version --directory mysql-source --strip-components=1

    cd mysql-source
    mkdir -p bld && cd bld

    export CC=/usr/bin/gcc
    export CXX=/usr/bin/g++

    cmake .. -DCMAKE_INSTALL_PREFIX=$DATABASE_DIR/mysql -DMYSQL_UNIX_ADDR=$DATABASE_DIR/mysql.sock -DWITH_BOOST=$DATABASE_DIR/mysql-source/boost
    make
    make install
}

#
# creates a "connection string" to be used to run commands in shell
#   $1: db identifier, MUST BE one of the following: 'status', 'depui', 'da_internal', 'compv4', 'prdv4'
#   $2: location of mysql binary
function create_mysql_conn_string {
    local db_key=$1
    local mysql_bin=$2
    local db_name_var="" # need this because for da_internal the format is different
    local var_prefix=""

    case $db_key in
        status) 
            db_name_var="SITE_DB_DATABASE_NAME"
            var_prefix="SITE"
        ;;
        depui) 
            db_name_var="SITE_DEP_DB_DATABASE_NAME"
            var_prefix="SITE_DEP"
        ;;
        da_internal) 
            db_name_var="SITE_DA_INTERNAL_DB_NAME"
            var_prefix="SITE_DA_INTERNAL"
        ;;
        compv4) 
            db_name_var="SITE_REFDATA_CC_DB_NAME"
            var_prefix="SITE_REFDATA"
        ;;
        prdv4) 
            db_name_var="SITE_REFDATA_PRD_DB_NAME"
            var_prefix="SITE_REFDATA"
        ;;
        *)
            # unknown option
            show_error_message "unknown database '$db_key', valid options are 'status', 'depui', 'da_internal', 'compv4', 'prdv4'"
            return -1
        ;;
    esac

    get_config_var ${db_name_var}; local db_name=$retval
    get_config_var ${var_prefix}_DB_HOST_NAME; local db_hostname=$retval
    get_config_var ${var_prefix}_DB_PORT_NUMBER; local db_port=$retval
    get_config_var ${var_prefix}_DB_USER_NAME; local db_user=$retval
    get_config_var ${var_prefix}_DB_PASSWORD; local db_password=$retval
    get_config_var ${var_prefix}_DB_SOCKET; local db_socket=$retval

    # here I'm assuming we'll be in the database setup folder
    retval="$mysql_bin -h$db_hostname -P$db_port -u$db_user -p$db_password --socket $db_socket"
}

#
# function to execute a given command in a mysql server
#   $1: mysql binary
#   $2: db identifier, MUST BE one of the following: 'status', 'depui', 'da_internal', 'compv4', 'prdv4'
#   $3: db command to be executed
function run_mysql_command {
    local mysql_bin=$1
    local db_key=$2
    local db_command=$3

    create_mysql_conn_string $db_key $mysql_bin; conn_string=$retval
    echo $(eval "$conn_string -e \"$db_command\"")
}

#
# function to execute a given command in a mysql server
#   $1: mysql binary
#   $2: db identifier, MUST BE one of the following: 'status', 'depui', 'da_internal', 'compv4', 'prdv4'
#   $3: sql script file to be passed to mysql
function run_mysql_script {
    local mysql_bin=$1
    local db_key=$2
    local script_file=$3

    create_mysql_conn_string $db_key $mysql_bin; conn_string=$retval
    echo $(eval "$conn_string < $script_file")
}

# checking if we're running as root

user=$(whoami)

if [[ $user == 'root' ]]; then
    show_warning_message "you should not run this script as root, run as a normal user instead and provide superuser credentials when asked"
    exit -1
fi

# ----------------------------------------------------------------
# arguments parsing
# ----------------------------------------------------------------

# arguments
ONEDEP_VERSION="latest"
SITE_ID="${WWPDB_SITE_ID}"
SITE_LOC="${WWPDB_SITE_LOC}"
MACHINE_ENV="production"
OPT_PREPARE_RUNTIME=false
OPT_PREPARE_BUILD=false
OPT_DO_COMPILE_SITE_CONFIG=false
OPT_DO_CLONE_DEPS=false
OPT_DO_BUILD=false
OPT_DO_RUNUPDATE=false
OPT_DO_MAINTENANCE=false
OPT_DO_APACHE=false
OPT_DO_RESTART_SERVICES=false
OPT_DO_HARD_RESET=false
OPT_VAL_SERVER_NUM_WORKERS="60"
OPT_DO_BUILD_DEV=false
OPT_DO_PULL_SINGULARITY=false

# database flags
OPT_DO_DATABASE=false
OPT_DB_ADD_DUMMY_CODES=false
OPT_DB_SKIP_BUILD=false
OPT_DB_RESET=false
DATABASE_DIR="default"

read -r -d '' USAGE << EOM
Usage: ${THIS_SCRIPT} [--full-install] [opts]

System preparation parameters:
    --full-install:             run all installation steps
    --install-runtime-base:     install packages ready for running onedep - not building tools (run as root)
    --install-build-base:       install packages ready for building tools (run as root)

OneDep installation parameters:
    --config-version:           OneDep config version, defaults to 'latest'
    --compile-site-config:      (re)compile site-config
    --clone-deps:               clone onedep_admin and onedep-maintenance
    --setup-venv:               setup onedep virtual environment
    --python3-path:             path to a Python interpreter, defaults to 'python3'
    --build-tools:              compile OneDep tools - OneDep requires compiled tools to be compiled or syncd from RCSB
    --install-onedep:           installs OneDep python packages
    --install-onedep-develop    installs OneDep python packages develop branches in edit mode
    --run-maintenance:          perform maintenance tasks as part of setup
    --setup-apache:             setup the apache
    --pull-singularity:         pull latest singularity image from GitLab
    --hard-reset:               clean up tools, deployment subtree for the provided site, sessions and site references
                                    this will be run before all steps

Database parameters:
    --setup-database:           setup database (installs server as non-root and setup tables)
    --database-dir:             directory where database will be setup, defaults to '$DEPLOY_DIR/onedep_database'
    --skip-db-build:            will skip downloading of mysql and building steps
    --reset-db:                 reset local database by removing files under 'data' dir
    --dummy-codes:              add PDB and EMDB dummy codes to database - useful for development installation

Post install parameters:
    --start-services:           start all onedep services (workflow engine, consumers, apache servers)
    --val-num-workers:          how many workers validation servers should have

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
        --database-dir)
            DATABASE_DIR="$2"
            shift
        ;;
        --val-num-workers)
            OPT_VAL_SERVER_NUM_WORKERS="$2"
            shift
        ;;
        --full-install)
            OPT_PREPARE_RUNTIME=true
            OPT_PREPARE_BUILD=true
            OPT_DO_BUILD=true
            OPT_DO_COMPILE_SITE_CONFIG=true
            OPT_DO_CLONE_DEPS=true
            OPT_DO_RUNUPDATE=true
            OPT_DO_BUILD_DEV=true
            OPT_DO_DATABASE=true
            OPT_DB_SKIP_BUILD=true
            OPT_DO_MAINTENANCE=true
            OPT_DO_APACHE=true
            OPT_DB_ADD_DUMMY_CODES=true
            OPT_DO_RESTART_SERVICES=true
        ;;
        --install-runtime-base) OPT_PREPARE_RUNTIME=true;;
        --install-build-base) OPT_PREPARE_BUILD=true;;

        --build-tools) OPT_DO_BUILD=true;;

        --compile-site-config) OPT_DO_COMPILE_SITE_CONFIG=true;;
        --clone-deps) OPT_DO_CLONE_DEPS=true;;
        --install-onedep) OPT_DO_RUNUPDATE=true;;
        --install-onedep-develop) OPT_DO_BUILD_DEV=true;;
        --hard-reset) OPT_DO_HARD_RESET=true;;
        --pull-singularity) OPT_DO_PULL_SINGULARITY=true;;

        --setup-database) OPT_DO_DATABASE=true;;
        --skip-db-build) OPT_DB_SKIP_BUILD=true;;
        --reset-db) OPT_DB_RESET=true;;

        --run-maintenance) OPT_DO_MAINTENANCE=true;;
        --setup-apache) OPT_DO_APACHE=true;;
        --dummy-codes) OPT_DB_ADD_DUMMY_CODES=true;;

        --restart-services) OPT_DO_RESTART_SERVICES=true;;

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

export SITE_CONFIG_DIR=$ONEDEP_PATH/site-config
export TOP_WWPDB_SITE_CONFIG_DIR=$ONEDEP_PATH/site-config

echo -e "----------------------------------------------------------------"
echo -e "[*] $(highlight_text WWPDB_SITE_ID) is set to $(highlight_text $WWPDB_SITE_ID)"
echo -e "[*] $(highlight_text WWPDB_SITE_LOC) is set to $(highlight_text $WWPDB_SITE_LOC)"
echo -e "[*] $(highlight_text ONEDEP_PATH) is set to $(highlight_text $ONEDEP_PATH)"
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

if [[ $OPT_DO_HARD_RESET == true ]]; then
    show_info_message "removing tools and site related data"
    
    show_warning_message "the following directories will be completely removed:\n \
    $TOOLS_DIR\n \
    $DEPLOY_DIR"

    read -p "[>] do you want to proceed (y/n)? " answer
    case ${answer:0:1} in
        y|Y )
            rm -rf $TOOLS_DIR
            rm -rf $DEPLOY_DIR
        ;;
        * )
            show_warning_message "clean up cancelled"
        ;;
    esac
fi

if [[ ( $OPT_PREPARE_RUNTIME == true || $OPT_DO_BUILD == true || $OPT_DO_RUNUPDATE == true || $OPT_PREPARE_BUILD == true ) && ! -d "onedep-build" ]]; then
    show_info_message "cloning onedep-build repository"
    git clone $ONEDEP_BUILD_REPO_URL
fi

if [[ $OPT_PREPARE_RUNTIME == true || $OPT_PREPARE_BUILD == true ]]; then
    show_info_message "installing required system packages"
    command=''

    if [[ $OPT_PREPARE_BUILD == true ]]; then
        show_info_message "installing system packages for compiling tools"
        command=onedep-build/install-base/centos-7-build-packages.sh
    else
        show_info_message "installing system packages for running OneDep"

        if [[ $CENTOS_MAJOR_VER == 8 ]]; then
          command=onedep-build/install-base/centos-8-host-packages.sh
        elif [[ $CENTOS_MAJOR_VER == 7 ]]; then
          command=onedep-build/install-base/centos-7-host-packages.sh
        else
          show_warning_message "unsupported OS version"
        fi
    fi

    show_warning_message "running command: $command"
    
    chmod +x $command
    sudo $command
else
    show_warning_message "skipping installation of required packages"
fi

# ----------------------------------------------------------------
# setting up directories used by onedep and python venv
# ----------------------------------------------------------------

if [[ $OPT_DO_COMPILE_SITE_CONFIG == true ]]; then
    show_info_message "setting up Python virtual env"

    # delete if it already exists
    if [[ -d "/tmp/venv" ]]; then
        rm -rf /tmp/venv
    fi

    unset PYTHONHOME
    $PYTHON3 -m venv /tmp/venv
    source /tmp/venv/bin/activate

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
fi

cd $ONEDEP_PATH

show_info_message "activating the new configuration"
# activate_configuration
. site-config/init/env.sh --siteid $WWPDB_SITE_ID --location $WWPDB_SITE_LOC

# now checking if DEPLOY_DIR has been set
show_info_message "checking if everything went ok..."

echo "[*] $(highlight_text DEPLOY_DIR) is set to $(highlight_text $DEPLOY_DIR)"
check_env_variable DEPLOY_DIR true

if [[ $OPT_DO_CLONE_DEPS == true ]]; then
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
fi

#show_info_message "creating 'resources' folder"
#mkdir -p $DEPLOY_DIR/resources

echo "[*] $(highlight_text TOOLS_DIR) is set to $(highlight_text $TOOLS_DIR)"
check_env_variable TOOLS_DIR true

# export some useful variables for building tools
export TOP_INSTALL_DIR=$TOOLS_DIR
export DISTRIB_DIR=$TOP_INSTALL_DIR/distrib
export DISTRIB_SOURCE=$TOP_INSTALL_DIR/distrib_source
export DISTRIB_SOURCE_DIR=$TOP_INSTALL_DIR/distrib_source
export BUILD_DIR=$TOP_INSTALL_DIR/build
export BUILD_PY_DIR=$TOP_INSTALL_DIR/build/python
export PREFIX=$TOP_INSTALL_DIR
export PACKAGE_DIR=$TOP_INSTALL_DIR/packages
export INSTALL_KERNEL=Linux

if [[ $OPT_DO_BUILD == true ]]; then
    show_info_message "now building, this may take a while"
    cd $ONEDEP_PATH/onedep-build/$ONEDEP_BUILD_VER/build-centos-$CENTOS_MAJOR_VER
    sudo ./BUILD.sh |& tee build.log
else
    show_warning_message "skipping build"
fi

# ----------------------------------------------------------------
# cloning OneDep admin pack
# ----------------------------------------------------------------
if [[ $OPT_DO_RUNUPDATE == true || $OPT_DO_BUILD_DEV == true || $OPT_DO_PULL_SINGULARITY == true ]]; then

  cd $ONEDEP_PATH
  if [[ ! -d "onedep_admin" ]]; then
    show_info_message "cloning OneDep admin repository"
    git clone $ONEDEP_ADMIN_REPO_URL
  fi
  show_info_message "checking for updates in onedep_admin"

  cd $ONEDEP_PATH/onedep_admin
  git checkout master
  git pull
  cd $ONEDEP_PATH
fi

# ----------------------------------------------------------------
# pull singularity image from GitLab
# ----------------------------------------------------------------

if [[ $OPT_DO_PULL_SINGULARITY == true ]]; then
  show_info_message "checking out singularity image from GitLab"
  show_info_message "checking for required username and access token (password)"
  check_env_variable SINGULARITY_DOCKER_USERNAME true
  check_env_variable  SINGULARITY_DOCKER_PASSWORD true

  SINGULARITY_PATH=$ONEDEP_PATH/singularity
  show_info_message "checking out singularity image into $(highlight_text $SINGULARITY_PATH)"


  SINGULARITY_BRANCH=feature-dbsetup
  SINGULARITY_FILE=onedep_admin_${SINGULARITY_BRANCH}.sif

  LATEST_VERSION=V1.0
  if [[ -f ${SINGULARITY_PATH}/current/${SINGULARITY_FILE} ]]; then
    LATEST_VERSION=$(singularity exec ${SINGULARITY_PATH}/current/${SINGULARITY_FILE} python $SCRIPT_DIR/RunUpdate.py --get-latest-version)
  fi

  show_info_message "OneDep version is $(highlight_text $LATEST_VERSION)"

  if [[ ! -d $SINGULARITY_PATH/$LATEST_VERSION ]]; then
    mkdir -p $SINGULARITY_PATH/$LATEST_VERSION
  fi

  show_info_message "pulling singularity image to $(highlight_text ${SINGULARITY_PATH}/$LATEST_VERSION)"

  cd ${SINGULARITY_PATH}/$LATEST_VERSION
  singularity pull --force docker://dockerhub.ebi.ac.uk/wwpdb/onedep_admin:${SINGULARITY_BRANCH}
  cd ${SINGULARITY_PATH}
  singularity pull --force docker://dockerhub.ebi.ac.uk/wwpdb/onedep_admin:${SINGULARITY_BRANCH}-develop
  singularity pull --force docker://dockerhub.ebi.ac.uk/wwpdb/onedep_admin:${SINGULARITY_BRANCH}-python-develop

  rm -f current
  ln -s $LATEST_VERSION current

  cd ${ONEDEP_PATH}

  show_info_message "updating tools"
  singularity exec ${SINGULARITY_PATH}/current/${SINGULARITY_FILE} python $SCRIPT_DIR/RunUpdate.py --build-tools --skip-pip --skip-resources --skip-webfe

fi

# ----------------------------------------------------------------
# setting up OneDep virtual env
# ----------------------------------------------------------------

if [[ $OPT_DO_RUNUPDATE == true || $OPT_DO_BUILD_DEV == true ]]; then
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

  show_info_message "setting up OneDep virtual environment in $(highlight_text $VENV_PATH) with $(highlight_text $PYTHON3)"

  $PYTHON3 -m venv $VENV_PATH
  source $VENV_PATH/bin/activate

  show_info_message "updating setuptools and pip"
  pip install --no-cache-dir --upgrade setuptools pip

  show_info_message "creating pip configuration file"

  get_config_var CS_HOST_BASE; cs_host_base=$retval
  get_config_var CS_USER; cs_user=$retval
  get_config_var CS_PW; cs_pw=$retval
  get_config_var CS_DISTRIB_URL; cs_distrib_url=$retval

  if [[ ! -z "$cs_host_base" && ! -z "$cs_user" && ! -z "$cs_pw" && ! -z "$cs_distrib_url" ]]; then
      pip config --site set global.trusted-host $cs_host_base
      pip config --site set global.extra-index-url "http://${cs_user}:${cs_pw}@${cs_distrib_url} https://pypi.anaconda.org/OpenEye/simple"
      pip config --site set global.no-cache-dir false
  else
      show_warning_message "some of the environment variables for the private RCSB Python repository are not set"
  fi

  show_info_message "install some base packages"
  pip install wheel

  pip install wwpdb.utils.config
  pip install ansible~=3.4

  show_info_message "running RunUpdate.py step"

  if [[ $OPT_DO_BUILD_DEV == true ]]; then
      python $ONEDEP_PATH/onedep_admin/scripts/RunUpdate.py --config $ONEDEP_VERSION --build-tools --build-dev
  else
      python $ONEDEP_PATH/onedep_admin/scripts/RunUpdate.py --config $ONEDEP_VERSION --build-tools
  fi
  pip list
else
    show_warning_message "skipping RunUpdate step"
fi

# ----------------------------------------------------------------
# database setup
# ----------------------------------------------------------------

if [[ $OPT_DO_DATABASE == true ]]; then
    show_info_message "setting up database"

    if [[ $DATABASE_DIR == "default" ]]; then
        DATABASE_DIR=$DEPLOY_DIR/onedep_database
    fi

    echo "[*] database will be installed in $(highlight_text $DATABASE_DIR)"

    if [[ ! -d $DATABASE_DIR ]]; then
        mkdir -p $DATABASE_DIR
    fi

    if [[ $OPT_DB_RESET == true ]]; then
        show_info_message "resetting local database"

        rm -rf $DATABASE_DIR/data
        echo > $DATABASE_DIR/log.err
    fi

    # site-config variables
    get_config_var SITE_DB_USER; db_user=$retval
    get_config_var SITE_DB_PASSWORD; db_password=$retval
    get_config_var SITE_DB_PORT_NUMBER; db_port=$retval

    cd $DATABASE_DIR

    mkdir -p data
    mkdir -p mysql

    if [[ $OPT_DB_SKIP_BUILD == false ]]; then
        build_mysql $DATABASE_DIR 
    fi

    cd $DATABASE_DIR

    # killing all mysqld instances initiated by this user
    killall mysqld

    ls -A $DATABASE_DIR/data
    if [[ ! "$(ls -A $DATABASE_DIR/data)" ]]; then
        show_info_message "initializing mysql server"
        ./mysql/bin/mysqld --user=w3_pdb05 --basedir=$DATABASE_DIR/mysql --datadir=$DATABASE_DIR/data --socket=$DATABASE_DIR/mysql.sock --log-error=$DATABASE_DIR/log --pid-file=$DATABASE_DIR/mysql.pid --port=$db_port --initialize
    fi

    show_info_message "starting mysql server, please wait"
    ./mysql/bin/mysqld --user=$USER --bind-address=0.0.0.0 --basedir=$DATABASE_DIR/mysql --datadir=$DATABASE_DIR/data --socket=$DATABASE_DIR/mysql.sock --log-error=$DATABASE_DIR/log --pid-file=$DATABASE_DIR/mysql.pid --port=$db_port &
    mysql_pid=$!

    while :; do
        mysql_state=$(ps -q $mysql_pid -o state --no-headers)

        if [[ -z $mysql_state ]]; then
            show_error_message "mysql process killed, skipping database setup"
            exit 1
            break
        fi

        mysql_log=$(tail log.err)

        if [[ $mysql_log == *"ready for connections"* ]]; then
            show_info_message "mysql server up and accepting connections"
            sleep 3
            break
        fi

        sleep 3
    done

    mysql_log=$(cat log.err)
    regex=$'A temporary password is generated for .*'

    if [[ $mysql_log =~ $regex ]]; then
        temp_db_root_password=$(echo ${BASH_REMATCH[0]} | cut -d" " -f8)
    else
        show_error_message "could not find temporary root password in log, exiting"
        exit 1
    fi

    get_config_var SITE_ADMIN_DB_PASSWORD_ROOT; new_db_root_password=$retval
    # new_db_root_password=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9!@#$%&*+<>?=-' | fold -w 32 | head -n 1)

    echo "[*] mysql temporary root password is $(highlight_text $temp_db_root_password)"
    echo "[*] setting mysql root password to $(highlight_text $new_db_root_password)"

    # not using the run_mysql_command here as this requires additional flags
    ./mysql/bin/mysql -P$db_port -uroot -p$temp_db_root_password --socket $DATABASE_DIR/mysql.sock --connect-expired-password -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '$new_db_root_password'"
    #./mysql/bin/mysql -P$db_port -uroot -p$new_db_root_password --socket $DATABASE_DIR/mysql.sock -e "CREATE USER '$db_user'@'%' IDENTIFIED WITH mysql_native_password BY '$db_password'"
    # should we restrict permissions for this user?
    #./mysql/bin/mysql -P$db_port -uroot -p$new_db_root_password --socket $DATABASE_DIR/mysql.sock -e "GRANT ALL ON *.* TO '$db_user'@'%' WITH GRANT OPTION"

    show_info_message "creating schemas"
    python -m wwpdb.apps.site_admin.DbAdminExec --create-schemas

    # dummy codes

    if [[ $OPT_DB_ADD_DUMMY_CODES == true ]]; then
        show_info_message "adding PDB and EMDB dummy codes to db"
        
        # codes
        seq -w 4 0001 0050 > dummy_pdb_codes.ids
        seq --format EMD-%04g 1 50 > dummy_emdb_codes.ids

        python -m wwpdb.apps.wf_engine.wf_engine_utils.tasks.WFTaskRequestExec --load_accessions_pdb --accession_file dummy_pdb_codes.ids
        python -m wwpdb.apps.wf_engine.wf_engine_utils.tasks.WFTaskRequestExec --load_accessions_emdb --accession_file dummy_emdb_codes.ids

        rm dummy_pdb_codes.ids
        rm dummy_emdb_codes.ids
    fi

    # shutdown
    # ./bin/mysqladmin --socket=./socket shutdown -uroot -p$new_db_root_password
    # ./onedep_admin/scripts/install_onedep.sh --setup-database --database-dir /nfs/public/release/msd/services/onedep/db
fi

# ----------------------------------------------------------------
# maintenance tasks
# according to the Confluence doc, there are many more maintenance tasks
# so keeping this to the minimum
# ----------------------------------------------------------------

if [[ $OPT_DO_MAINTENANCE == true ]]; then

  if [[ $OPT_DO_MAINTENANCE == true && ! -d "onedep-maintenance" ]]; then
    show_info_message "cloning OneDep maintenance repository"
    git clone $ONEDEP_MAINTENANCE_REPO_URL
  fi

    show_info_message "Running setup maintenance"
    python -m wwpdb.apps.site_admin.RunSetupMaintenance

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
    mkdir $APACHE_PREFIX_DIR/conf.d
    ln -s $SITE_CONFIG_DIR/apache_config/development.conf $APACHE_PREFIX_DIR/conf.d
else
  show_warning_message "skipping setting up the apache"
fi

#show_info_message "setting up csd"

#ln -s $ONEDEP_PATH/resources/csds/latest $ONEDEP_PATH/resources/csd

# ----------------------------------------------------------------
# restart services
# ----------------------------------------------------------------

if [[ $OPT_DO_RESTART_SERVICES == true ]]; then
    show_info_message "restarting all services"

    # restart_all_apaches
    python $ONEDEP_PATH/onedep_admin/scripts/RestartServices.py --restart_apache
    # restart_workflow_engines
    python $ONEDEP_PATH/onedep_admin/scripts/RestartServices.py --restart_wfe
    # val_api_consumer_restart
    python $ONEDEP_PATH/onedep_admin/scripts/RestartServices.py --restart_val_api_consumers $OPT_VAL_SERVER_NUM_WORKERS
    # val_rel_consumer_restart, should we have this as well?
    python $ONEDEP_PATH/onedep_admin/scripts/RestartServices.py --restart_val_rel_consumers $OPT_VAL_SERVER_NUM_WORKERS
fi

# ----------------------------------------------------------------
# service startup
# ----------------------------------------------------------------

start_service_command="python $ONEDEP_PATH/onedep_admin/scripts/restart_services.py"
startWFE="$start_service_command --restart_wfe"
startApache="$start_service_command --restart_apache"

activate_onedep_command=". ${ONEDEP_PATH}/site-config/init/env.sh --siteid ${WWPDB_SITE_ID} --location ${WWPDB_SITE_LOC}"

show_info_message "done..."
echo "[*] activate OneDep: $(highlight_text $activate_onedep_command)"
echo "[*] wfm url: $(highlight_text http://$HOSTNAME/wfm)"
echo "[*] deposition url: $(highlight_text http://$HOSTNAME/deposition)"
echo "[*] start wfe service: $(highlight_text $startWFE)"
echo "[*] start apache service: $(highlight_text $startApache)"

) |& tee install_script.log

# cleaning up log file
sed -i 's,\x1b\[[0-9]*m,,g' install_script.log
