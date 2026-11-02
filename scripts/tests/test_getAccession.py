#!/usr/bin/env python

"""Script to test that retrieving accession codes works"""

import argparse
import sys

from wwpdb.utils.db.MyConnectionBase import MyConnectionBase

def count_accession(dbcon, table) -> int:
    sql = f"select count(1) from {table} where used = 'n'"

    cursor = dbcon.getCursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    cursor.close()

    assert len(rows) == 1
    return rows[0][0]

def get_accession(dbcon, table, codecol) -> str:
    sql = f"call getAccession('{codecol}', '{table}')"

    cursor = dbcon.getCursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    cursor.close()

    assert len(rows) == 1
    return rows[0][0]
    

def test_bmrb(dbcon):
    table = "bmrbID"
    codecol = "bmrb_id"

    startcount = count_accession(dbcon, table)
    print(f"{table} has {startcount} ids available")

    # Start a transaction

    print("Starting transaction")
    session = dbcon.getConnection().driver_connection
    session.begin()

    # Get a BMRB id

    idcode = get_accession(dbcon, table, codecol)
    print(f"{table} code is {idcode}")

    endcount = count_accession(dbcon, table)
    print(f"{table} has {endcount} ids available after getting accession")
    assert(endcount == startcount - 1)

    # Rollback
    print("\nPerforming rollback of DB\n")
    session.rollback()

    endcount2 = count_accession(dbcon, table)
    print(f"{table} has {endcount2} ids available after rollback")
    assert(endcount2 == startcount)


def test_dangerous(dbcon):

    print("\n--------  Extended PDB id support ----\n")

    table = "pdbID"
    codecol = "pdb_id"
    extpdb = "pdb_12345678"

    startcount = count_accession(dbcon, table)
    print(f"{table} has {startcount} ids available")

    # Start a transaction

    print("Starting transaction")
    session = dbcon.getConnection().driver_connection
    session.begin()

    # Update all pdb ids
    sql = f"update {table} set used='y' where used='n'"
    cursor = dbcon.getCursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    cursor.close()

    sql = f"insert into {table} ({codecol}, used) VALUES('{extpdb}', 'n')"
    cursor = dbcon.getCursor()
    cursor.execute(sql)
    rows = cursor.fetchall()
    cursor.close()


    # Get a pdb id
    idcode = get_accession(dbcon, table, codecol)
    print(f"{table} code is {idcode}")

    if idcode != extpdb:
        print("!!!!DANGER!!!!!")
        print("extended PDB id truncated")
        print("!!!!DANGER!!!!!\n")
    else:
        print("Extended PDB ids support seems to work")

    # Rollback
    print("\nPerforming rollback of DB\n")
    session.rollback()

    endcount2 = count_accession(dbcon, table)
    print(f"{table} has {endcount2} ids available after rollback")
    assert(endcount2 == startcount)




def main():

    parser = argparse.ArgumentParser(prog='test_getAccession',
                    description="Tests that the mysql procedure works in non-destructive manner")

    parser.add_argument('--dangerous', action='store_true', help="Dangerous test to ensure that extended pdb is handled properly")

    args = parser.parse_args()
 
    dbcon = MyConnectionBase()
    dbcon.setResource("STATUS")

    ret = dbcon.openConnection()

    if ret is False:
        print("Could not open database connection")
        sys.exit(1)

    print("Database opened\n")

    test_bmrb(dbcon)

    if args.dangerous:
        test_dangerous(dbcon)

    # Close
    dbcon.closeConnection()

if __name__ == "__main__":
    main()

