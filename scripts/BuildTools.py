#!/usr/bin/env python

"""A small script to build tools"""

import argparse
import json
import os
import sys
import shutil
import subprocess
import tempfile
import time
try:
    from ConfigParser import ConfigParser, NoOptionError
except ImportError:
    from configparser import ConfigParser, NoOptionError

from wwpdb.utils.config.ConfigInfo import ConfigInfo


class BuildTools(object):
    def __init__(self, config_file, noop):
        self.__configfile = config_file
        self.__noop = noop

        # Infer topdir from where running from
        topdir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        cdict = {'topdir': topdir}

        self.__cparser = ConfigParser(cdict)
        self.__cparser.read(self.__configfile)
        self.__ci = ConfigInfo()
        pass

    def __exec(self, cmd, overridenoop=False):
        print(cmd)
        ret = 0
        if not self.__noop or overridenoop:
            ret = subprocess.call(cmd, shell=True)
        return ret

    def build(self):
        try:
            packages = self.__cparser.get('DEFAULT', 'buildupdates')
        except:
            # Missing stanza - exit
            return

        packs = packages.split(',')
        for pack in packs:
            p = pack.strip()
            pbuild = "pkg_build_{}".format(p)
            print("About to build %s" % pbuild)
            self.run_build_command(pbuild=pbuild)

    def run_build_command(self, pbuild, build_version='v-3300'):
        onedep_build_dir = self.__ci.get('WWPDB_ONEDEP_BUILD')
        onedep_build_dir_version = os.path.join(onedep_build_dir, build_version)
        #distrib_dir = os.environ.get('DISTRIB_DIR', None)

        cmd = ['#!/bin/bash']

        #if distrib_dir:
        #    if os.path.exists(distrib_dir):
        #        cmd.append('rm -rf ${DISTRIB_DIR}/*')
        #    else:
        #        os.makedirs(distrib_dir)
        #else:
        #    print('DISTRIB_DIR not defined - exiting')
        #    sys.exit(1)

        cmd.append('cd {}'.format(onedep_build_dir))
        cmd.append('git pull')
        cmd.append('. {}/utils/pkg-utils-v2.sh'.format(onedep_build_dir))
        cmd.append('get_environment')
        cmd.append('export FORCE_REBUILD="YES"')
        cmd.append('. {}/packages/all-packages.sh'.format(onedep_build_dir_version))

        cmd.append(pbuild)

        working_dir = tempfile.mkdtemp()
        temp_file = os.path.join(working_dir, 'cmd.sh')
        print('writing out commands to: {}'.format(temp_file))

        with open(temp_file, 'w') as outFile:
            for command in cmd:
                outFile.write(command)
            #outFile.writelines(cmd)

        #cmd_string = '; '.join(cmd)
        #return self.__exec(cmd_string)



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help='Configuration file for release')
    parser.add_argument("--noop", "-n", default=False, action='store_true', help='Do not carry out actions')

    args = parser.parse_args()
    bt = BuildTools(args.config, args.noop)
    bt.build()
    return 0

if __name__ == '__main__':
    main()
