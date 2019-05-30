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
import json

from wwpdb.utils.config.ConfigInfo import ConfigInfo
from wwpdb.utils.db.MyConnectionBase import MyConnectionBase
from wwpdb.utils.db.MyDbUtil import MyDbQuery


class DbSchemaManager(object):
    """A class to manage updates to the various schemas"""
    def __init__(self, noop):
        self.__noop = noop
        self.__ci = ConfigInfo()
        self.__configuration = [
            # name, resource, table,       approvefunc, 
            ['V3.6 depui', 'STATUS', 'deposition', '_notexists', 
             #  colname,  command_to_add
             [['post_rel_status', 'ADD COLUMN `post_rel_status` VARCHAR(6) NULL DEFAULT NULL AFTER `status_code_other`'],
              ['post_rel_recvd_coord', 'ADD COLUMN `post_rel_recvd_coord` VARCHAR(1) NULL DEFAULT NULL AFTER `post_rel_status`'],
              ['post_rel_recvd_coord_date', 'ADD COLUMN `post_rel_recvd_coord_date` DATE NULL DEFAULT NULL AFTER `post_rel_recvd_coord`']]
             ]
            ]

        #ALTER TABLE `status`.`deposition` 
    def updateschema(self):
        """Updates the schema configurations"""

        print("")
        print("Checking/Updating DB schema")

        for upd in self.__configuration:
            name = upd[0]
            resource = upd[1]
            table = upd[2]
            func = upd[3]
            #getattr(self, upd[3], None)
            mth = getattr(self, func, None)
            coldata = upd[4]
            #print name, resource, table, func, mth

            mydb = MyConnectionBase()
            mydb.setResource(resourceName=resource)
            ok = mydb.openConnection()
            if not ok:
                print("ERROR: Could not open resource %s" % resource)
                return

            for row in coldata:
                colname = row[0]
                cmd = row[1]
                rc = mth(mydb._dbCon, table, colname)
                if rc:
                    myq = MyDbQuery(dbcon=mydb._dbCon)
                    query = "ALTER TABLE `{}` {}".format(table, cmd)
                    print(query)
                    if not self.__noop:
                        ret = myq.sqlCommand([query])
                        if not ret:
                            print("ERROR UPDATING SCHEMA %s" % query)
                    
            mydb.closeConnection()

    def _notexists(self, dbconn, table, colname):
        myq = MyDbQuery(dbcon=dbconn)
        query = "show columns from `{}` LIKE '{}'".format(table, colname)
        rows = myq.selectRows(queryString=query)
        if len(rows) == 0:
            return True
        return False

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

    def __exec(self, cmd, overridenoop = False):
        print(cmd)
        ret = 0
        if not self.__noop or overridenoop:
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

        command = 'cd {}; git pull; git checkout {}; git pull'.format(resdir, restag)
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
        webdir = os.path.abspath(os.path.join(webappsdir, '..'))
        webfetag = self.__cparser.get('DEFAULT', 'webfetag')

        command = 'cd {}; git pull; git checkout {}; git pull ; git submodule init; git submodule update'.format(webdir, webfetag)
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

        if count >= taxdbsize and count < maxsize:
            print("Taxdb at least as big as expected")
            return

        if not taxresource:
            print("ERROR: TAXONOMY_FILE_NAME is not set in site-config")
            return
        command = "python -m wwpdb.apps.deposit.depui.taxonomy.loadData"
        self.__exec(command)

    def updateschema(self):
        dbs = DbSchemaManager(self.__noop)
        dbs.updateschema()

    def checktoolvers(self):
        #  vers_config_var,  configinfovar,             relative path
        confs = [['annotver', 'SITE_ANNOT_TOOLS_PATH', 'etc/bundleversion.json'],
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
                        print("WARNING: Tool version mismatch %s != %s in %s" % (tvers, vstring, fname))
            except NoOptionError as e:
                # Option not in config file - continue
                pass


#        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help='Configuration file for release')
    parser.add_argument("--noop", "-n", default=False, action='store_true', help='Do not carry out actions')
    parser.add_argument("--skip-pip", default=False, action='store_true', help='Skip pip upgrade')
    parser.add_argument("--skip-resources", default=False, action='store_true', help='Skip resources update')
    parser.add_argument("--skip-webfe", default=False, action='store_true', help='Skip webfe update')
    parser.add_argument("--skip-taxdb", default=False, action='store_true', help='Skip update of taxdb if needed')
    parser.add_argument("--skip-schema", default=False, action='store_true', help='Skip update of DB schemas if needed')
    parser.add_argument("--skip-toolvers", default=False, action='store_true', help='Skip checking versions of tools')

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

    # update db schemas
    if not args.skip_schema:
        um.updateschema()

    if not args.skip_toolvers:
        um.checktoolvers()

    # Final check on webfe
    if not args.skip_webfe:
        um.checkwebfe(overridenoop = True)

if __name__ == '__main__':
    main()
