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
import subprocess
import argparse
import os.path
import fnmatch
import json
import sys

import os
from wwpdb.utils.config.ConfigInfo import ConfigInfo

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

        instenv = self.__ci.get('INSTALL_ENVIRONMENT')
        self.__extraconf = instenv.get("ADMIN_EXTRA_CONF", None)
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

    def __exec(self, cmd, overridenoop = False, working_directory=False):
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

    def updatepyenv(self, dev_build):
        instenv = self.__ci.get('INSTALL_ENVIRONMENT')
        cs_user = instenv['CS_USER']
        cs_pass = instenv['CS_PW']
        cs_url = instenv['CS_URL']

        urlreq = urlparse(cs_url)
        urlpath = "{}://{}:{}@{}{}/dist/simple/".format(urlreq.scheme, cs_user, cs_pass, urlreq.netloc, urlreq.path)

        # pip installing from requirements.txt in base_packages
        script_dir = os.path.dirname(os.path.realpath(__file__))

        reqfile = os.path.abspath(os.path.join(script_dir, '../base_packages/pre-requirements.txt'))
        constraintfile = os.path.abspath(os.path.join(script_dir, '../base_packages/constraints.txt'))
        pip_extra_urls = "--extra-index-url {} --trusted-host {} --extra-index-url https://pypi.anaconda.org/OpenEye/simple -c {}".format(
            urlpath, urlreq.netloc, constraintfile)

        command = 'pip install {} -r {}'.format(pip_extra_urls, reqfile)
        self.__exec(command)

        reqfile = os.path.abspath(os.path.join(script_dir, '../base_packages/requirements.txt'))
        command = 'pip install {} -r {} '.format(pip_extra_urls, reqfile)
        self.__exec(command)

        if self.__cparser.has_option('DEFAULT', 'pip_extra_reqs'):
            opt_req = self.__cparser.get('DEFAULT', 'pip_extra_reqs', vars = self.__confvars)
        else:
            opt_req = None

        reqfile = self.__cparser.get('DEFAULT', 'piprequirements')
        if dev_build:
            # Clone and do pip edit install
            webappsdir = self.__ci.get('TOP_WWPDB_WEBAPPS_DIR')

            # Checking if source directory exist
            source_dir = os.path.abspath(os.path.join(webappsdir, '../..'))
            if not os.path.isdir(source_dir):
                os.makedirs(source_dir)

            path_to_list_of_repo = os.path.abspath(os.path.join(script_dir, '../base_packages/requirements_wwpdb_dependencies.txt'))
            with open(path_to_list_of_repo) as list_of_repo:
                for repo in list_of_repo:
                    command = 'git clone --recursive git@github.com:wwPDB/{}.git'.format(repo.rstrip())
                    self.__exec(command, working_directory=source_dir)
                    command = 'pip install {} --edit {}'.format(pip_extra_urls, repo)
                    self.__exec(command, working_directory=source_dir)
        else:
            # reqfile = self.__cparser.get('DEFAULT', 'piprequirements')
            command = 'pip install -U {} -r {}'.format(pip_extra_urls, reqfile)
            self.__exec(command)

        if opt_req:
            command = 'export CS_USER={}; export CS_PW={}; export CS_URL={}; export URL_NETLOC={}; export URL_PATH={}; pip install -U {} -r {}'.format(
                cs_user, cs_pass, cs_url, urlreq.netloc, urlreq.path, urlpath, urlreq.netloc, pip_extra_urls, opt_req)
            self.__exec(command)

    def updateresources(self):
        restag = self.__cparser.get('DEFAULT', 'resourcestag')
        resdir = self.__ci.get('RO_RESOURCE_PATH')
        if resdir:
            if not os.path.exists(resdir):
                command = 'git clone git@github.com:wwPDB/onedep-resources_ro.git {}'.format(resdir)
                self.__exec(command)

            command = 'cd {}; git pull; git checkout master; git pull; git checkout {}; git pull origin {}'.format(resdir, restag, restag)
            self.__exec(command)

    def checkwebfe(self, overridenoop = False):
        webappsdir = self.__ci.get('TOP_WWPDB_WEBAPPS_DIR')
        webdir = os.path.abspath(os.path.join(webappsdir, '..'))
        curdir = os.path.dirname(__file__)
        checkscript = os.path.join(curdir, 'ManageWebFE.py')
        webfecheck = self.__cparser.get('DEFAULT', 'webfeconf')

        command = 'python {} --webroot {} check -r {}'.format(checkscript, webdir, webfecheck)
        ret = self.__exec(command, overridenoop = overridenoop)
        if ret:
            print("ERROR: check of webfe directory failed")


    def updatewebfe(self):
        webappsdir = self.__ci.get('TOP_WWPDB_WEBAPPS_DIR')

        # Checking if source directory exist
        source_dir = os.path.abspath(os.path.join(webappsdir, '../..'))
        if not os.path.isdir(source_dir):
            os.makedirs(source_dir)

        #Check if repo is cloned
        webfe_repo = os.path.abspath(os.path.join(webappsdir, '..'))
        if not os.path.isdir(webfe_repo):
            command = 'git clone --recurse-submodules git@github.com:wwPDB/onedep-webfe.git'
            self.__exec(command, working_directory=source_dir)
            self.checkwebfe()

        webfetag = self.__cparser.get('DEFAULT', 'webfetag')

        command = 'cd {}; git pull; git checkout {}; git pull origin {}; git submodule init; git submodule update'.format(webfe_repo, webfetag, webfetag)
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
        taxresource = self.__ci.get('TAXONOMY_FILE_NAME')


        if not taxresource:
            print("ERROR: TAXONOMY_FILE_NAME is not set in site-config")
            return

        curdir = os.path.dirname(__file__)
        checkscript = os.path.join(curdir, 'ManageTaxDB.py')

        if self.__noop:
            command = 'python {} --noop update --maxsize {} --taxdbsize {}'.format(checkscript, maxsize, taxdbsize)
        else:
            command = 'python {} --maxsize {} --taxdbsize {}'.format(checkscript,  maxsize, taxdbsize)
        self.__exec(command)

    def updateschema(self):
        dbs = DbSchemaManager(self.__noop)
        dbs.updateschema()

    def postflightdbcheck(self):
        dbs = DbSchemaManager(self.__noop)
        dbs.checkviews()
        

    def checktoolvers(self):
        #  vers_config_var,  configinfovar,             relative path
        confs = [['annotver', 'SITE_ANNOT_TOOLS_PATH', 'etc/bundleversion.json'],
                 ['webfever', 'TOP_WWPDB_WEBAPPS_DIR', 'version.json'],
                 ['resourcever', 'RO_RESOURCE_PATH', 'version.json'],
                 ['cctoolsver', 'SITE_CC_APPS_PATH', 'etc/bundleversion.json'],
                 ['sfvalidver', 'SITE_PACKAGES_PATH', 'sf-valid/etc/bundleversion.json']
             ]

        for c in confs:
            varname = c[0]
            confvar = c[1]
            fpart = c[2]

            try:
                tvers = self.__cparser.get('DEFAULT', varname)
                toolspath = self.__ci.get(c[1])
                fname = os.path.join(toolspath, fpart)
                if not os.path.exists(fname):
                    print("WARNING: Tool out of date. %s not found" % fname)
                    continue
                with open(fname, 'r') as fin:
                    jdata = json.load(fin)
                    vstring = jdata['Version']
                    if vstring != tvers:
                        print("***ERROR: Version mismatch %s != %s in %s" % (tvers, vstring, fname))
            except NoOptionError as e:
                # Option not in config file - continue
                pass

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


        oelicfile = self.__ci.get('SITE_CC_OE_LICENSE')
        # Might be in OS_ENVIRONMENT
        if not oelicfile:
            oelicfile = os.getenv('SITE_CC_OE_LICENSE')
        if not oelicfile:
            print ("***ERROR: Cannot determine open eye license from config")
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

