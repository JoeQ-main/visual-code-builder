"""Left-hand side tabs: the reserved-word palette and the file explorer."""
from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import model

BUTTON_MIN_W = 78   # pixels per column before the grid reflows
MAX_COLUMNS = 12
GRID_MARGIN = 12


def columns_for_width(width: int) -> int:
    return max(1, min(MAX_COLUMNS, (width - GRID_MARGIN) // BUTTON_MIN_W))


def single_column_width() -> int:
    """The widest panel width that still lays the buttons out in one column."""
    return GRID_MARGIN + 2 * BUTTON_MIN_W - 1


class ButtonPalette(ttk.Frame):
    """A grid of buttons that add blocks when clicked.

    ``sections`` is a list of ``(title, entries)``; every entry has a
    ``word`` for the button text. The grid reflows to as many columns as
    fit the panel width.
    """

    def __init__(self, master, intro, sections, on_pick, on_hint, hint_for):
        super().__init__(master, padding=6)
        self.on_pick = on_pick
        self.on_hint = on_hint
        self.hint_for = hint_for
        self._cols = 0
        self._grids: list[tuple[ttk.Frame, list[ttk.Button]]] = []
        self._intro = ttk.Label(self, text=intro, wraplength=200, justify="left",
                                foreground="#555")
        self._intro.pack(anchor="w", pady=(0, 6))
        for title, entries in sections:
            self._section(title, entries)
        self.bind("<Configure>", self._on_resize)
        self._reflow(3)

    def _section(self, title, entries):
        ttk.Label(self, text=title, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", pady=(6, 2))
        grid = ttk.Frame(self)
        grid.pack(fill="x")
        buttons = []
        for entry in entries:
            btn = ttk.Button(grid, text=entry.word, width=8,
                             command=lambda e=entry: self.on_pick(e))
            hint = self.hint_for(entry)
            btn.bind("<Enter>", lambda e, h=hint: self.on_hint(h))
            btn.bind("<Leave>", lambda e: self.on_hint(""))
            buttons.append(btn)
        self._grids.append((grid, buttons))

    def _on_resize(self, event):
        width = event.width
        self._intro.configure(wraplength=max(80, width - 16))
        self._reflow(columns_for_width(width))

    def _reflow(self, cols: int):
        if cols == self._cols:
            return
        self._cols = cols
        for grid, buttons in self._grids:
            for i in range(MAX_COLUMNS):
                grid.columnconfigure(i, weight=1 if i < cols else 0)
            for n, btn in enumerate(buttons):
                btn.grid(row=n // cols, column=n % cols, sticky="ew", padx=1, pady=1)


def keyword_hint(entry: model.PaletteEntry) -> str:
    article = "an" if entry.kind[0] in "aeiou" else "a"
    hint = entry.note or f"Add {article} {entry.kind} block."
    doc = model.block_doc(entry.kind)
    return f"{hint}  {doc}" if doc else hint


def function_hint(entry: model.FunctionEntry) -> str:
    result = f" → {entry.result}" if entry.result else ""
    return f"{entry.word}({entry.params}){result}: {entry.doc}"


class PalettePanel(ButtonPalette):
    """One button per reserved word, plus the other statement kinds."""

    def __init__(self, master, on_pick, on_hint):
        super().__init__(
            master,
            "Click a word to add its block. With a container selected, the block "
            "goes inside it.",
            [("Reserved words", model.PALETTE), ("Other statements", model.PALETTE_EXTRA)],
            on_pick, on_hint, keyword_hint)


class FunctionPalette(ButtonPalette):
    """The 30 most common built-in functions as ready-made blocks."""

    def __init__(self, master, on_pick, on_hint):
        super().__init__(
            master,
            "Click a function to add a block that calls it. Functions that return "
            "a value get an assignment block; print gets a call block.",
            [("Built-in functions", model.FUNCTIONS)],
            on_pick, on_hint, function_hint)


class FileExplorer(ttk.Frame):
    """A lazy tree of folders and Python files. Double-click a file to open it."""

    def __init__(self, master, on_open):
        super().__init__(master)
        self.on_open = on_open
        bar = ttk.Frame(self, padding=4)
        bar.pack(fill="x")
        ttk.Button(bar, text="Drives", command=self.show_drives).pack(side="left")
        ttk.Button(bar, text="Home", command=self.show_home).pack(side="left", padx=2)
        ttk.Button(bar, text="Folder…", command=self.choose_folder).pack(side="left")
        self.tree = ttk.Treeview(self, show="tree", selectmode="browse")
        ysb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewOpen>>", self._on_open_node)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", self._on_double)
        self.show_home()

    # roots -------------------------------------------------------------
    def set_roots(self, paths):
        self.tree.delete(*self.tree.get_children())
        for p in paths:
            self._add_node("", Path(p), label=str(p))

    def show_drives(self):
        if hasattr(os, "listdrives"):
            drives = os.listdrives()
        else:
            drives = [os.path.abspath(os.sep)]
        self.set_roots(drives)

    def show_home(self):
        self.set_roots([Path.home()])

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Browse folder")
        if folder:
            self.set_roots([folder])

    # nodes -------------------------------------------------------------
    def _add_node(self, parent, path: Path, label=None):
        is_dir = path.is_dir()
        node = self.tree.insert(parent, "end", text=label or path.name,
                                values=(str(path), "dir" if is_dir else "file"),
                                open=False)
        if is_dir:
            self.tree.insert(node, "end", text="loading…", values=("", "dummy"))
        return node

    def _populate(self, node):
        children = self.tree.get_children(node)
        if not (len(children) == 1 and self.tree.item(children[0], "values")[1] == "dummy"):
            return
        self.tree.delete(*children)
        path = Path(self.tree.item(node, "values")[0])
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            self.tree.insert(node, "end", text="(not accessible)", values=("", "info"))
            return
        for p in entries:
            try:
                if p.is_dir():
                    if not p.name.startswith("."):
                        self._add_node(node, p)
                elif p.suffix.lower() in (".py", ".pyw", ".pyi"):
                    self._add_node(node, p)
            except OSError:
                continue

    def _on_open_node(self, event):
        node = self.tree.focus()
        if node:
            self._populate(node)

    def _on_double(self, event):
        node = self.tree.focus()
        if not node:
            return
        path, kind = self.tree.item(node, "values")[:2]
        if kind == "file":
            self.on_open(path)
