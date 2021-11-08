import sys
import os
import traceback
import subprocess

from wwpdb.utils.config.ConfigInfo import ConfigInfo
from wwpdb.utils.config.ConfigInfoApp import ConfigInfoAppCommon

def get_config(var):
    ci = ConfigInfo()
    valsect = ci.get("VALIDATION_SERVICES")
    if valsect is not None and var in valsect:
        return valsect[var]
    else:
        return ci.get(var)

def runCmd(cmd):
    """ Run back-end command using subprocess
    """

    retcode = -1
    try:
        retcode = subprocess.call(cmd, shell=True)
        if retcode != 0:
            sys.stderr.write("Command '%s' completed with return code %r\n" % (cmd, retcode))
            #
    except OSError as e:
        sys.stderr.write("Command '%s' failed  with exception %r\n" % (cmd, str(e)))
    except:
        traceback.print_exc(file=sys.stderr)

    return retcode

def main():

    csds_config = get_config("VALIDATION_CSDS_CONFIG_DIR")
    csds_python = get_config("CSDS_PYTHON_PATH")
    if csds_python is None:
        print("CSDS_PYTHON_PATH not set")
        sys.exit(1)


    # A script to get version
    pid = os.getpid()
    versionInfoPath = "/tmp/mogul_version_%s.txt" % pid
    versionLogPath =  "/tmp/mogul_version_%s.log" % pid
    versionApiPath = "/tmp/mogul_version_api_%s.py" % pid
    fth = open(versionApiPath, "w")
    fth.write("import sys\n")
    fth.write("import ccdc.conformer\n")
    fth.write("import ccdc.io\n#\n")
    fth.write("fth = open(\"" + versionInfoPath + "\", \"w\")\n")
    fth.write("fth.write(\"ccdc_version=%s\\n\" % ccdc.conformer._mogul_version())\n")
    fth.write("fth.write(\"csd_version=%s\\n\" % ccdc.io.csd_version())\n")
    fth.write("fth.write(\"csd_directory=%s\\n\" % ccdc.io.csd_directory())\n")
    fth.write("fth.close()\n")
    fth.write("sys.exit(0)\n")
    fth.close()


    setting = ""
    
    if csds_config is not None:
        setting += "export XDG_CONFIG_HOME=%s ; " % os.path.abspath(csds_config)
    #

    cmd = setting + os.path.abspath(csds_python) + " < " + versionApiPath + " > " + versionLogPath + " 2>&1; "

    retcode = runCmd(cmd)
    if retcode:
        print("ERROR: There is a an issue with licensing.  Look at %s to see more" % versionLogPath)
        sys.exit(1)
    else:
        print("Licensing and python API works")
        sys.exit(0)

if __name__ == "__main__":
    main()

