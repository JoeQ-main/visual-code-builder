"""The block canvas: drawing, selection, drag and drop, flow lines."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from . import layout, model
from .model import Block

# Light header tint per category (containers only) and a strong accent used
# for the left spine / stripe. Structure is carried by shape; colour is a hint.
FILL = {
    "definition": "#dbeafe", "flow": "#fef3c7", "loop": "#dcfce7", "error": "#fee2e2",
    "context": "#ede9fe", "simple": "#f3f4f6", "import": "#e0f2fe", "scope": "#fae8ff",
}
ACCENT = {
    "definition": "#2563eb", "flow": "#d97706", "loop": "#16a34a", "error": "#dc2626",
    "context": "#7c3aed", "simple": "#6b7280", "import": "#0284c7", "scope": "#c026d3",
}
EDGE = "#334155"
RULE = "#cbd5e1"
SELECT = "#1d4ed8"
FLOW = "#374151"
CALL = "#7c3aed"
DROP = "#2563eb"
RADIUS = 6
TITLE_FONT = ("Consolas", 10)
TITLE_BOLD = ("Consolas", 10, "bold")
DESC_FONT = ("Segoe UI", 8, "italic")
LABEL_FONT = ("Segoe UI", 8)
SLOT_FONT = ("Segoe UI", 7, "bold")


class CanvasView(tk.Frame):
    def __init__(self, master, on_select, on_change, on_context):
        super().__init__(master)
        self.on_select = on_select
        self.on_change = on_change
        self.on_context = on_context
        self.blocks: list[Block] = []
        self.lang = "python"
        self.selected: Block | None = None
        self.show_desc = False
        self.show_calls = True
        self._drag: dict | None = None
        self._pan = False
        self._bold = tkfont.Font(family="Consolas", size=10, weight="bold")
        self._slot_font = tkfont.Font(family="Segoe UI", size=7, weight="bold")

        self.canvas = tk.Canvas(self, bg="#f8fafc", highlightthickness=0)
        ysb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        xsb = tk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        c = self.canvas
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_motion)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<ButtonPress-3>", self._on_right)
        c.bind("<Double-Button-1>", self._on_double)
        c.bind("<MouseWheel>", lambda e: c.yview_scroll(-int(e.delta / 120), "units"))
        c.bind("<Shift-MouseWheel>", lambda e: c.xview_scroll(-int(e.delta / 120), "units"))
        c.bind("<Configure>", lambda e: self._update_scrollregion())

    # ------------------------------------------------------------------ data
    def set_blocks(self, blocks: list[Block], keep_positions_from=None, lang=None):
        """Replace all blocks. With ``keep_positions_from`` (the previous
        top-level blocks), blocks whose kind and title match keep their old
        position and expanded state; everything is re-arranged otherwise."""
        self.blocks = list(blocks)
        if lang is not None:
            self.lang = lang
        elif self.blocks:
            self.lang = self.blocks[0].lang
        self.selected = None
        old = {(b.kind, b.title()): b for b in (keep_positions_from or [])}
        matched = []
        for b in self.blocks:
            prev = old.pop((b.kind, b.title()), None)
            if prev is not None:
                matched.append((b, prev))
        if keep_positions_from and len(matched) == len(self.blocks):
            for b, prev in matched:
                b.x, b.y, b.expanded = prev.x, prev.y, prev.expanded
        else:
            layout.auto_arrange(self.blocks, self.show_desc)
        self.redraw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.on_select(None)

    def source(self) -> str:
        """Module order is the block list, not the canvas position."""
        return model.generate_source(self.blocks, lang=self.lang)

    def source_with_spans(self) -> tuple[str, dict[int, tuple[int, int]]]:
        """Source plus ``block.id -> (first line, last line)`` for every block."""
        spans: dict[int, tuple[int, int]] = {}
        return model.generate_source(self.blocks, spans, lang=self.lang), spans

    def place_top_level(self, block: Block):
        """Insert a top-level block into the module order by its position.

        Blocks are ordered only against blocks of the same column (program
        flow, or definitions), so moving a function never reorders the main
        flow and vice versa. A first definition goes after the imports.
        """
        if block in self.blocks:
            self.blocks.remove(block)
        col = layout.column(block)
        same = [o for o in self.blocks if layout.column(o) == col]
        if same:
            above = [o for o in same if o.y <= block.y]
            if above:
                idx = self.blocks.index(max(above, key=lambda o: o.y)) + 1
            else:
                idx = self.blocks.index(min(same, key=lambda o: o.y))
        elif col == "defs":
            idx = 0
            for i, o in enumerate(self.blocks):
                if o.kind in ("import", "from"):
                    idx = i + 1
        else:
            idx = len(self.blocks)
        self.blocks.insert(idx, block)

    def select(self, block: Block | None):
        self.selected = block
        self.redraw()
        self.on_select(block)

    def add_block(self, block: Block, into: tuple[Block, str, int | None] | None = None):
        if into is not None:
            parent, slot, index = into
            model.insert_block(parent, slot, block, index)
        else:
            layout.layout_all(self.blocks, self.show_desc)
            col = layout.column(block)
            block.x = layout.STACK_X if col == "main" else layout.defs_column_x(self.blocks)
            block.y = layout.next_free_y(self.blocks, col)
            self.place_top_level(block)
        self.select(block)
        self._changed()
        self._scroll_to(block)

    def delete_block(self, block: Block | None = None):
        block = block or self.selected
        if block is None:
            return
        if block.parent is not None:
            model.detach_block(block)
        elif block in self.blocks:
            self.blocks.remove(block)
        self.select(None)
        self._changed()

    def duplicate_block(self, block: Block | None = None):
        block = block or self.selected
        if block is None:
            return
        copy = model.clone_block(block)
        if block.parent is not None:
            idx = block.parent.slots[block.slot_name].index(block) + 1
            self.add_block(copy, (block.parent, block.slot_name, idx))
        else:
            copy.x, copy.y = block.x + 30, block.y + 30
            self.blocks.insert(self.blocks.index(block) + 1, copy)
            self.select(copy)
            self._changed()

    def fill_slot(self, block: Block, slot: str, child: Block | None = None):
        """Make a slot visible by giving it a block (default: a placeholder)."""
        child = child or model.new_block(model.LANGUAGES[self.lang].placeholder_kind,
                                         self.lang)
        model.insert_block(block, slot, child)
        self.select(child)
        self._changed()

    def auto_arrange(self):
        layout.auto_arrange(self.blocks, self.show_desc)
        self.redraw()

    def set_show_desc(self, flag: bool):
        self.show_desc = flag
        self.redraw()

    def set_show_calls(self, flag: bool):
        self.show_calls = flag
        self.redraw()

    def _changed(self):
        self.redraw()
        self.on_change()

    # --------------------------------------------------------------- drawing
    def redraw(self):
        c = self.canvas
        c.delete("all")
        layout.layout_all(self.blocks, self.show_desc)
        dragging = self._drag["block"] if self._drag and self._drag.get("moved") else None
        layout.push_apart(self.blocks, skip=dragging)
        layout.layout_all(self.blocks, self.show_desc)
        self._draw_column_labels()
        for b in self.blocks:
            self._draw_block(b)
        self._draw_flow()
        if self.show_calls:
            self._draw_calls()
        if self._drag and self._drag.get("target"):
            parent, slot, _ = self._drag["target"]
            x1, y1, x2, y2 = parent.slot_rects[slot]
            c.create_rectangle(x1 - 2, y1, x2 + 2, y2, outline=DROP, width=2, dash=(4, 2))
        self._update_scrollregion()

    def _update_scrollregion(self):
        bbox = self.canvas.bbox("all") or (0, 0, 0, 0)
        w = max(bbox[2] + 300, self.canvas.winfo_width())
        h = max(bbox[3] + 300, self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, w, h))

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _draw_block(self, b: Block, depth: int = 0):
        c = self.canvas
        cat = b.spec.category
        accent = ACCENT[cat]
        selected = b is self.selected
        is_container = bool(b.spec.slots)
        x1, y1, x2, y2 = b.x, b.y, b.x + b.w, b.y + b.h
        hh = layout.header_height(b)

        # Outline style: no bottom or right edges. A block is marked by its
        # left spine for its full height, plus a top rule over the header.
        if is_container:
            c.create_rectangle(x1, y1, x2, y1 + hh,
                               fill="#dbeafe" if selected else FILL[cat], outline="")
            c.create_line(x1, y1 + 1, x2, y1 + 1,
                          fill=SELECT if selected else EDGE, width=2 if selected else 1.5)
            c.create_rectangle(x1, y1, x1 + 7, y2,
                               fill=SELECT if selected else accent, outline="")
        else:
            if selected:
                c.create_rectangle(x1, y1, x2, y2, fill="#dbeafe", outline="")
            c.create_rectangle(x1 + 1, y1 + 2, x1 + 5, y2 - 2,
                               fill=SELECT if selected else accent, outline="")

        # Header text: bold keyword, regular remainder, extra lines when expanded.
        lines = layout.title_lines(b)
        kw, rest = layout.title_parts(b)
        tx = x1 + layout.TEXT_X
        cy = y1 + layout.HEADER_H / 2
        if kw:
            c.create_text(tx, cy, text=kw, anchor="w", font=TITLE_BOLD, fill="#0f172a")
            tx += self._bold.measure(kw)
        c.create_text(tx, cy, text=rest, anchor="w",
                      font=TITLE_BOLD if is_container and not kw else TITLE_FONT,
                      fill="#0f172a")
        for n, line in enumerate(lines[1:], start=1):
            c.create_text(x1 + layout.TEXT_X, cy + n * layout.TITLE_LINE_H, text=line,
                          anchor="w", font=TITLE_FONT, fill="#0f172a")
        y = y1 + layout.HEADER_H + (len(lines) - 1) * layout.TITLE_LINE_H
        if layout.is_truncatable(b):
            c.create_text(x2 - layout.PAD, cy, anchor="e", font=LABEL_FONT, fill=SELECT,
                          text="▾ less" if b.expanded else "▸ more")
        for line in b.desc_lines:
            c.create_text(x1 + layout.TEXT_X, y + layout.DESC_H / 2, text=line,
                          anchor="w", font=DESC_FONT, fill="#475569")
            y += layout.DESC_H

        # Slots: a small caps label and a rule across the section start.
        for slot in layout.visible_slots(b):
            sx1, sy1, sx2, sy2 = b.slot_rects[slot]
            label = model.slot_label(b, slot).upper()
            ly = sy1 + layout.SLOT_LABEL_H / 2
            c.create_text(x1 + 12, ly, text=label, anchor="w", font=SLOT_FONT,
                          fill="#475569")
            lw = self._slot_font.measure(label)
            c.create_line(x1 + 12 + lw + 6, ly, sx2, ly, fill=RULE)
            kids = b.slots.get(slot, [])
            if not kids:
                c.create_rectangle(sx1, sy1 + layout.SLOT_LABEL_H, sx2, sy2,
                                   dash=(3, 3), outline="#94a3b8")
                c.create_text((sx1 + sx2) / 2, (sy1 + layout.SLOT_LABEL_H + sy2) / 2,
                              text="drop blocks here", font=LABEL_FONT, fill="#94a3b8")
            for k in kids:
                self._draw_block(k, depth + 1)

    def _arrow(self, pts, **kw):
        flat = [v for p in pts for v in p]
        self.canvas.create_line(*flat, arrow="last", arrowshape=(8, 10, 4), **kw)

    def _draw_column_labels(self):
        if not self.blocks:
            return
        c = self.canvas
        font = ("Segoe UI", 9, "bold")
        if any(layout.column(b) == "main" for b in self.blocks):
            c.create_text(layout.STACK_X, layout.STACK_Y - 16, text="program flow",
                          anchor="w", font=font, fill="#6b7280")
        if any(layout.column(b) == "defs" for b in self.blocks):
            c.create_text(layout.defs_column_x(self.blocks), layout.STACK_Y - 16,
                          text="functions and classes (entered by calls)",
                          anchor="w", font=font, fill="#6b7280")

    def _sequence(self, kids):
        """Solid arrows between consecutive blocks that actually flow on."""
        for a, nb in zip(kids, kids[1:]):
            if layout.terminates(a):
                continue
            self._arrow([(a.x + 30, a.y + a.h), (nb.x + 30, nb.y)], fill=FLOW, width=1.5)

    def _draw_flow(self):
        # Module execution: the main column in module order. Definitions are
        # only entered through calls, so they are not part of this chain.
        self._sequence([b for b in self.blocks if layout.column(b) == "main"])
        for b in model.walk(self.blocks):
            for slot in layout.visible_slots(b):
                kids = b.slots.get(slot, [])
                if not kids:
                    continue
                first = kids[0]
                x = b.x + 16
                style = {"dash": (4, 3)} if slot == "handlers" else {}
                self._arrow([(x, b.slot_rects[slot][1] + layout.SLOT_LABEL_H),
                             (x, first.y + 12), (first.x, first.y + 12)],
                            fill=FLOW, width=1.5, **style)
                self._sequence(kids)
            if layout.is_loop(b):
                self._loop_back(b)
            elif b.kind == "continue":
                loop = layout.enclosing_loop(b)
                if loop is not None:
                    self._jump_to_header(b, loop)
            elif b.kind == "break":
                loop = layout.enclosing_loop(b)
                if loop is not None:
                    x = loop.x - 9
                    self._arrow([(b.x, b.y + 13), (x, b.y + 13),
                                 (x, loop.y + loop.h + 5), (loop.x + 16, loop.y + loop.h + 5)],
                                fill=FLOW, width=1.5)

    def _loop_back(self, loop: Block):
        """Solid arrow from the end of the body back to the loop header,
        routed just outside the loop's left edge."""
        body = loop.slots.get("body", [])
        if not body or layout.terminates(body[-1]):
            return
        last = body[-1]
        x = loop.x - 9
        self._arrow([(last.x + 30, last.y + last.h), (last.x + 30, last.y + last.h + 5),
                     (x, last.y + last.h + 5), (x, loop.y + 13), (loop.x, loop.y + 13)],
                    fill=FLOW, width=1.5)

    def _jump_to_header(self, src: Block, loop: Block):
        x = loop.x - 9
        self._arrow([(src.x, src.y + 13), (x, src.y + 13), (x, loop.y + 13),
                     (loop.x, loop.y + 13)], fill=FLOW, width=1.5)

    def _draw_calls(self):
        """Dashed call lines, routed through gutters so they cross no blocks.

        Calls from the program flow run through the channel between the two
        columns and enter the definition from the left. Calls made inside a
        definition run out to a gutter right of the definitions column and
        enter from the right.
        """
        edges = model.call_edges(self.blocks)
        if not edges:
            return
        right_gutter = max(b.x + b.w for b in model.walk(self.blocks)) + 30
        channel = layout.defs_column_x(self.blocks) - 30
        arrivals: dict[int, int] = {}   # lines already arriving at a block
        for i, (src, dst) in enumerate(edges):
            off = 6 * (i % 6)
            # Several lines into one block get their own row in its header,
            # otherwise their overlapping dashes merge into a solid line.
            n = arrivals.get(dst.id, 0)
            arrivals[dst.id] = n + 1
            x1, y1, y2 = src.x + src.w, src.y + 13, dst.y + 8 + 5 * (n % 4)
            if layout.column(layout.root_of(src)) == "main":
                xc = channel - off
                pts = [(x1, y1), (xc, y1), (xc, y2), (dst.x, y2)]
            else:
                xg = right_gutter + off
                pts = [(x1, y1), (xg, y1), (xg, y2), (dst.x + dst.w, y2)]
            self._arrow(pts, fill=CALL, dash=(6, 4), width=1.5)

    # ----------------------------------------------------------------- mouse
    def _xy(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _on_press(self, event):
        self.canvas.focus_set()
        x, y = self._xy(event)
        b = layout.block_at(self.blocks, x, y)
        self.select(b)
        if b is None:
            self._pan = True
            self.canvas.scan_mark(event.x, event.y)
            self._drag = None
            return
        self._pan = False
        if (layout.is_truncatable(b) and x >= b.x + b.w - layout.TOGGLE_W
                and y <= b.y + layout.HEADER_H):
            self.toggle_expanded(b)
            self._drag = None
            return
        self._drag = {"block": b, "ox": x - b.x, "oy": y - b.y, "moved": False,
                      "target": None, "start": (x, y)}

    def _on_double(self, event):
        x, y = self._xy(event)
        b = layout.block_at(self.blocks, x, y)
        if b is not None and layout.is_truncatable(b):
            self._drag = None
            self.toggle_expanded(b)

    def toggle_expanded(self, block: Block):
        block.expanded = not block.expanded
        self.redraw()

    def _on_motion(self, event):
        if self._pan:
            self.canvas.scan_dragto(event.x, event.y, gain=1)
            return
        d = self._drag
        if not d:
            return
        x, y = self._xy(event)
        b = d["block"]
        if not d["moved"]:
            if abs(x - d["start"][0]) + abs(y - d["start"][1]) < 4:
                return
            d["moved"] = True
            if b.parent is not None:
                model.detach_block(b)
                self.blocks.append(b)
            self.canvas.configure(cursor="fleur")
        b.x, b.y = x - d["ox"], y - d["oy"]
        d["target"] = layout.drop_target(self.blocks, x, y, b)
        self.redraw()

    def _on_release(self, event):
        d = self._drag
        self._drag = None
        self._pan = False
        self.canvas.configure(cursor="")
        if not d or not d["moved"]:
            return
        b = d["block"]
        if d["target"]:
            parent, slot, index = d["target"]
            self.blocks.remove(b)
            model.insert_block(parent, slot, b, index)
        else:
            self.place_top_level(b)
        self._changed()

    def _on_right(self, event):
        x, y = self._xy(event)
        b = layout.block_at(self.blocks, x, y)
        self.select(b)
        self.on_context(b, event.x_root, event.y_root)

    def _scroll_to(self, block: Block):
        c = self.canvas
        region = c.cget("scrollregion").split()
        if len(region) != 4:
            return
        total_h = float(region[3])
        if total_h <= 0:
            return
        top = c.canvasy(0)
        bottom = top + c.winfo_height()
        if not (top <= block.y <= bottom):
            c.yview_moveto(max(0.0, (block.y - 40) / total_h))
