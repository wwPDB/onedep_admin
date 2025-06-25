#!/usr/bin/env python

"""Utility to help run/manage update of OneDep system"""

try:
    from ConfigParser import ConfigParser, NoOptionError
except ImportError:
    from configparser import ConfigParser, NoOptionError
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
import argparse
import fnmatch
import json
import os
import os.path
import subprocess
import sys
import warnings
import importlib.metadata
from enum import Enum, Flag, auto
import xml.etree.ElementTree as ET

from wwpdb.utils.config.ConfigInfo import ConfigInfo
from wwpdb.utils.config.ConfigInfoApp import ConfigInfoAppCommon, ConfigInfoAppValidation


class DbSchemaManager(object):
    def __init__(self, noop):
        self.__noop = noop
        curdir = os.path.dirname(__file__)
        self.__checkscript = os.path.join(curdir, 'ManageDB.py')

    def __exec(self, cmd):
        print(cmd)
        ret = 0
        ret = subprocess.call(cmd, shell=True)
        return ret

    def updateschema(self):
        if self.__noop:
            command = 'python {} --noop update'.format(self.__checkscript)
        else:
            command = 'python {} update'.format(self.__checkscript)
        self.__exec(command)

    def checkviews(self):
        if self.__noop:
            command = 'python {} --noop finalcheck'.format(self.__checkscript)
        else:
            command = 'python {} finalcheck'.format(self.__checkscript)
        self.__exec(command)


