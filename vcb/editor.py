"""Property panel for the selected block."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import model
from .model import Block

CHECK_FIELDS = {"async": "async", "star": "*"}
WRAP = 270


class EditorPanel(ttk.Frame):
    def __init__(self, master, on_change, on_delete):
        super().__init__(master, padding=8)
        self.on_change = on_change
        self.on_delete = on_delete
        self.block: Block | None = None
        self._vars: dict[str, tk.Variable] = {}
        self._auto_labels: dict[str, ttk.Label] = {}
        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True)
        self.show(None)

    def show(self, block: Block | None):
        self.block = None
        for w in self._body.winfo_children():
            w.destroy()
        self._vars = {}
        self._auto_labels = {}
        self.block = block
        if block is None:
            ttk.Label(self._body, text="No block selected.",
                      font=("Segoe UI", 10, "bold")).pack(anchor="w")
            ttk.Label(self._body, wraplength=250, justify="left", text=(
                "Click a block on the canvas to edit it.\n\n"
                "Drag a block to move it. Drop it inside another block's slot to "
                "nest it, or onto empty canvas to make it top level.\n\n"
                "The left column is the program flow, in execution order from "
                "top to bottom. Functions and classes live in the right column "
                "and are entered only through calls (dashed lines). Moving a "
                "block up or down reorders it within its own column."
            )).pack(anchor="w", pady=(6, 0))
            return
        spec = block.spec
        ttk.Label(self._body, text=f"{block.kind} block",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        doc = model.block_doc(block.kind, block.lang)
        if doc:
            ttk.Label(self._body, text=doc, wraplength=WRAP, justify="left").pack(
                anchor="w", pady=(2, 4))
        if spec.help:
            ttk.Label(self._body, text=spec.help, wraplength=WRAP, justify="left",
                      foreground="#555").pack(anchor="w", pady=(0, 6))
        form = ttk.Frame(self._body)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        row = 0
        for name, _default in spec.fields:
            fdoc = model.field_doc(block.kind, name, block.lang)
            if name in CHECK_FIELDS:
                var = tk.BooleanVar(value=bool(block.fields.get(name)))
                cb = ttk.Checkbutton(form, text=name, variable=var,
                                     command=lambda b=block: self._apply(b))
                cb.grid(row=row, column=0, columnspan=2, sticky="w", pady=(4, 0))
            else:
                var = tk.StringVar(value=block.fields.get(name, ""))
                ttk.Label(form, text=name, font=("Segoe UI", 9, "bold")).grid(
                    row=row, column=0, sticky="nw", padx=(0, 6), pady=(4, 0))
                ent = ttk.Entry(form, textvariable=var, font=("Consolas", 10))
                ent.grid(row=row, column=1, sticky="ew", pady=(4, 0))
                ent.bind("<Return>", lambda e, b=block: self._apply(b))
                ent.bind("<FocusOut>", lambda e, b=block: self._apply(b))
            row += 1
            if fdoc:
                ttk.Label(form, text=fdoc, wraplength=WRAP - 40, justify="left",
                          foreground="#64748b", font=("Segoe UI", 8)).grid(
                    row=row, column=1, sticky="w", pady=(0, 2))
                row += 1
            self._vars[name] = var
        if not spec.fields:
            ttk.Label(form, text="This block has no properties.").grid(
                row=row, column=0, columnspan=2, sticky="w")
            row += 1

        ttk.Separator(self._body).pack(fill="x", pady=8)
        ttk.Label(self._body, text="Descriptions",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(self._body, wraplength=WRAP, justify="left", foreground="#64748b",
                  font=("Segoe UI", 8), text=(
                      "Your own notes on what goes in and what comes out. Shown on "
                      "the block when View > descriptions is on; empty uses the "
                      "automatic text.")).pack(anchor="w")
        auto_in, auto_out = model.describe_io(block)
        desc = ttk.Frame(self._body)
        desc.pack(fill="x")
        desc.columnconfigure(1, weight=1)
        for r, (label, attr, auto) in enumerate([("inputs", "desc_in", auto_in),
                                                 ("outputs", "desc_out", auto_out)]):
            var = tk.StringVar(value=getattr(block, attr))
            ttk.Label(desc, text=label, font=("Segoe UI", 9, "bold")).grid(
                row=r * 2, column=0, sticky="w", padx=(0, 6))
            ent = ttk.Entry(desc, textvariable=var)
            ent.grid(row=r * 2, column=1, sticky="ew", pady=2)
            ent.bind("<Return>", lambda e, b=block: self._apply(b))
            ent.bind("<FocusOut>", lambda e, b=block: self._apply(b))
            auto_label = ttk.Label(desc, text=f"auto: {auto or '(none)'}", foreground="#777",
                                   wraplength=WRAP - 40, justify="left", font=("Segoe UI", 8))
            auto_label.grid(row=r * 2 + 1, column=1, sticky="w", pady=(0, 4))
            self._auto_labels[attr] = auto_label
            self._vars[attr] = var

        ttk.Separator(self._body).pack(fill="x", pady=8)
        btns = ttk.Frame(self._body)
        btns.pack(fill="x")
        ttk.Button(btns, text="Apply", command=lambda b=block: self._apply(b)).pack(
            side="left")
        ttk.Button(btns, text="Delete block", command=self.on_delete).pack(side="right")

    def refresh(self):
        """Update the parts of the form derived from the block (the automatic
        descriptions) without rebuilding it, so the focused field survives."""
        if self.block is None or not self._auto_labels:
            return
        auto_in, auto_out = model.describe_io(self.block)
        for attr, auto in (("desc_in", auto_in), ("desc_out", auto_out)):
            self._auto_labels[attr].configure(text=f"auto: {auto or '(none)'}")

    def _apply(self, block: Block):
        if block is not self.block:
            return
        changed = False
        for name, var in self._vars.items():
            if name in ("desc_in", "desc_out"):
                value = var.get()
                if getattr(block, name) != value:
                    setattr(block, name, value)
                    changed = True
            else:
                if name in CHECK_FIELDS:
                    value = CHECK_FIELDS[name] if var.get() else ""
                else:
                    value = var.get()
                if block.fields.get(name) != value:
                    block.fields[name] = value
                    changed = True
        if changed:
            self.on_change()
