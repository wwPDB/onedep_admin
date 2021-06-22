#!/usr/bin/env python

"""A script to ensure that status and da_internal DB have proper schema.  This is separate from update script as needs to load fresh if pip install mysqlclient updates version
"""

try:
    from ConfigParser import ConfigParser, NoOptionError
except ImportError:
    from configparser import ConfigParser, NoOptionError
import os
import argparse
import subprocess

from wwpdb.utils.config.ConfigInfo import ConfigInfo
from wwpdb.utils.db.MyConnectionBase import MyConnectionBase
from wwpdb.utils.db.MyDbUtil import MyDbQuery


class TaxDbManager(object):
    """A class to manage updates to the various schemas"""
    def __init__(self, taxdbsize, maxsize, noop, useftp):
        self.__noop = noop
        self.__taxdbsize = taxdbsize
        self.__maxsize = maxsize
        self.__useftp = useftp
        self.__cI = ConfigInfo()

    def updatedb(self):
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

        if count >= self.__taxdbsize and count < self.__maxsize:
            print("Taxdb at least as big as expected")
            return

        if self.__useftp:
            command = "python -m wwpdb.apps.deposit.depui.taxonomy.loadTaxonomyFromFTP --write_sql"
        else:
            taxfile = self.__cI.get("TAXONOMY_FILE_NAME")
            if not taxfile:
                print("Could not find site-config TAXONOMY_FILE_NAME -- cannot load taxonomy")
                return

            command = "python -m wwpdb.apps.deposit.depui.taxonomy.loadData --input_csv {}".format(taxfile)
        self.__exec(command)


    def __exec(self, cmd):
        print(cmd)
        ret = 0
        if not self.__noop :
            ret = subprocess.call(cmd, shell=True)
        return ret



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--noop", required=False, help='Indicate what is needed but do not act',
                        action='store_true', default=False)
    parser.add_argument("--maxsize", required=True, type=int, help='Maximum size for taxdb to be before load')
    parser.add_argument("--taxdbsize", required=True, type=int, help='Minimum size size for taxdb to be before load')
    parser.add_argument("--ftpload", required=False, action='store_true', default=False, help='Use FTP to download taxdump')

    args = parser.parse_args()
    tdb = TaxDbManager(args.taxdbsize, args.maxsize, args.noop, args.ftpload)
    tdb.updatedb()


if __name__ == '__main__':
    main()
