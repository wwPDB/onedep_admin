#!/usr/bin/env python

"""A script to ensure that status and da_internal DB have proper schema.  This is separate from update script as needs to load fresh if pip install mysqlclient updates version
"""

import os
import argparse
import subprocess
import sys
import warnings

from wwpdb.utils.config.ConfigInfo import ConfigInfo
from wwpdb.utils.config.ConfigInfoApp import ConfigInfoAppCommon
from wwpdb.utils.db.MyConnectionBase import MyConnectionBase
from wwpdb.utils.db.MyDbUtil import MyDbQuery
from wwpdb.utils.db.MyDbAdapter import MyDbAdapter
from wwpdb.utils.db.WorkflowSchemaDef import WorkflowSchemaDef
from mmcif.io.PdbxReader import PdbxReader


class WFTaskRequest(MyDbAdapter):

    """Manage workflow task requests and status queries --"""

    def __init__(self, verbose=False, log=sys.stderr):
        super(WFTaskRequest, self).__init__(schemaDefObj=WorkflowSchemaDef(verbose=verbose, log=log), verbose=verbose, log=log)

    def getWfClassDict(self, **opts):
        self._setDebug(True)

        tableId = "WF_CLASS_DICT"
        mapL = self._getDefaultAttributeParameterMap(tableId)
        self._setAttributeParameterMap(tableId=tableId, mapL=mapL)

        s = self._select(tableId=tableId, **opts)
        return s

    def addWfClassDict(self, **wfOpts):
        #
        tableId = "WF_CLASS_DICT"
        mapL = self._getDefaultAttributeParameterMap(tableId)
        self._setAttributeParameterMap(tableId=tableId, mapL=mapL)
        return self._insertRequest(tableId=tableId, contextId=None, **wfOpts)


