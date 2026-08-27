import os
import re
import sqlite3
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import xlsxwriter

EXCEL_MAX_ROWS = 1_048_576
DATA_ROWS_PER_SHEET = EXCEL_MAX_ROWS - 1
EXCEL_MAX_CELL_CHARS = 32_767
INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def unique_sheet_name(raw_name: str, used: set[str], part: int = 1) -> str:
    cleaned = INVALID_SHEET_CHARS.sub("_", raw_name).strip("'") or "Sheet"
    suffix = "" if part == 1 else f"_{part}"
    max_base = 31 - len(suffix)
    base = cleaned[:max_base]
    candidate = base + suffix
    counter = 2
    while candidate.casefold() in used:
        extra = f"_{counter}"
        candidate = base[: 31 - len(suffix) - len(extra)] + suffix + extra
        counter += 1
    used.add(candidate.casefold())
    return candidate


def normalize_value(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.hex().upper()
    if isinstance(value, str):
        return value[:EXCEL_MAX_CELL_CHARS]
    return value


def export_database(db_path: Path) -> Path:
    output_path = db_path.with_suffix(".xlsx")
    temp_path = output_path.with_name(output_path.name + ".tmp")

    if temp_path.exists():
        temp_path.unlink()

    conn = sqlite3.connect(str(db_path))
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")

    workbook = xlsxwriter.Workbook(
        str(temp_path),
        {
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )

    used_sheet_names: set[str] = set()
    header_format = workbook.add_format({"bold": True})

    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]

        if not tables:
            ws = workbook.add_worksheet("Info")
            ws.write(0, 0, "No user tables found in this SQLite database.")

        for table_name in tables:
            cursor = conn.execute(f"SELECT * FROM {quote_identifier(table_name)}")
            headers = [col[0] for col in cursor.description]
            part = 1
            worksheet = None
            excel_row = 0

            def start_sheet(current_part: int):
                nonlocal excel_row
                sheet_name = unique_sheet_name(table_name, used_sheet_names, current_part)
                ws = workbook.add_worksheet(sheet_name)
                for col_idx, header in enumerate(headers):
                    ws.write(0, col_idx, str(header), header_format)
                ws.freeze_panes(1, 0)
                excel_row = 1
                return ws

            worksheet = start_sheet(part)

            while True:
                rows = cursor.fetchmany(5000)
                if not rows:
                    break

                for row in rows:
                    if excel_row >= EXCEL_MAX_ROWS:
                        part += 1
                        worksheet = start_sheet(part)

                    for col_idx, value in enumerate(row):
                        value = normalize_value(value)
                        if value is None:
                            continue
                        worksheet.write(excel_row, col_idx, value)
                    excel_row += 1

        workbook.close()
        workbook = None
        conn.close()
        conn = None
        os.replace(temp_path, output_path)
        return output_path

    except Exception:
        if workbook is not None:
            try:
                workbook.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        raise


class ExportApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SQLite → XLSX")
        self.root.geometry("520x165")
        self.root.resizable(False, False)

        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="SQLite → XLSX", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        self.status = tk.StringVar(value="请选择 SQLite 数据库文件")
        ttk.Label(frame, textvariable=self.status, wraplength=480).pack(anchor="w", pady=(12, 8))

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 10))

        self.choose_button = ttk.Button(frame, text="选择数据库并导出", command=self.choose_files)
        self.choose_button.pack(anchor="e")

        self.root.after(100, self.choose_files)

    def choose_files(self):
        files = filedialog.askopenfilenames(
            title="选择 SQLite 数据库",
            filetypes=[
                ("SQLite 数据库", "*.db *.sqlite *.sqlite3"),
                ("所有文件", "*.*"),
            ],
        )
        if not files:
            return

        self.choose_button.config(state="disabled")
        self.progress.start(12)
        paths = [Path(p) for p in files]
        threading.Thread(target=self.run_export, args=(paths,), daemon=True).start()

    def run_export(self, paths: list[Path]):
        successes = []
        failures = []

        for index, path in enumerate(paths, start=1):
            self.root.after(
                0,
                lambda i=index, n=len(paths), p=path: self.status.set(
                    f"正在导出 {i}/{n}: {p.name}"
                ),
            )
            try:
                output = export_database(path)
                successes.append(output)
            except Exception as exc:
                failures.append((path, exc, traceback.format_exc()))

        self.root.after(0, lambda: self.finish(successes, failures))

    def finish(self, successes, failures):
        self.progress.stop()
        self.choose_button.config(state="normal")

        if failures:
            lines = []
            if successes:
                lines.append(f"成功导出 {len(successes)} 个数据库。")
            lines.append(f"失败 {len(failures)} 个：")
            for path, exc, _ in failures[:8]:
                lines.append(f"- {path.name}: {exc}")
            if len(failures) > 8:
                lines.append("……")
            self.status.set("导出完成，但有失败项目")
            messagebox.showerror("导出结果", "\n".join(lines))
        else:
            self.status.set(f"完成：已导出 {len(successes)} 个 XLSX")
            output_lines = "\n".join(str(p) for p in successes[:10])
            if len(successes) > 10:
                output_lines += "\n……"
            messagebox.showinfo(
                "导出完成",
                f"已成功导出 {len(successes)} 个数据库。\n\n文件保存在数据库同级目录：\n{output_lines}",
            )

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ExportApp().run()
