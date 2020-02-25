#!/usr/bin/env python

"""A script to ensure that status and da_internal DB have proper schema.  This is separate from update script as needs to load fresh if pip install mysqlclient updates version
"""

import os
import argparse
import subprocess

from wwpdb.utils.config.ConfigInfo import ConfigInfo
from wwpdb.utils.db.MyConnectionBase import MyConnectionBase
from wwpdb.utils.db.MyDbUtil import MyDbQuery
from mmcif.io.PdbxReader import PdbxReader


class DbSchemaManager(object):
    """A class to manage updates to the various schemas"""
    def __init__(self, noop):
        self.__noop = noop
        self.__ci = ConfigInfo()
        self.__daintschema = []

        self.__configuration = [
            # name, resource, table,       approvefunc, 
            ['V3.6 depui', 'STATUS', 'deposition', '_notexists', 
             #  colname,  command_to_add
             [['post_rel_status', 'ADD COLUMN `post_rel_status` VARCHAR(6) NULL DEFAULT NULL AFTER `status_code_other`'],
              ['post_rel_recvd_coord', 'ADD COLUMN `post_rel_recvd_coord` VARCHAR(1) NULL DEFAULT NULL AFTER `post_rel_status`'],
              ['post_rel_recvd_coord_date', 'ADD COLUMN `post_rel_recvd_coord_date` DATE NULL DEFAULT NULL AFTER `post_rel_recvd_coord`']]],
            ['V3.7 depui', 'STATUS', 'user_data', '_colwidth',
             # colname, command to alter, expected width
             [['country', "CHANGE COLUMN `country` `country` VARCHAR(100) NOT NULL DEFAULT ''", 100]]],
            ['V3.7 da_internal', 'DA_INTERNAL', 'rcsb_status', '_daintnotexists',
             [['post_rel_status', 'ADD COLUMN `post_rel_status` VARCHAR(10) NULL AFTER `pdb_format_compatible`'],
              ['post_rel_recvd_coord', 'ADD COLUMN `post_rel_recvd_coord` VARCHAR(1) NULL AFTER `post_rel_status`'],
              ['post_rel_recvd_coord_date', 'ADD COLUMN `post_rel_recvd_coord_date` DATE NULL AFTER `post_rel_recvd_coord`']]],
            ['V4.5 da_internal', 'DA_INTERNAL', 'rcsb_status', '_daintnotexists',
             [['status_code_nmr_data', 'ADD COLUMN `status_code_nmr_data` VARCHAR(10) NULL AFTER `post_rel_recvd_coord_date`'],
              ['recvd_nmr_data', 'ADD COLUMN `recvd_nmr_data` VARCHAR(1) NULL AFTER `status_code_nmr_data`'],
              ['date_nmr_data', 'ADD COLUMN `date_nmr_data` DATE NULL AFTER `recvd_nmr_data`'],
              ['date_hold_nmr_data', 'ADD COLUMN `date_hold_nmr_data` DATE NULL AFTER `date_nmr_data`'],
              ['dep_release_code_nmr_data', 'ADD COLUMN `dep_release_code_nmr_data` VARCHAR(24) NULL AFTER `date_hold_nmr_data`'],
              ['date_of_nmr_data_release', 'ADD COLUMN `date_of_nmr_data_release` DATE NULL AFTER `dep_release_code_nmr_data`']]],
        ]

        self.__wftasks = [
            # idname     filename
            [ 'SeqModUI', 'SequenceModuleUI.xml'],
            # [ 'LigModUI', 'LigandModuleUI.xml']
        ]


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
                if len(row) == 3:
                    opt = row[2]
                    rc = mth(mydb._dbCon, table, colname, opt)
                else:
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


    def updatewftasks(self):
        """ Handles the addition of new WF tasks """

        print("")
        print("Checking WF scheme status DB")

        mydb = MyConnectionBase()
        mydb.setResource(resourceName="STATUS")
        ok = mydb.openConnection()
        if not ok:
                print("ERROR: Could not open resource %s" % 'STATUS')
                return

        defpath = self.__ci.get('SITE_WF_XML_PATH')

        for taskid, fname in self.__wftasks:

            fpath = os.path.join(defpath, fname)

            if not os.path.exists(fpath):
                #print("Skipping %s as does not exist" % fname)
                continue

            myq = MyDbQuery(dbcon=mydb._dbCon)
            query = "select wf_class_id from wf_class_dict where wf_class_id='{}'".format(taskid)

            rows = myq.selectRows(queryString=query)
            if len(rows) == 0:
                print("About to install WF schema %s with name %s" % (taskid, fname))
                cmd = 'python -m wwpdb.apps.wf_engine.wf_engine_utils.tasks.WFTaskRequestExec --verbose --load_wf_def_file={}'.format(fname)
                self.__exec(cmd)
                
        mydb.closeConnection()

    def __exec(self, cmd, overridenoop = False):
        print(cmd)
        ret = 0
        if not self.__noop or overridenoop:
            ret = subprocess.call(cmd, shell=True)
        return ret

    def checkviews(self):
        """Checks that views are loaded and updated"""

        print("")
        print("Checking views in status DB")
        mydb = MyConnectionBase()
        mydb.setResource(resourceName="STATUS")
        ok = mydb.openConnection()
        if not ok:
            print("ERROR: Could not open resource %s" % 'STATUS')
            return

        if self._notexists(mydb._dbCon, 'dep_last_instance', 'dep_post_rel_status'):
            print("ERROR: You need to load latest views with something like: mysql -uroot -pxxxx -hxxxx status < %s/dep-views.sql" 
                  % os.path.dirname(__file__))

        mydb.closeConnection()

    def _notexists(self, dbconn, table, colname):
        """Checks if colname exists in table. Returns True if does not exist"""
        myq = MyDbQuery(dbcon=dbconn)
        query = "show columns from `{}` LIKE '{}'".format(table, colname)
        rows = myq.selectRows(queryString=query)
        if len(rows) == 0:
            return True
        return False

    def _colwidth(self, dbconn, table, colname, width):
        """Returns True if colname is not width characters"""
        myq = MyDbQuery(dbcon=dbconn)
        query = "select character_maximum_length from information_schema.columns where table_schema=Database() and table_name='{}' and column_name='{}'".format(table,colname);
        rows = myq.selectRows(queryString=query)
        if len(rows) == 0:
            print("ERROR  {}.{} does not exist!!!!!!!".format(table,colname))
            return False

        size = rows[0][0]
        if size != width:
            return True
        return False

    def _daintnotexists(self, dbconn, table, colname):
        """Return True if _rcsb_attribute_def.table_name == table and
        _rcsb_attribute_def.attribute_name == colname is present in 
        SITE_DA_INTERNAL_SCHEMA_PATH and colname is not present in table my da_internal"""

        schemapath = self.__ci.get('SITE_DA_INTERNAL_SCHEMA_PATH')
        if not schemapath:
            print("ERROR: SITE_DA_INTERNAL_SCHEMA_PATH not in site-config")
            return False

        if not len(self.__daintschema):
            with open(schemapath, 'r') as fin:
                prd = PdbxReader(fin)
                self.__daintschema = []
                prd.read(containerList = self.__daintschema, selectList=['rcsb_attribute_def'])

        block = self.__daintschema[0]
        tb = block.getObj('rcsb_attribute_def')
        if not tb:
            print("ERROR: Schema file does not contain rcsb_attribute_def")
            return False
        #print dir(tb)
        #block.printIt()
        found = False
        for row in range(tb.getRowCount()):
            table_name = tb.getValue('table_name', row)
            attr_name = tb.getValue('attribute_name', row)
            if table_name == table and attr_name == colname:
                found = True
                break

        if not found:
            # schema mapping file does not list - we should not need to add
            #print("{}.{} not found -- skipping".format(table, colname))
            return False

        # Ok exists in schema file. See if in database
        ret = self._notexists(dbconn, table, colname)

        return ret


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--noop", required=False, help='Indicate what is needed but do not act',
                        action='store_true', default=False)

    subparsers = parser.add_subparsers(help='sub-command help')

    sub_b = subparsers.add_parser('update', help='update schemas')
    sub_b.set_defaults(func="update")

    sub_c = subparsers.add_parser('finalcheck', help='check schema but do nothing')
    sub_c.set_defaults(func="check")

    args = parser.parse_args()
    dbs = DbSchemaManager(args.noop)

    if args.func == 'update':
       dbs.updateschema()
       dbs.updatewftasks()
    elif args.func=='finalcheck':
        dbs.checkviews()

if __name__ == '__main__':
    main()