class DbSchemaManager(object):
    """A class to manage updates to the various schemas"""
    def __init__(self, noop):
        self.__noop = noop
        self.__ci = ConfigInfo()
        self.__ci_common = ConfigInfoAppCommon()
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
            ['V5.8 da_internal', 'DA_INTERNAL', 'struct', '_daintnotexists',
             [['pdbx_center_of_mass_x', 'ADD COLUMN `pdbx_center_of_mass_x` float NULL AFTER `pdbx_CASP_flag`'],
              ['pdbx_center_of_mass_y', 'ADD COLUMN `pdbx_center_of_mass_y` float NULL AFTER `pdbx_center_of_mass_x`'],
             ['pdbx_center_of_mass_z', 'ADD COLUMN `pdbx_center_of_mass_z` float NULL AFTER `pdbx_center_of_mass_y`']]],
            ['V5.18 da_internal', 'DA_INTERNAL', 'pdbx_depui_entry_details', '_daintnotexists',
             [['validated_identifier_ORCID', 'ADD COLUMN `validated_identifier_ORCID` varchar(20) NULL AFTER `wwpdb_site_id`']]],
            ['V5.25 da_internal', 'DA_INTERNAL', 'em_admin', '_daintnotexists',
             [['composite_map', 'ADD COLUMN `composite_map` VARCHAR(3) DEFAULT NULL AFTER `entry_id`']]],
            ['V5.28 da_internal', 'DA_INTERNAL', 'database_2', '_daintnotexists',
             [['pdbx_database_accession', 'ADD COLUMN `pdbx_database_accession` varchar(20) NULL AFTER `database_code`'],
              ['pdbx_DOI', 'ADD COLUMN `pdbx_DOI` varchar(30) NULL AFTER `pdbx_database_accession`']]],
            ['V5.30 da_internal', 'DA_INTERNAL', 'pdbx_audit_revision_history', '_daintnotexists',
             [['internal_part_number', 'ADD COLUMN `internal_part_number` INT NULL AFTER `internal_deposition_id`'],
              ['part_number', 'ADD COLUMN `part_number` INT NULL AFTER `internal_part_number`']]],

        ]

        self.__tableexists = [
            ['V5.8 da_internal cell table', 'DA_INTERNAL', 'cell1', '_dainttablenotexists', 
             ["""CREATE TABLE IF NOT EXISTS cell1
                (
             Structure_ID                                                 varchar(15)    not null,
             angle_alpha                                                  float              null,
             angle_beta                                                   float              null,
             angle_gamma                                                  float              null,
             entry_id                                                     varchar(15)    not null,
             details                                                      varchar(200)       null,
             length_a                                                     float              null,
             length_b                                                     float              null,
             length_c                                                     float              null,
             volume                                                       float              null,
             Z_PDB                                                        int                null,
             pdbx_unique_axis                                             varchar(200)       null
             );""",

              """CREATE UNIQUE INDEX primary_index ON cell1
              (
              Structure_ID,
              entry_id
              );
              """
          ]],

            ['V5.8 da_internal pdbx_struct_oper_list table', 'DA_INTERNAL', 'pdbx_struct_oper_list', '_dainttablenotexists', 
             ["""CREATE TABLE IF NOT EXISTS pdbx_struct_oper_list
             (
             Structure_ID                                                 varchar(15)    not null,
             id                                                           varchar(10)    not null,
             type                                                         varchar(80)        null,
             name                                                         varchar(80)        null,
             symmetry_operation                                           varchar(80)        null
             );""",

              """CREATE UNIQUE INDEX primary_index ON pdbx_struct_oper_list
              (
              Structure_ID,
              id
              );"""
          ]],

            ['V5.8 da_internal pdbx_struct_assembly_gen table', 'DA_INTERNAL', 'pdbx_struct_assembly_gen', '_dainttablenotexists', 
             ["""CREATE TABLE IF NOT EXISTS pdbx_struct_assembly_gen
             (
             Structure_ID                                                 varchar(15)    not null,
             assembly_id                                                  varchar(80)    not null,
             entity_inst_id                                               varchar(10)        null,
             asym_id_list                                                 varchar(32000) not null,
             auth_asym_id_list                                            varchar(80)        null,
             oper_expression                                              varchar(511)   not null
             );"""
          ]],

            ['V5.8 da_internal symmetry table', 'DA_INTERNAL', 'symmetry', '_dainttablenotexists', 
             ["""CREATE TABLE IF NOT EXISTS symmetry
             (
             Structure_ID                                                 varchar(15)    not null,
             entry_id                                                     varchar(15)    not null,
             cell_setting                                                 varchar(13)        null,
             Int_Tables_number                                            int                null,
             space_group_name_Hall                                        varchar(80)        null,
             space_group_name_H_M                                         varchar(80)        null,
             pdbx_full_space_group_name_H_M                               varchar(80)        null
             );""",
              """CREATE UNIQUE INDEX primary_index ON symmetry
              (
              Structure_ID,
              entry_id
              );"""
          ]],

            ['V5.8.1 da_internal pdbx_struct_assembly', 'DA_INTERNAL', 'pdbx_struct_assembly', '_dainttablenotexists', 
             ["""CREATE TABLE pdbx_struct_assembly
             (
             Structure_ID                       varchar(15)    not null,
             method_details                     varchar(200)       null,
             oligomeric_details                 varchar(255)       null,
             oligomeric_count                   int                null,
             details                            varchar(200)       null,
             id                                 varchar(80)    not null
             );""",
              """CREATE UNIQUE INDEX primary_index ON pdbx_struct_assembly
              (
              Structure_ID,
              id
              );"""
          ]],

            ['V5.10 status entry_flags', 'STATUS', 'entry_flags', '_nottableexists',
             ["""CREATE TABLE entry_flags
             (
             dep_set_id                         VARCHAR(45) NOT NULL COMMENT 'The deposition id D_XXX',
             flag                               VARCHAR(45) NOT NULL COMMENT 'The flag name',
             value                              VARCHAR(45) NULL COMMENT 'The flag value, may be Y/N or more complicated depending on flag requirements',
             PRIMARY KEY (dep_set_id, flag)
             );"""
          ]],

            ['V5.12 da_internal em_depui', 'DA_INTERNAL', 'em_depui', '_dainttablenotexists', 
             ["""CREATE TABLE em_depui
             (
             Structure_ID                                                 varchar(15)    not null,
             depositor_hold_instructions                                  varchar(80)        null,
             macromolecule_description                                    varchar(10)        null,
             obsolete_instructions                                        varchar(200)       null,
             same_authors_as_pdb                                          varchar(5)         null,
             same_title_as_pdb                                            varchar(5)         null
             );""",
              """CREATE UNIQUE INDEX primary_index ON em_depui
              (
              Structure_ID
              );"""
          ]],


            ['V5.12 da_internal em_author_list', 'DA_INTERNAL', 'em_author_list', '_dainttablenotexists', 
             ["""CREATE TABLE em_author_list
             (
             Structure_ID                                                 varchar(15)    not null,
             author                                                       varchar(150)       null,
             ordinal                                                      int            not null,
             identifier_ORCID                                             varchar(20)        null
             );""",
              """CREATE UNIQUE INDEX primary_index ON em_author_list
              (
              Structure_ID,
              ordinal
              );"""
          ]]

        ]



        self.__wftasks = [
            # idname     filename
            [ 'SeqModUI', 'SequenceModuleUI.xml'],
            [ 'AnnModUI', 'AnnotateModuleUI.xml'],
            [ 'TransModUI', 'TransformerModuleUI.xml'],
            # Not needed now that WF name is not too long - it loads automatically from DepUI
            # [ 'MRG2CIF_TDEP', 'wf_op_nmr_mrg2cif_fs_tempdep.xml'],
            # [ 'MRG2CIF_DEP', 'wf_op_nmr_mrg2cif_fs_deposit.xml'],
            # [ 'LigModUI', 'LigandModuleUI.xml']
        ]

        # In release V5.26, the X-ray processing was revisited, but the incorrect class ids were used.  We need to register improper names with wf_class_dict
        self.__compat_dict = [
            # wf_class_id      | wf_class_name                   | title                                | author | version | class_file                   
            ["merge-xyz-pdbx", "wf_op_mrgpdbxdep_fs_tempdep.xml", "Merge with PDBx coordinates (tempdep)", "jdw", "1.51", "wf_op_mrgpdbxdep_fs_tempdep.xml"],
            ["pdbx-fmt-check", "wf_op_pdbxfmck_fs_tempdep.xml", "PDBx Format check (tempdep)", "jdw", "1.51", "wf_op_pdbxfmck_fs_tempdep.xml"]
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
            #print(name, resource, table, func, mth)
            if mth is None:
                print("INTERNAL ERROR: %s does not exist" % func)
                return

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

        self.__updatemissingtables()

    def __updatemissingtables(self):
        """Update missing tables"""
        for upd in self.__tableexists:
            name = upd[0]
            resource = upd[1]
            table = upd[2]
            func = upd[3]
            mth = getattr(self, func, None)
            commands = upd[4]
            #print(name, resource, table, func, mth)
            if mth is None:
                print("INTERNAL ERROR: %s does not exist" % func)
                return

            mydb = MyConnectionBase()
            mydb.setResource(resourceName=resource)
            ok = mydb.openConnection()
            if not ok:
                print("ERROR: Could not open resource %s" % resource)
                return

            rc = mth(mydb._dbCon, table)
            if rc:
                print("About to load schema for %s" % table)
                self.prettyprintcommands(commands)
                if not self.__noop:
                    myq = MyDbQuery(dbcon=mydb._dbCon)
                    ret = myq.sqlCommand(commands)
                    if not ret:
                        print("ERROR CREATING TABLE %s" % table)

                        
            mydb.closeConnection()


    def prettyprintcommands(self, cmds):
        for c in cmds:
            print(c)

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

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            defpath = self.__ci_common.get_wf_defs_path()

        for taskid, fname in self.__wftasks:

            fpath = os.path.join(defpath, fname)

            if not os.path.exists(fpath):
                # print("Skipping %s as does not exist" % fname)
                continue

            myq = MyDbQuery(dbcon=mydb._dbCon)
            query = "select wf_class_id from wf_class_dict where wf_class_id='{}'".format(taskid)

            rows = myq.selectRows(queryString=query)
            if len(rows) == 0:
                print("About to install WF schema %s with name %s" % (taskid, fname))
                cmd = 'python -m wwpdb.apps.wf_engine.wf_engine_utils.tasks.WFTaskRequestExec --verbose --load_wf_def_file={}'.format(fname)
                self.__exec(cmd)
                
        mydb.closeConnection()

    def update_compat_wf_tasks(self):
        """Handle management of the V5.26 error class dictionary"""
        print("")
        print("Checking V5.26 compat WF scheme status DB")

        wftr = WFTaskRequest(verbose = True)
        for setD in self.__compat_dict:
            wfClassId = setD[0]
            # returns list of len 0
            c = wftr.getWfClassDict(wfClassId=wfClassId)
            if len(c) == 0:
                print("About to install compat WF schema %s" % wfClassId)
                if not self.__noop:
                    wfOpts = {}
                    wfOpts["wfClassId"] = wfClassId
                    wfOpts["wfClassName"] = setD[1]
                    wfOpts["title"] = setD[2]
                    wfOpts["author"] = setD[3]
                    wfOpts["version"] = setD[4]
                    wfOpts["classFile"] = setD[5]

                    status = wftr.addWfClassDict(**wfOpts)
                    if not status:
                        print("FAILED")

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

    def _nottableexists(self, dbconn, table):
        """Checks if table does not exist. Returns True if does not exist"""
        myq = MyDbQuery(dbcon=dbconn)
        query = "SELECT count(*) FROM information_schema.TABLES WHERE TABLE_NAME = '{}'  AND TABLE_SCHEMA in (SELECT DATABASE())".format(table)

        rows = myq.selectRows(queryString=query)
        if len(rows) != 1:
            return True
        val = rows[0][0]
        if val == 0:
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

        self.__loaddaintschema()

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

    def __loaddaintschema(self):
        """load da_internal schema from configuration"""
        # schemapath = self.__ci.get('SITE_DA_INTERNAL_SCHEMA_PATH')

        # Handle deprecated warning
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            schemapath = self.__ci_common.get_site_da_internal_schema_path()

        if not schemapath:
            print("ERROR: SITE_DA_INTERNAL_SCHEMA_PATH not in site-config")
            return False

        if not len(self.__daintschema):
            with open(schemapath, 'r') as fin:
                prd = PdbxReader(fin)
                self.__daintschema = []
                prd.read(containerList = self.__daintschema, selectList=['rcsb_attribute_def', 'rcsb_table_abbrev'])


    def _dainttablenotexists(self, dbconn, table):
        """Return True if _rcsb_attribute_def.table_name == table and table does not exist
        Handles alias
        """

        self.__loaddaintschema()

        block = self.__daintschema[0]


        # Handle aliases
        orig_table = table
        tb = block.getObj('rcsb_table_abbrev')
        if tb:
            for row in range(tb.getRowCount()):
                table_name = tb.getValue('table_name', row)
                table_abbrev = tb.getValue('table_abbrev', row)
                if table_abbrev == table:
                    # print("Switch table %s to %s" %(table, table_name))
                    table = table_name
                    break

        tb = block.getObj('rcsb_attribute_def')
        if not tb:
            print("ERROR: Schema file does not contain rcsb_attribute_def")
            return False
        found = False
        for row in range(tb.getRowCount()):
            table_name = tb.getValue('table_name', row)
            if table_name == table:
                found = True
                break

        if not found:
            # schema mapping file does not list - we should not need to add
            # print("{} not found -- skipping".format(table))
            return False


        ret = self._nottableexists(dbconn, orig_table)

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
    if 'func' not in args:
        parser.print_usage()
        sys.exit(1)

    dbs = DbSchemaManager(args.noop)


    if args.func == 'update':
       dbs.updateschema()
       dbs.updatewftasks()
       dbs.update_compat_wf_tasks()
    elif args.func=='finalcheck':
        dbs.checkviews()

if __name__ == '__main__':
    main()
