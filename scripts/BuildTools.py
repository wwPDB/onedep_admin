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
    def __init__(self, config_file, noop, build_version='v-5200'):
        self.__configfile = config_file
        self.__noop = noop
        self.__build_version = build_version

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

        packs = packages.split(' ')
        for pack in packs:
            p = pack.strip()
            if p:
                pbuild = "pkg_build_{}".format(p)
                print("About to build %s" % pbuild)
                self.run_build_command(pbuild=pbuild)

    def run_build_command(self, pbuild):
        onedep_build_dir = self.__ci.get('WWPDB_ONEDEP_BUILD')
        onedep_build_dir_version = os.path.join(onedep_build_dir, self.__build_version)

        cmd = ['#!/bin/bash']

        # set the environment and ensure we are up to date
        cmd.append('cd {}'.format(onedep_build_dir))
        cmd.append('git pull')
        cmd.append('. {}/utils/pkg-utils-v2.sh'.format(onedep_build_dir))
        cmd.append('get_environment')
        cmd.append('export FORCE_REBUILD="YES"')
        cmd.append('. {}/packages/all-packages.sh'.format(onedep_build_dir_version))

        # clear out the existing distrib dir so files are re-fetched
        cmd.append('if [ -z "$DISTRIB_DIR" ]')
        cmd.append('then')
        cmd.append('echo "DISTRIB_DIR not defined - exiting"')
        cmd.append('exit 1')
        cmd.append('else')
        cmd.append('echo "remove files from distrib_dir so that they are rebuilt"')
        cmd.append('rm -rf ${DISTRIB_DIR}/*')
        cmd.append('fi')

        # append the actual command
        cmd.append(pbuild)

        # write everything to a temp file
        working_dir = tempfile.mkdtemp()
        temp_file = os.path.join(working_dir, 'cmd.sh')
        print('writing out commands to: {}'.format(temp_file))

        with open(temp_file, 'w') as outFile:
            outFile.write('\n'.join(cmd))

        print('commands to run')
        print('\n'.join(cmd))

        # run the temp file
        cmd_string = 'chmod +x {0}; {0}; rm -rf {1}'.format(temp_file, working_dir)
        return self.__exec(cmd_string)



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help='Configuration file for release')
    parser.add_argument("--build-version", help='Version of tools to build from', default='v-5200')
    parser.add_argument("--noop", "-n", default=False, action='store_true', help='Do not carry out actions')

    args = parser.parse_args()
    bt = BuildTools(config_file=args.config, noop=args.noop, build_version=args.build_version)
    bt.build()
    return 0

if __name__ == '__main__':
    main()
