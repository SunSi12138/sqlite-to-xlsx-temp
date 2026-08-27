# SQLite to XLSX

Windows GUI tool: select one or more SQLite database files and export each database to an XLSX file in the same directory.

- Supports `.db`, `.sqlite`, `.sqlite3`
- One workbook per database
- One or more sheets per table
- Unicode text preserved
- BLOB values exported as hexadecimal text
- Large tables are split automatically when an Excel worksheet row limit is reached

The Windows executable is built by GitHub Actions and published as a workflow artifact.
