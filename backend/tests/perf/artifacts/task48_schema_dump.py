import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parent / "task48-world.db"
con = sqlite3.connect(str(db))
for name in ("attempt", "attempt_item"):
    for row in con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)):
        print(f"===== {name} =====")
        print(row[0])
for row in con.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type='index' AND tbl_name='attempt'"):
    print("INDEX:", row)
