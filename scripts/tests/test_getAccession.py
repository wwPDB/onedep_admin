#!/usr/bin/env python

"""Script to test that retrieving accession codes works"""

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
    

def main():
    dbcon = MyConnectionBase()
    dbcon.setResource("STATUS")

    ret = dbcon.openConnection()

    if ret is False:
        print("Could not open database connection")
        sys.exit(1)

    print("Database opened\n")

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


    # Close
    dbcon.closeConnection()

if __name__ == "__main__":
    main()

