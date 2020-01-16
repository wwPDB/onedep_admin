#!/usr/bin/env python

"""A small script to build tools"""

import argparse
import json
import os
import sys
try:
    from ConfigParser import ConfigParser, NoOptionError
except ImportError:
    from configparser import ConfigParser, NoOptionError

class BuildTools(object):
    def __init__(self, config_file, noop):
        self.__configfile = config_file
        self.__noop = noop

        # Infer topdir from where running from
        topdir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        cdict = {'topdir': topdir}

        self.__cparser = ConfigParser(cdict)
        self.__cparser.read(self.__configfile)
        pass

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
            if self.__noop:
                print("Would build %s" % pbuild)
            else:
                print("About to build %s" % pbuild)
                # XXXXX


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