class UpdateManager(object):
    def __init__(self, config_file, noop):
        self.__configfile = config_file
        self.__noop = noop
        self.__ci = ConfigInfo()
        self.__ci_common = ConfigInfoAppCommon()
        self.__ci_val = ConfigInfoAppValidation()

        self.__extraconf = self.get_variable("ADMIN_EXTRA_CONF", environment='INSTALL_ENVIRONMENT')
        self.__confvars = {}
        self.__extraconfdir = None

        if self.__extraconf is not None:
            self.__extraconfdir = os.path.abspath(os.path.dirname(self.__extraconf))
            self.__confvars["extraconfdir"] = self.__extraconfdir

        # Infer topdir from where running from
        topdir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        cdict = {'topdir': topdir}

        self.__cparser = ConfigParser(cdict)

        cfiles = self.__configfile
        if self.__extraconf is not None:
            cfiles = [self.__configfile, self.__extraconf]
        self.__cparser.read(cfiles)

        self.__web_apps_path = self.get_variable('TOP_WWPDB_WEBAPPS_DIR')
        self.__resources_ro_path = self.get_variable('RO_RESOURCE_PATH')
        self.__resources_rw_path = self.get_variable('RW_RESOURCE_PATH')

    def __exec(self, cmd, overridenoop=False, working_directory=None):
        print(cmd)
        ret = 0
        if not self.__noop or overridenoop:
            if working_directory:
                print('Working Directory= {}'.format(working_directory))
                original_wd = os.getcwd()
                os.chdir(working_directory)
                ret = subprocess.call(cmd, shell=True)
                os.chdir(original_wd)
            else:
                ret = subprocess.call(cmd, shell=True)
        return ret

    def get_variable(self, variable, environment=None):
        ret = None
        if environment:
            ret = self.__ci.get(environment, {}).get(variable)
        if not ret:
            ret = self.__ci.get(variable)
        if not ret:
            ret = os.getenv(variable)
        return ret

    def updatepyenv(self, dev_build, use_https_git=False):
        cs_user = self.get_variable('CS_USER', environment='INSTALL_ENVIRONMENT')
        cs_pass = self.get_variable('CS_PW', environment='INSTALL_ENVIRONMENT')
        cs_url = self.get_variable('CS_URL', environment='INSTALL_ENVIRONMENT')

        git_prefix = "https://" if use_https_git else "git@"
        script_dir = os.path.dirname(os.path.realpath(__file__))
        constraintfile = os.path.abspath(os.path.join(script_dir, '../base_packages/constraints.txt'))

        urlreq = urlparse(cs_url)
        urlpath = "{}://{}:{}@{}{}/dist/simple/".format(urlreq.scheme, cs_user, cs_pass, urlreq.netloc, urlreq.path)
        # pip_extra_urls = "--extra-index-url {} --trusted-host {} --extra-index-url https://pypi.anaconda.org/OpenEye/simple ".format(
        #                   urlpath, urlreq.netloc)

        self.__exec("pip config --site set global.trusted-host {}".format(urlreq.netloc))
        self.__exec('pip config --site set global.extra-index-url "{} https://pypi.anaconda.org/OpenEye/simple"'.format(urlpath))
        self.__exec("pip config --site set global.no-cache-dir false")

        pip_extra_urls = '-c {}'.format(constraintfile)

        # pip installing from requirements.txt in base_packages

        reqfile = os.path.abspath(os.path.join(script_dir, '../base_packages/pre-requirements.txt'))

        # PKG_CONFIG_PATH is needed for future pip in which global-options will not be supported
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            babel_pkg_path = os.path.join(self.__ci_common.get_site_packages_path(), "openbabel", "lib", "pkgconfig")
            base_pkg_path = os.path.join(self.__ci.get("TOOLS_PATH"), "lib", "pkgconfig")

            if base_pkg_path:
                cpaths = "%s:%s" % (base_pkg_path, babel_pkg_path)
            else:
                cpaths = babel_pkg_path

        command = 'PKG_CONFIG_PATH={} pip install {} -r {}'.format(cpaths, pip_extra_urls, reqfile)
        self.__exec(command)

        reqfile = os.path.abspath(os.path.join(script_dir, '../base_packages/requirements.txt'))
        command = 'PKG_CONFIG_PATH={} pip install {} -r {}'.format(cpaths, pip_extra_urls, reqfile)
        self.__exec(command)

        if self.__cparser.has_option('DEFAULT', 'pip_extra_reqs'):
            opt_req = self.__cparser.get('DEFAULT', 'pip_extra_reqs', vars=self.__confvars)
        else:
            opt_req = None

        reqfile = self.__cparser.get('DEFAULT', 'piprequirements')
        if dev_build:
            # Clone and do pip edit install

            # Checking if source directory exist
            source_dir = os.path.abspath(os.path.join(self.__web_apps_path, '../..'))
            if not os.path.isdir(source_dir):
                os.makedirs(source_dir)

            path_to_list_of_repo = os.path.abspath(
                os.path.join(script_dir, '../base_packages/requirements_wwpdb_dependencies.txt'))
            with open(path_to_list_of_repo) as list_of_repo:
                for repo in list_of_repo:
                    command = 'git clone --recursive {0}github.com:wwPDB/{1}.git; cd {1}; git checkout develop; cd ..'.format(
                        git_prefix,
                        repo.rstrip())
                    self.__exec(command, working_directory=source_dir)
                    command = 'pip install {} --edit {}'.format(pip_extra_urls, repo)
                    self.__exec(command, working_directory=source_dir)
        else:
            command = 'pip install -U {} -r {}'.format(pip_extra_urls, reqfile)
            self.__exec(command)

        if opt_req:
            command = 'export CS_USER={}; export CS_PW={}; export CS_URL={}; export URL_NETLOC={}; export URL_PATH={}; pip install -U {} -r {}'.format(
                cs_user, cs_pass, cs_url, urlreq.netloc, urlreq.path, pip_extra_urls, opt_req)
            self.__exec(command)

    def removepyenv(self):
        if not self.__cparser.has_option('DEFAULT', 'removepy'):
            return
        pkgs = self.__cparser.get('DEFAULT', 'removepy').split(" ")
        for p in pkgs:
            if len(p) == 0:
                # extra spaces in config
                continue
            # Package has a version number
            pname, vers = p.split("=")
            try:
                vinst = importlib.metadata.version(pname)
            except importlib.metadata.PackageNotFoundError:
                continue

            if vers != vinst:
                continue

            cmd = "pip uninstall -y {}".format(pname)
            self.__exec(cmd)

    def updateresources(self, use_https_git=False):
        git_prefix = "https://" if use_https_git else "git@"

        restag = self.__cparser.get('DEFAULT', 'resourcestag')
        if self.__resources_ro_path:
            if not os.path.exists(self.__resources_ro_path):
                command = 'git clone {}github.com:wwPDB/onedep-resources_ro.git {}'.format(git_prefix, self.__resources_ro_path)
                self.__exec(command)

            command = 'cd {}; git pull; git checkout master; git pull; git checkout {}; git pull origin {}'.format(
                self.__resources_ro_path, restag, restag)
            self.__exec(command)

        if self.__resources_rw_path:
            if not os.path.exists(self.__resources_rw_path):
                command = 'git clone {}github.com:wwPDB/onedep-resources_rw.git {}'.format(git_prefix, self.__resources_rw_path)
                self.__exec(command)

            command = 'cd {}; git pull'.format(self.__resources_rw_path)
            self.__exec(command)

    def checkwebfe(self, overridenoop=False):
        webdir = os.path.abspath(os.path.join(self.__web_apps_path, '..'))
        curdir = os.path.dirname(__file__)
        checkscript = os.path.join(curdir, 'ManageWebFE.py')
        webfecheck = self.__cparser.get('DEFAULT', 'webfeconf')

        command = 'python {} --webroot {} check -r {}'.format(checkscript, webdir, webfecheck)
        ret = self.__exec(command, overridenoop=overridenoop)
        if ret:
            print("ERROR: check of webfe directory failed")

    def updatewebfe(self, use_https_git=False):
        git_prefix = "https://" if use_https_git else "git@"

        # Checking if source directory exist
        source_dir = os.path.abspath(os.path.join(self.__web_apps_path, '../..'))
        if not os.path.isdir(source_dir):
            os.makedirs(source_dir)

        # Check if repo is cloned
        webfe_repo = os.path.abspath(os.path.join(self.__web_apps_path, '..'))
        if not os.path.isdir(webfe_repo):
            command = f'git clone --recurse-submodules {git_prefix}github.com:wwPDB/onedep-webfe.git'
            self.__exec(command, working_directory=source_dir)
            self.checkwebfe()

        webfetag = self.__cparser.get('DEFAULT', 'webfetag')

        command = 'cd {}; git pull; git checkout {}; git pull origin {}; git submodule init; git submodule update'.format(
            webfe_repo, webfetag, webfetag)
        self.__exec(command)

        # Now check the results
        self.checkwebfe()

    def updatetaxdb(self):
        # Checks the number of rows in db and decides if to update
        taxdbsize = int(self.__cparser.get('DEFAULT', 'taxdbminsize'))
        if self.__cparser.has_option('DEFAULT', 'taxdbmaxsize'):
            maxsize = int(self.__cparser.get('DEFAULT', 'taxdbmaxsize'))
        else:
            maxsize = 999999999

        taxuseftp = self.__cparser.has_option('DEFAULT', 'taxuseftp')
        if not taxuseftp:
            taxresource = self.get_variable('TAXONOMY_FILE_NAME')

            if not taxresource:
                print("ERROR: TAXONOMY_FILE_NAME is not set in site-config")
                return

        curdir = os.path.dirname(__file__)
        checkscript = os.path.join(curdir, 'ManageTaxDB.py')

        if taxuseftp:
            addftp=" --ftpload"
        else:
            addftp=""

        if self.__noop:
            command = 'python {} --noop --maxsize {} --taxdbsize {}{}'.format(checkscript, maxsize, taxdbsize, addftp)
        else:
            command = 'python {} --maxsize {} --taxdbsize {}{}'.format(checkscript, maxsize, taxdbsize, addftp)
        self.__exec(command)

    def updateschema(self):
        dbs = DbSchemaManager(self.__noop)
        dbs.updateschema()

    def postflightdbcheck(self):
        dbs = DbSchemaManager(self.__noop)
        dbs.checkviews()



    def checktoolvers(self):

        class OptFlags(Flag):
            APP_VALIDATION = auto()

        class VersionEnum(Enum):
            PARSE_PHENIX_ENV = auto()
            PARSE_CCP4_VER = auto()

        #  vers_config_var,  configinfovar,             relative path            ConfiginfoAppMethod            VersionMethod   Flags
        confs = [['annotver', 'SITE_ANNOT_TOOLS_PATH', 'etc/bundleversion.json', 'get_site_annot_tools_path', None, None],
                 ['webfever', 'TOP_WWPDB_WEBAPPS_DIR', 'version.json', '', None, None],
                 ['resourcever', 'RO_RESOURCE_PATH', 'version.json', '', None, None],
                 ['cctoolsver', 'SITE_CC_APPS_PATH', 'etc/bundleversion.json', 'get_site_cc_apps_path', None, None],
                 ['sfvalidver', 'SITE_PACKAGES_PATH', 'sf-valid/etc/bundleversion.json', 'get_site_packages_path', None, None],
                 ['dictver', 'SITE_PACKAGES_PATH', 'dict/etc/bundleversion.json', 'get_site_packages_path', None, None],
                 ['dbloadver', 'SITE_PACKAGES_PATH', 'dbloader/etc/bundleversion.json', 'get_site_packages_path', None, None],
                 ['wurcs2pic', 'SITE_PACKAGES_PATH', 'wurcs2pic/BUNDLEVERSION', 'get_site_packages_path', None, None],
                 ['mapfixver', 'SITE_PACKAGES_PATH', 'mapFix/etc/BUNDLEVERSION', '', None, None],
                 ['phenixver', 'PHENIXROOT', 'phenix_env.sh', 'get_phenixroot', VersionEnum.PARSE_PHENIX_ENV, OptFlags.APP_VALIDATION],
                 ['ccp4ver', 'CCP4ROOT', 'restore/restores.xml', 'get_ccp4root', VersionEnum.PARSE_CCP4_VER, OptFlags.APP_VALIDATION],
                 ]

        for c in confs:
            varname = c[0]
            confvar = c[1]
            fpart = c[2]
            config_info_app_method = c[3]
            vers_method = c[4]
            vers_flags = c[5]

            try:
                tvers = self.__cparser.get('DEFAULT', varname)
                if config_info_app_method:
                    if vers_flags and (vers_flags & OptFlags.APP_VALIDATION):
                        class_method = getattr(self.__ci_val, config_info_app_method)
                    else:
                        class_method = getattr(self.__ci_common, config_info_app_method)
                    toolspath = class_method()
                else:
                    toolspath = self.get_variable(confvar)
                fname = os.path.join(toolspath, fpart)
                if not os.path.exists(fname):
                    print("WARNING: Tool out of date. %s not found" % fname)
                    continue
                if vers_method is None:
                    with open(fname, 'r') as fin:
                        if ".json" in fname:
                            jdata = json.load(fin)
                            vstring = jdata['Version']
                        else:
                            vstring = fin.read().strip()
                elif vers_method == VersionEnum.PARSE_PHENIX_ENV:
                    vstring = self.__parse_phenix_vers(fname)
                elif vers_method == VersionEnum.PARSE_CCP4_VER:
                    vstring = self.__parse_ccp4_vers(fname)
                else:
                    vstring = "UNKNOWN METHOD"
                if vstring != tvers:
                    print("***ERROR: Version mismatch %s != %s in %s" % (tvers, vstring, fname))
            except NoOptionError as e:
                # Option not in config file - continue
                pass

    def __parse_phenix_vers(self, fname):
        """Parses phenix environment setup script"""
        with open(fname, 'r') as fin:
            lines = [line.strip() for line in fin.readlines()]
        vlist = list(filter((lambda x: "export PHENIX_VERSION" in x), lines))
        if len(vlist) < 1:
            return "UNKNOWN"
        vstr = vlist[0].split("=")[1].strip()
        return vstr

    def __parse_ccp4_vers(self, fname):
        """Parses ccp4 restores xml file for update version"""
        tree = ET.parse(fname)
        root = tree.getroot()
        vstr = "XXXX"
        for element in root.findall("./update/id"):
            vstr = element.text  # Get the last one
        return vstr


    def buildtools(self, build_version='v-5200'):
        curdir = os.path.dirname(__file__)
        buildscript = os.path.join(curdir, 'BuildTools.py')

        command = 'python {} --config {} --build-version {}'.format(buildscript, self.__configfile, build_version)

        ret = self.__exec(command)
        if ret:
            print("ERROR: buildtools failed")
        pass

    def checkoelicense(self):
        try:
            # If not in config will fall through
            expdate = self.__cparser.get('DEFAULT', 'openeyeexp')
        except NoOptionError as e:
            # Option not in config file - continue
            return

        oelicfile = self.__ci_common.get_site_cc_oe_licence()
        # Might be in OS_ENVIRONMENT
        if not oelicfile:
            oelicfile = self.get_variable('SITE_CC_OE_LICENSE')
        if not oelicfile:
            print("***ERROR: Cannot determine open eye license from config")
            return

        with open(oelicfile, 'r') as fin:
            data = fin.readlines()
            for d in data:
                if "#EXP_DATE:" not in d:
                    continue
                edate = d.split(':')[1].strip()
                if edate != expdate:
                    print("ERROR: Openeye Licence expiration wrong  %s vs %s" % (edate, expdate))
                    # Only need single report
                    return


