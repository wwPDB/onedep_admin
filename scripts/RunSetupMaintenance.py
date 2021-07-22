from wwpdb.apps.chem_ref_data.utils.ChemRefDataDbExec import ChemRefDataDbExec
from wwpdb.apps.deposit.depui.taxonomy.loadTaxonomyFromFTP import run_process as depui_taxonomy_update
from wwpdb.apps.site_admin.UpdateMmcifDictionary import update_mmcif_dictionary
from wwpdb.apps.site_admin.UpdateReferenceSequence import SequenceUpdates
from wwpdb.apps.site_admin.UpdateTaxonomyFiles import update_taxonomy_data


def update_ligand_info():
    crx = ChemRefDataDbExec(sessionId=None)
    crx.doCheckoutChemComp()
    crx.doCheckoutPRD()
    crx.doLoadChemCompMulti(numProc=8)
    crx.doLoadBird()
    crx.doUpdateSupportFiles()


def run_setup_maintenance():
    update_mmcif_dictionary()
    update_taxonomy_data()
    update_ligand_info()
    depui_taxonomy_update(write_sql=True)
    SequenceUpdates().run_process()


if __name__ == '__main__':
    run_setup_maintenance()
