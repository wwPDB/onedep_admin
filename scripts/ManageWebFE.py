#!/usr/bin/env python

"""A small script to help ensure proper versions of WebFE code have been checked out with git"""

import argparse
import json
import os
import sys

class WebFEVersions(object):
    def __init__(self, topdir):
        self.__topdir = topdir
        self._getversions()

    def _getversions(self):
        self._versions = {}

        # Get list of directories
        basedir=os.path.join('webapps', 'htdocs')
        dlist = os.listdir(os.path.join(self.__topdir, basedir))

        dirs = ['.']
        for d in dlist:
            if d in ['assets']:
                continue
            if os.path.isdir(os.path.join(self.__topdir, basedir, d)):
                dirs.append(os.path.join(basedir, d))

        for d in dirs:
            self._versions[d] = self.__getversion(os.path.join(self.__topdir, d))


    def __getversion(self, dirpath):
        fullpath=os.path.abspath(dirpath)

        versfile = os.path.join(fullpath, 'version.json')
        if not os.path.exists(versfile):
            versfile = os.path.join(fullpath, 'VERSION.json')
            if not os.path.exists(versfile):
                print("Could not find version file in %s" % fullpath)
                return None

        ret = None
        with open(versfile, 'r') as fp:
            vdict = json.load(fp)
            ret = vdict['Version']

        return ret

    def __writerequirements(self, fp):
        for k in sorted(self._versions.keys()):
            fp.write("%s==%s\n" % (k, self._versions[k]))

    def list(self):
        self.__writerequirements(sys.stdout)

    def freeze(self, requirements):
        with open(requirements, 'w') as fout:
            self.__writerequirements(fout)

    def check(self, requirements):
        ok = True
        ref = {}
        with open(requirements, 'r') as fin:
            for line in fin:
                [prog, vers] = line.strip().split('==')
                ref[prog.strip()] = vers.strip()

        refkeys = set(ref.keys())
        verskeys = set(self._versions.keys())
        extrakeys = refkeys - verskeys

        if len(extrakeys) > 0:
            print("The following subdirs are missing")
            ok = False
            for k in extrakeys:
                print("%s   %s" % (k, ref[k]))

        head = False
        for k in sorted(ref.keys()):
            if k not in self._versions:
                # Already listed above
                continue
            if ref[k] != self._versions[k]:
                if not head:
                    print("The following versions are incorrect")
                    head = True
                    ok = False
                print("Mismatch %s   %s != %s" % (k, ref[k], self._versions[k]))

        if ok:
            sys.exit(0)
        else:
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--webroot", required=True, help='Top of checked out root')
    subparsers = parser.add_subparsers(help='sub-command help')
    sub_a = subparsers.add_parser('list', help='list installed webfe packages')
    sub_a.set_defaults(func="list")

    sub_b = subparsers.add_parser('freeze', help='freeze list of installed webfe packages')
    sub_b.add_argument("-r", "--requirements", help='refquirements file to write', required=True)
    sub_b.set_defaults(func="freeze")

    sub_c = subparsers.add_parser('check', help='check list of installed webfe packages vs requirements')
    sub_c.add_argument("-r", "--requirements", help='refquirements file to write', required=True)
    sub_c.set_defaults(func="check")

    args = parser.parse_args()
    wfe = WebFEVersions(topdir=args.webroot)

    if args.func == 'list':
        wfe.list()
    elif args.func=='freeze':
        wfe.freeze(args.requirements)
    elif args.func == 'check':
        wfe.check(args.requirements)




if __name__ == '__main__':
    main()