#        pass

def get_latest_version_no():
    """
    Get the latest config version from the parent directory
    using the pattern V[0-9]*

    Returns:
        String -- latest version found in onedep_admin dir
    """
    parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    versions = []

    if os.scandir:
        # scandir is only available for python > 3.6
        with os.scandir(parent_dir) as entries:
            for e in entries:
                if e.is_dir() and fnmatch.fnmatch(e.name, 'V[0-9]*'):
                    versions.append(e.name)
    else:
        # fallback for python 2
        for e in os.listdir(parent_dir):
            if os.path.isdir(e) and fnmatch.fnmatch(e.name, 'V[0-9]*'):
                versions.append(e)

    if len(versions) == 0:
        # this should never happen
        return None

    # Versions need to be sorted as tuples i.e. (5, 1), (5, 10) as string sorting will not work
    def ver_tuple(z):
        return tuple([int(x) for x in z.split('.') if x.isdigit()])

    latest_version = sorted(versions, key = lambda x:ver_tuple(x[1:]))[-1]
    
    return latest_version
    

def get_latest_version_filepath():
    parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    latest_version = get_latest_version_no()
    version_filepath = os.path.join(parent_dir, latest_version, '{}rel.conf'.format(latest_version.replace('.', '')))

    return version_filepath


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default='latest', help='Configuration file for release')
    parser.add_argument("--noop", "-n", default=False, action='store_true', help='Do not carry out actions')
    parser.add_argument("--skip-pip", default=False, action='store_true', help='Skip pip upgrade')
    parser.add_argument("--skip-remove", default=False, action='store_true', help='Skip removal of python packages')
    parser.add_argument("--skip-resources", default=False, action='store_true', help='Skip resources update')
    parser.add_argument("--skip-webfe", default=False, action='store_true', help='Skip webfe update')
    parser.add_argument("--skip-taxdb", default=False, action='store_true', help='Skip update of taxdb if needed')
    parser.add_argument("--skip-schema", default=False, action='store_true', help='Skip update of DB schemas if needed')
    parser.add_argument("--skip-toolvers", default=False, action='store_true', help='Skip checking versions of tools')
    parser.add_argument("--build-tools", default=False, action='store_true', help='Build tools that have been updated')
    parser.add_argument("--build-version", default='v-5200', help='Version of tools to build from')
    parser.add_argument("--use-https-git", default=False, action='store_true', help='If set, use https git instead of ssh for cloning repos')
    parser.add_argument("--build-dev", default=False, action='store_true', help='pip installs repos with edit param')
    parser.add_argument("--get-latest-version", default=False, action='store_true', help='get latest version number')

    args = parser.parse_args()

    config_version = args.config
    
    if args.get_latest_version:
        print(get_latest_version_no())
        sys.exit(0)

    if config_version == 'latest':
        # getting the latest config version available
        config_version = get_latest_version_filepath()

    if not os.path.exists(config_version):
        print('Failed to find config file: {}'.format(config_version))
        sys.exit(1)

    um = UpdateManager(config_version, args.noop)

    # update resources_ro
    if not args.skip_resources:
        um.updateresources(use_https_git=args.use_https_git)

    # Remove obsolete python packages
    if not args.skip_remove:
        um.removepyenv()

    # update python
    if not args.skip_pip:
        um.updatepyenv(args.build_dev, args.use_https_git)

    # update webfe
    if not args.skip_webfe:
        um.updatewebfe(use_https_git=args.use_https_git)

    # update taxonomy
    if not args.skip_taxdb:
        um.updatetaxdb()

    # update db schemas
    if not args.skip_schema:
        um.updateschema()

    if args.build_tools:
        um.buildtools(args.build_version)

    if not args.skip_toolvers:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            um.checktoolvers()
            um.checkoelicense()
        um.postflightdbcheck()

    # Final check on webfe
    if not args.skip_webfe:
        um.checkwebfe(overridenoop=True)


if __name__ == '__main__':
    main()
