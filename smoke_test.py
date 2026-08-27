import sqlite3
import tempfile
import zipfile
from pathlib import Path

from app import export_database

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "unicode-test.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE "测试表" (id INTEGER, text_value TEXT, blob_value BLOB)')
    conn.execute('INSERT INTO "测试表" VALUES (?, ?, ?)', (1, "中文🙂 UTF-8", b"\x00\xff"))
    conn.commit()
    conn.close()

    xlsx = export_database(db)
    assert xlsx.exists(), "XLSX was not created"

    with zipfile.ZipFile(xlsx) as zf:
        xml = b"\n".join(
            zf.read(name)
            for name in zf.namelist()
            if name.endswith(".xml")
        )
        assert "中文".encode("utf-8") in xml, "Unicode text missing from XLSX"
        assert b"00FF" in xml, "BLOB hex text missing from XLSX"

print("Smoke test passed")
