import os
import warnings
from wwpdb.utils.config.ConfigInfoApp import ConfigInfoAppDepUI

# Disable legacy warning
with warnings.catch_warnings(record=True) as _w:  # noqa: F841

    ciad = ConfigInfoAppDepUI()
    jpath = ciad.get_site_dataset_siteloc_file_path()
    aok = os.access(jpath, os.R_OK)

    print("Dataset location file: %s" % jpath)
    print("Access ok? %s" % aok)