def get_latest_version_filepath():
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
    
    latest_version = sorted(versions)[-1]
    version_filepath = os.path.join(parent_dir, latest_version, '{}rel.conf'.format(latest_version.replace('.', '')))
    
    return version_filepath


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default='latest', help='Configuration file for release')
    parser.add_argument("--noop", "-n", default=False, action='store_true', help='Do not carry out actions')
    parser.add_argument("--skip-pip", default=False, action='store_true', help='Skip pip upgrade')
    parser.add_argument("--skip-resources", default=False, action='store_true', help='Skip resources update')
    parser.add_argument("--skip-webfe", default=False, action='store_true', help='Skip webfe update')
    parser.add_argument("--skip-taxdb", default=False, action='store_true', help='Skip update of taxdb if needed')
    parser.add_argument("--skip-schema", default=False, action='store_true', help='Skip update of DB schemas if needed')
    parser.add_argument("--skip-toolvers", default=False, action='store_true', help='Skip checking versions of tools')
    parser.add_argument("--build-tools", default=False, action='store_true', help='Build tools that have been updated')
    parser.add_argument("--build-version", default='v-5200', help='Version of tools to build from')
    parser.add_argument("--build-dev", default=False, action='store_true', help='pip installs repos with edit param')

    args = parser.parse_args()
    print(args)

    config_version = args.config

    if config_version == 'latest':
        # getting the latest config version available
        config_version = get_latest_version_filepath()

    if not os.path.exists(config_version):
        print('Failed to find config file: {}'.format(config_version))
        sys.exit(1)

    um = UpdateManager(config_version, args.noop)

    # update resources_ro
    if not args.skip_resources:
        um.updateresources()

    # update python
    if not args.skip_pip:
            um.updatepyenv(args.build_dev)

    # update webfe
    if not args.skip_webfe:
        um.updatewebfe()

    # update taxonomy
    if not args.skip_taxdb:
        um.updatetaxdb()

    # update db schemas
    if not args.skip_schema:
        um.updateschema()

    if args.build_tools:
        um.buildtools(args.build_version)

    if not args.skip_toolvers:
        um.checktoolvers()
        um.checkoelicense()
        um.postflightdbcheck()

    # Final check on webfe
    if not args.skip_webfe:
        um.checkwebfe(overridenoop = True)

if __name__ == '__main__':
    main()
