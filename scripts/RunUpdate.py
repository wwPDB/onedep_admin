#!/usr/bin/env python

"""Utility to help run/manage update of OneDep system"""

try:
    from ConfigParser import ConfigParser
except ImportError:
    from configparser import ConfigParser
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse
import subprocess
import argparse
import os.path

from wwpdb.utils.config.ConfigInfo import ConfigInfo
from wwpdb.utils.db.MyConnectionBase import MyConnectionBase
from wwpdb.utils.db.MyDbUtil import MyDbQuery


class UpdateManager(object):
    def __init__(self, config_file, noop):
        self.__configfile = config_file
        self.__noop = noop
        self.__ci = ConfigInfo()

        # Infer topdir from where running from
        topdir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        cdict = {'topdir': topdir}

        self.__cparser = ConfigParser(cdict)
        self.__cparser.read(self.__configfile)

        # print(self.__cparser.defaults())
        # print()

    def __exec(self, cmd):
        print(cmd)
        ret = 0
        if not self.__noop:
            ret = subprocess.call(cmd, shell=True)
        return ret

    def updatepyenv(self):
        reqfile = self.__cparser.get('DEFAULT', 'piprequirements')
        instenv = self.__ci.get('INSTALL_ENVIRONMENT')
        cs_user = instenv['CS_USER']
        cs_pass = instenv['CS_PW']
        cs_url = instenv['CS_URL']

        urlreq = urlparse(cs_url)
        urlpath = "{}://{}:{}@{}{}/dist/simple/".format(urlreq.scheme, cs_user, cs_pass, urlreq.netloc, urlreq.path)

        command = 'pip install -U --extra-index-url {} --trusted-host {} -r {}'.format(urlpath, urlreq.netloc, reqfile)
        self.__exec(command)

    def updateresources(self):
        restag = self.__cparser.get('DEFAULT', 'resourcestag')
        resdir = self.__ci.get('RO_RESOURCE_PATH')

        command = 'cd {}; git pull; git checkout {}'.format(resdir, restag)
        self.__exec(command)

    def updatewebfe(self):
        webappsdir = self.__ci.get('TOP_WWPDB_WEBAPPS_DIR')
        webdir = os.path.abspath(os.path.join(webappsdir, '..'))
        webfetag = self.__cparser.get('DEFAULT', 'webfetag')
        webfecheck = self.__cparser.get('DEFAULT', 'webfeconf')
        curdir = os.path.dirname(__file__)
        checkscript = os.path.join(curdir, 'ManageWebFE.py')

        command = 'cd {}; git pull; git checkout {}; git submodule init; git submodule update'.format(webdir, webfetag)
        self.__exec(command)

        # Now check the results

        command = 'python {} --webroot {} check -r {}'.format(checkscript, webdir, webfecheck)
        ret = self.__exec(command)
        if ret:
            print("ERROR: check of webfe directory failed")

    def updatetaxdb(self):
        # Checks the number of rows in db and decides if to update
        taxdbsize = int(self.__cparser.get('DEFAULT', 'taxdbminsize'))
        taxresource = self.__ci.get('TAXONOMY_FILE_NAME')

        mydb = MyConnectionBase()
        mydb.setResource(resourceName="STATUS")
        ok = mydb.openConnection()
        if not ok:
            print("ERROR: Could not open status db")
            return

        myq = MyDbQuery(dbcon=mydb._dbCon)
        query = "select count(ordinal) from taxonomy "

        rows = myq.selectRows(queryString=query)

        count = rows[0][0]

        mydb.closeConnection()

        if count >= taxdbsize:
            print("Taxdb at least as big as expected")
            return

        if not taxresource:
            print("ERROR: TAXONOMY_FILE_NAME is not set in site-config")
            return
        command = "python -m wwpdb.apps.deposit.depui.taxonomy.loadData"
        self.__exec(command)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help='Configuration file for release')
    parser.add_argument("--noop", "-n", default=False, action='store_true', help='Do not carry out actions')
    parser.add_argument("--skip-pip", default=False, action='store_true', help='Skip pip upgrade')
    parser.add_argument("--skip-resources", default=False, action='store_true', help='Skip resources update')
    parser.add_argument("--skip-webfe", default=False, action='store_true', help='Skip webfe update')
    parser.add_argument("--skip-taxdb", default=False, action='store_true', help='Skip update of taxdb if needed')

    args = parser.parse_args()
    print(args)

    um = UpdateManager(args.config, args.noop)

    # update resources_ro
    if not args.skip_resources:
        um.updateresources()

    # update webfe
    if not args.skip_webfe:
        um.updatewebfe()

    # update python
    if not args.skip_pip:
        um.updatepyenv()

    # update taxonomy
    if not args.skip_taxdb:
        um.updatetaxdb()


if __name__ == '__main__':
    main()
