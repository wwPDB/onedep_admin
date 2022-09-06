#!/usr/bin/env python

"""Utility to help finalize transfer of a session so seen by WFM

   The rules:  For RCSB, PDBJ, PDBC.   The deposit site in the status DB must be set to current site
               For PDBE: The depositor in the model file must be set to PDBE. dbload must take place.
               For all:  Fix processing site in pdbx_database_status
               Update the latest model file (V1) so reset to origin will work

"""


import os
import sys
import argparse
from wwpdb.utils.config.ConfigInfo import ConfigInfo, getSiteId
from wwpdb.utils.wf.dbapi.WfDbApi import WfDbApi
from wwpdb.io.locator.PathInfo import PathInfo
from mmcif.io.IoAdapterCore import IoAdapterCore
from wwpdb.utils.db.StatusLoadWrapper import StatusLoadWrapper


import logging

FORMAT = '[%(levelname)s]-%(module)s.%(funcName)s: %(message)s'
logging.basicConfig(format=FORMAT)
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger2 = logging.getLogger("mmcif.io")
logger2.setLevel(logging.INFO)
logger2 = logging.getLogger("wwpdb.io.locator")
logger2.setLevel(logging.INFO)


def SetWFDepSite(ds, siteName):
    print("Updating status.deposition deposit_site for %s" % ds)
    wfApi = WfDbApi(verbose=True)
    sql = (
        "update deposition set deposit_site = '%s' where dep_set_id = '%s'"
                % (siteName, ds)
    )
    nrow = wfApi.runUpdateSQL(sql)
    print("   In ds %s updated site to %s #rows %s" % (ds, siteName, nrow))

def SetWFDepAnnotator(ds, ann):
    print("Updating status.deposition annotator_initials for %s" % ds)
    wfApi = WfDbApi(verbose=True)
    sql = (
        "update deposition set annotator_initials = '%s' where dep_set_id = '%s'"
                % (ann, ds)
    )
    nrow = wfApi.runUpdateSQL(sql)
    print("   In ds %s annotator initials to %s #rows %s" % (ds, ann, nrow))



def UpdateModel(ds, siteName):
    print("Updating model processing site for %s" % ds)
    siteId = getSiteId()
    pI = PathInfo(siteId, verbose=False)
    modelPath = pI.getModelPdbxFilePath(dataSetId=ds, fileSource="archive", versionId="latest")

    io = IoAdapterCore()

    cL = io.readFile(modelPath)
    if cL is None or len(cL) < 1:
        print("%s could not be parsed" % modelPath)
        sys.exit(1)

    b0 = cL[0]

    cat = "pdbx_database_status"
    cobj = b0.getObj(cat)

    # If category does not exist
    if cobj is None:
        print("Could not find %s" % cat)
        sys.exit(1)

    cobj.setValue(siteName, "process_site", 0)

    # PDBE requires annotator set
    if siteName == "PDBE":
        cobj.setValue("EBI", "pdbx_annotator", 0)


    # modelPath="/tmp/a.cif"
    ret = io.writeFile(modelPath, cL)
    if not ret:
        print("Writing model failed error %s", ret)
        sys.exit(1)

def StatusLoad(ds):
    siteId = getSiteId()
    slw = StatusLoadWrapper(siteId=siteId, verbose=True, log=sys.stderr)
    slw.dbLoad(depSetId=ds, fileSource='archive', versionId='latest', mileStone=None)

def checkUsername():
    # For all but PDBe, need to be wwwdev to modify files

    # When you sudo - USERNAME will be root, os.getlogin() is original user.
    user = os.environ["USER"]
    allowed = ["wwwdev"]  # , "wwpdbdev"]

    if user not in allowed:
        print("Must be logged in as %s" % allowed)
        sys.exit(1)

def main():

    
    parser = argparse.ArgumentParser()
    parser.add_argument("--sitename", dest="siteName", default=None, choices=['PDBE', 'RCSB', 'PDBJ', 'PDBC'], help='Processing site')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dataset", dest="dataSetId", default=None, help="Data set identifier (e.g. D_0000000000)")
    group.add_argument("--dataset_file", dest="dataSetIdFile", default=None, help="File containing a list of data sets one per line")


    args = parser.parse_args()

    print(args)

    if args.siteName is None:
        cI = ConfigInfo()
        siteName = cI.get("SITE_NAME")
    else:
        siteName = args.siteName

    print("Proc site is %s" % siteName)

    if args.dataSetIdFile:
        try:
            with open(args.dataSetIdFile, "r") as ifh:
                dsL = []
                for line in ifh:
                    dsL.append(str(line[:-1]).strip())
        except Exception as e:
            sys.stderr.write("main() read failed for %r %r\n" % (args.dataSetIdFile, str(e)))
    elif args.dataSetId:
        dsL = [args.dataSetId]

    print("Depids: ", dsL)

    for ds in dsL:
        if siteName in ['PDBJ', 'RCSB', 'PDBC']:
            checkUsername()
            SetWFDepSite(ds, siteName)

        if siteName in ['PDBE']:
            SetWFDepAnnotator(ds, 'EBI')

        # Everyone update model file
        UpdateModel(ds, siteName)
        StatusLoad(ds)

if __name__ == '__main__':
    main()

