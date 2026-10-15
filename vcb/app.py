"""Main window: menus, panes, open/save, live code preview."""
from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import js  # noqa: F401 - registers the JavaScript language
from . import layout, model
from .canvas import CanvasView
from .editor import EditorPanel
from .model import Block, FunctionEntry, PaletteEntry
from .palette import FileExplorer, FunctionPalette, PalettePanel, single_column_width

APP_NAME = "Visual Code Builder"
EDITOR_W = 330
PREVIEW_W = 400


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1400x860")
        self.path: str | None = None
        self.modified = False
        self.lang = "python"
        self._build_menu()
        self._build_panes()
        self._build_status()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.new_file()
        self.after(50, self._init_panes)

    def _init_panes(self):
        """Blocks pane at the widest single-column width; fixed editor and
        code pane widths so later content changes never clip them."""
        self.update_idletasks()
        total = self._outer.winfo_width()
        if total < EDITOR_W + PREVIEW_W + 300:
            # The window is not laid out yet (or is tiny); try again shortly
            # rather than clamping every sash to zero.
            self.after(100, self._init_panes)
            return
        self._outer.sashpos(2, total - PREVIEW_W)
        self._outer.sashpos(1, total - PREVIEW_W - EDITOR_W)
        chrome = max(0, self._left.winfo_width() - self.palette.winfo_width())
        self._outer.sashpos(0, single_column_width() + chrome)

    # ------------------------------------------------------------------ UI
    def _build_menu(self):
        menubar = tk.Menu(self)
        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="New Python file", accelerator="Ctrl+N",
                          command=lambda: self.new_file("python"))
        filem.add_command(label="New JavaScript file", accelerator="Ctrl+J",
                          command=lambda: self.new_file("javascript"))
        filem.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_file)
        filem.add_command(label="Save", accelerator="Ctrl+S", command=self.save_file)
        filem.add_command(label="Save As…", accelerator="Ctrl+Shift+S",
                          command=self.save_file_as)
        filem.add_separator()
        filem.add_command(label="Exit", command=self.quit_app)
        menubar.add_cascade(label="File", menu=filem)

        editm = tk.Menu(menubar, tearoff=0)
        editm.add_command(label="Delete block", accelerator="Del",
                          command=lambda: self.canvas.delete_block())
        editm.add_command(label="Duplicate block", accelerator="Ctrl+D",
                          command=lambda: self.canvas.duplicate_block())
        editm.add_separator()
        editm.add_command(label="Auto-arrange", accelerator="Ctrl+L",
                          command=lambda: self.canvas.auto_arrange())
        menubar.add_cascade(label="Edit", menu=editm)

        self.var_desc = tk.BooleanVar(value=False)
        self.var_calls = tk.BooleanVar(value=True)
        viewm = tk.Menu(menubar, tearoff=0)
        viewm.add_checkbutton(label="Show input/output descriptions",
                              variable=self.var_desc,
                              command=lambda: self.canvas.set_show_desc(self.var_desc.get()))
        viewm.add_checkbutton(label="Show call lines (dashed)", variable=self.var_calls,
                              command=lambda: self.canvas.set_show_calls(self.var_calls.get()))
        menubar.add_cascade(label="View", menu=viewm)

        helpm = tk.Menu(menubar, tearoff=0)
        helpm.add_command(label="How to use", command=self.show_help)
        menubar.add_cascade(label="Help", menu=helpm)
        self.config(menu=menubar)

    def _build_panes(self):
        outer = ttk.PanedWindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True)
        self._outer = outer

        left = ttk.Notebook(outer, width=250)
        self._left = left
        language = model.LANGUAGES[self.lang]
        self.palette = PalettePanel(left, language, on_pick=self.pick_entry,
                                    on_hint=self.set_hint)
        self.functions = FunctionPalette(left, language, on_pick=self.pick_function,
                                         on_hint=self.set_hint)
        self.explorer = FileExplorer(left, on_open=self.open_file)
        left.add(self.palette, text="Blocks")
        left.add(self.functions, text="Functions")
        left.add(self.explorer, text="Files")
        outer.add(left, weight=0)

        self.canvas = CanvasView(outer, on_select=self.on_select,
                                 on_change=self.on_change, on_context=self.show_context)
        outer.add(self.canvas, weight=1)

        self.editor = EditorPanel(outer, on_change=self.on_change,
                                  on_delete=lambda: self.canvas.delete_block())
        outer.add(self.editor, weight=0)

        preview = ttk.Frame(outer, width=380)
        head = ttk.Frame(preview)
        head.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(head, text="Real code", font=("Segoe UI", 9, "bold")).pack(
            side="left", padx=4, pady=2)
        self.syntax_label = ttk.Label(head, text="")
        self.syntax_label.pack(side="left", padx=8)
        ttk.Button(head, text="Apply", command=self.apply_code, width=7).pack(
            side="right", padx=4, pady=2)
        self.code = tk.Text(preview, width=48, font=("Consolas", 10), wrap="none",
                            background="#f8fafc", undo=True)
        self.code.tag_configure("selected", background="#fde68a")
        self.code.bind("<FocusOut>", lambda e: self.apply_code(silent=True))
        self.code.bind("<KeyRelease>", self._on_code_typed)
        self.code.bind("<Tab>", self._focus_next)
        self.code.bind("<Shift-Tab>", self._focus_prev)
        self._spans: dict[int, tuple[int, int]] = {}
        self._last_src = ""
        ysb = ttk.Scrollbar(preview, orient="vertical", command=self.code.yview)
        xsb = ttk.Scrollbar(preview, orient="horizontal", command=self.code.xview)
        self.code.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.code.grid(row=1, column=0, sticky="nsew")
        ysb.grid(row=1, column=1, sticky="ns")
        xsb.grid(row=2, column=0, sticky="ew")
        preview.rowconfigure(1, weight=1)
        preview.columnconfigure(0, weight=1)
        outer.add(preview, weight=0)

    def _build_status(self):
        self.status = ttk.Label(self, anchor="w", padding=(6, 2), relief="sunken")
        self.status.pack(fill="x", side="bottom")

    def _bind_keys(self):
        self.bind_all("<Control-n>", lambda e: self.new_file("python"))
        self.bind_all("<Control-j>", lambda e: self.new_file("javascript"))
        self.bind_all("<Control-o>", lambda e: self.open_file())
        self.bind_all("<Control-s>", lambda e: self.save_file())
        self.bind_all("<Control-S>", lambda e: self.save_file_as())
        self.bind_all("<Control-d>", lambda e: self._if_canvas(self.canvas.duplicate_block))
        self.bind_all("<Control-l>", lambda e: self.canvas.auto_arrange())
        self.bind_all("<Delete>", lambda e: self._if_canvas(self.canvas.delete_block))
        # Tab always moves between fields; it never inserts whitespace.
        self.bind_class("Text", "<Tab>", self._focus_next)
        self.bind_class("Text", "<Shift-Tab>", self._focus_prev)
        self.bind_class("TEntry", "<Tab>", self._focus_next)
        self.bind_class("TEntry", "<Shift-Tab>", self._focus_prev)

    def _if_canvas(self, fn):
        focus = self.focus_get()
        if isinstance(focus, (tk.Entry, ttk.Entry, tk.Text)):
            return
        fn()

    # ------------------------------------------------------------ callbacks
    def on_select(self, block: Block | None):
        self.editor.show(block)
        self.highlight_code(block)
        if block is not None:
            self.set_status(f"Selected {block.kind}: {block.title()}")

    def on_change(self):
        self.modified = True
        self.canvas.redraw()
        if self.canvas.selected is not None:
            self.editor.show(self.canvas.selected)
        self.refresh_preview()
        self.highlight_code(self.canvas.selected)
        self.update_title()

    def highlight_code(self, block: Block | None):
        """Mark the generated lines that belong to the selected block."""
        self.code.tag_remove("selected", "1.0", "end")
        if block is None:
            return
        span = self._spans.get(block.id)
        if span is None:
            return
        first, last = span
        self.code.tag_add("selected", f"{first}.0", f"{last}.end+1c")
        self.code.see(f"{last}.0")
        self.code.see(f"{first}.0")

    def refresh_preview(self):
        src, self._spans = self.canvas.source_with_spans()
        self._last_src = src
        self.code.delete("1.0", "end")
        self.code.insert("1.0", src)
        self.code.edit_reset()
        self._show_syntax(src)

    def _show_syntax(self, src: str) -> SyntaxError | None:
        err = model.syntax_error(src, self.lang)
        if err is None:
            self.syntax_label.configure(text="syntax OK", foreground="#15803d")
        else:
            self.syntax_label.configure(
                text=f"syntax error line {err.lineno}: {err.msg}", foreground="#b91c1c")
        return err

    def _code_text(self) -> str:
        return self.code.get("1.0", "end-1c")

    def _code_edited(self) -> bool:
        return self._code_text().rstrip("\n") != self._last_src.rstrip("\n")

    def _on_code_typed(self, event=None):
        if event is not None and event.keysym in ("Tab", "Shift_L", "Shift_R"):
            return
        if self._code_edited():
            self.syntax_label.configure(
                text="edited: click Apply or leave the pane", foreground="#b45309")

    def apply_code(self, silent: bool = False) -> bool:
        """Re-parse the Real code pane into blocks when it was edited."""
        if not self._code_edited():
            return True
        text = self._code_text()
        err = self._show_syntax(text)
        if err is not None:
            if not silent:
                messagebox.showerror(
                    APP_NAME, f"Cannot apply: syntax error on line {err.lineno}:\n{err.msg}")
            return False
        blocks = model.parse_source(text, self.lang)
        self.canvas.set_blocks(blocks, keep_positions_from=self.canvas.blocks,
                               lang=self.lang)
        self.modified = True
        self.refresh_preview()
        self.update_title()
        self.set_status("Applied the edited code to the canvas.")
        return True

    def _focus_next(self, event):
        event.widget.tk_focusNext().focus_set()
        return "break"

    def _focus_prev(self, event):
        event.widget.tk_focusPrev().focus_set()
        return "break"

    def set_status(self, text: str):
        self.status.configure(text=text)

    def set_hint(self, text: str):
        if text:
            self.set_status(text)

    def language(self) -> model.Language:
        return model.LANGUAGES[self.lang]

    def update_title(self):
        name = os.path.basename(self.path) if self.path else self._untitled_name()
        star = "*" if self.modified else ""
        self.title(f"{name}{star} - {self.language().label} - {APP_NAME}")

    def _untitled_name(self) -> str:
        return "untitled" + self.language().extensions[0]

    def set_language(self, lang: str):
        """Switch the editor to a language, rebuilding the palette tabs."""
        if lang == self.lang and self.palette.lang.name == lang:
            return
        self.lang = lang
        language = self.language()
        current = self._left.index(self._left.select())
        for old, tab, maker, attr in (
                (self.palette, 0, PalettePanel, "palette"),
                (self.functions, 1, FunctionPalette, "functions")):
            self._left.forget(old)
            old.destroy()
            on_pick = self.pick_entry if attr == "palette" else self.pick_function
            new = maker(self._left, language, on_pick=on_pick, on_hint=self.set_hint)
            setattr(self, attr, new)
            self._left.insert(tab, new, text="Blocks" if tab == 0 else "Functions")
        self._left.select(current)
        self.canvas.lang = lang
        self.update_title()

    # -------------------------------------------------------------- palette
    def pick_entry(self, entry: PaletteEntry):
        sel = self.canvas.selected
        block = model.block_from_entry(entry)
        if entry.slot is not None:
            host = sel if sel is not None and entry.slot in sel.spec.slots else None
            if host is None:
                host = model.new_block(self.language().slot_hosts[entry.slot], self.lang)
                model.insert_block(host, entry.slot, block)
                self.canvas.add_block(host)
                self.canvas.select(block)
                self.set_status(f"Added a {host.kind} block to hold the {entry.word}.")
            else:
                self.canvas.add_block(block, (host, entry.slot, None))
            return
        self._add_new_block(block)

    def pick_function(self, entry: FunctionEntry):
        self._add_new_block(model.block_from_function(entry))
        self.set_status(f"Added {entry.word}(): {entry.doc}")

    def _add_new_block(self, block: Block):
        """Inside the selected container, after the selected block, or at the
        end of the program flow."""
        sel = self.canvas.selected
        if sel is not None and "body" in sel.spec.slots:
            self.canvas.add_block(block, (sel, "body", None))
        elif sel is not None and sel.parent is not None:
            idx = sel.parent.slots[sel.slot_name].index(sel) + 1
            self.canvas.add_block(block, (sel.parent, sel.slot_name, idx))
        else:
            self.canvas.add_block(block)

    def show_context(self, block: Block | None, x: int, y: int):
        menu = tk.Menu(self, tearoff=0)
        if block is None:
            menu.add_command(label="Auto-arrange", command=self.canvas.auto_arrange)
        else:
            menu.add_command(label="Delete", command=lambda: self.canvas.delete_block(block))
            menu.add_command(label="Duplicate",
                             command=lambda: self.canvas.duplicate_block(block))
            if layout.is_truncatable(block):
                menu.add_command(
                    label="Collapse text" if block.expanded else "Expand text",
                    command=lambda: self.canvas.toggle_expanded(block))
            extras = [s for s in block.spec.slots
                      if s not in model._REQUIRED_SLOTS and not block.slots.get(s)]
            if extras:
                menu.add_separator()
            fillers = self.language().slot_fillers
            for slot in extras:
                label = model.slot_label(block, slot)
                child = (model.new_block(fillers[slot], self.lang)
                         if slot in fillers else None)
                menu.add_command(
                    label=f"Add {label} slot",
                    command=lambda b=block, s=slot, c=child: self.canvas.fill_slot(b, s, c))
            if block.parent is not None:
                menu.add_separator()
                menu.add_command(label="Move to top level",
                                 command=lambda: self._move_top(block))
        menu.tk_popup(x, y)

    def _move_top(self, block: Block):
        model.detach_block(block)
        block.x += 40
        self.canvas.place_top_level(block)
        self.canvas.select(block)
        self.on_change()

    # ---------------------------------------------------------------- files
    def confirm_discard(self) -> bool:
        if not self.modified:
            return True
        ans = messagebox.askyesnocancel(APP_NAME, "Save changes to the current file?")
        if ans is None:
            return False
        if ans:
            return self.save_file()
        return True

    def _filetypes(self):
        types = []
        for lang in model.LANGUAGES.values():
            types.append((f"{lang.label} files", " ".join("*" + e for e in lang.extensions)))
        types.append(("All files", "*.*"))
        return types

    def new_file(self, lang: str | None = None):
        if not self.confirm_discard():
            return
        lang = lang or self.lang
        self.path = None
        self.set_language(lang)
        self.canvas.set_blocks([], lang=lang)
        self.modified = False
        self.refresh_preview()
        self.update_title()
        self.set_status(f"New {self.language().label} file. Add blocks from the Blocks tab.")

    def open_file(self, path: str | None = None):
        if not self.confirm_discard():
            return
        if path is None:
            path = filedialog.askopenfilename(title="Open source file",
                                              filetypes=self._filetypes())
            if not path:
                return
        lang = model.language_for_path(path) or self.lang
        try:
            src = Path(path).read_text(encoding="utf-8")
            blocks = model.parse_source(src, lang)
        except SyntaxError as e:
            messagebox.showerror(APP_NAME, f"Cannot parse {path}:\nline {e.lineno}: {e.msg}")
            return
        except ImportError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Cannot read {path}:\n{e}")
            return
        self.path = path
        self.set_language(lang)
        self.canvas.set_blocks(blocks, lang=lang)
        self.modified = False
        self.refresh_preview()
        self.update_title()
        self.set_status(f"Opened {path}  ({self.language().label}, "
                        f"{len(blocks)} top-level blocks)")

    def save_file(self) -> bool:
        if self.path is None:
            return self.save_file_as()
        return self._write(self.path)

    def save_file_as(self) -> bool:
        ext = self.language().extensions[0]
        path = filedialog.asksaveasfilename(
            title=f"Save {self.language().label} file", defaultextension=ext,
            initialfile=os.path.basename(self.path) if self.path else self._untitled_name(),
            filetypes=self._filetypes())
        if not path:
            return False
        return self._write(path)

    def _write(self, path: str) -> bool:
        src = self.canvas.source()
        err = model.syntax_error(src, self.lang)
        if err is not None:
            if not messagebox.askyesno(
                    APP_NAME, f"The generated code has a syntax error on line "
                    f"{err.lineno}:\n{err.msg}\n\nSave anyway?"):
                return False
        try:
            Path(path).write_text(src, encoding="utf-8")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Cannot write {path}:\n{e}")
            return False
        self.path = path
        self.modified = False
        self.update_title()
        self.set_status(f"Saved {path}")
        return True

    def quit_app(self):
        if self.confirm_discard():
            self.destroy()

    def show_help(self):
        messagebox.showinfo(APP_NAME, (
            "Python and JavaScript files are supported. The language follows the "
            "file extension when you open a file; File > New picks it for a new "
            "file, and the Blocks and Functions tabs change with it.\n\n"
            "Blocks tab: click a reserved word to add its block. If a container "
            "block is selected the new block goes inside it.\n\n"
            "Files tab: browse folders and double-click a .py or .js file to open "
            "it.\n\n"
            "Canvas: click to select, drag to move. Drop a block inside another "
            "block's slot to nest it; drop it on empty canvas to make it top level. "
            "Moving a block moves everything nested in it. Drag empty canvas to pan. "
            "Right-click a block for more actions.\n\n"
            "The left column is the program flow in execution order; functions and "
            "classes sit in the right column and are entered only through calls. "
            "Solid arrows show control flow, including loop-backs and continue/break "
            "jumps; a block that returns, raises, breaks or continues has no arrow "
            "to the next block. Dashed purple lines show calls to functions and "
            "classes on the canvas; dashed grey arrows lead into except handlers.\n\n"
            "Right panel: edit the properties and the input/output descriptions of "
            "the selected block. View menu toggles the descriptions on the blocks.\n\n"
            "Shortcuts: Ctrl+N new, Ctrl+O open, Ctrl+S save, Ctrl+Shift+S save as, "
            "Del delete block, Ctrl+D duplicate, Ctrl+L auto-arrange."))


def main():
    App().mainloop()
