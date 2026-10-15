"""Geometry for blocks: sizes, absolute positions, hit testing. No GUI."""
from __future__ import annotations

import textwrap

from . import model
from .model import Block

HEADER_H = 29
DESC_H = 17
PAD = 8
TEXT_X = 14          # text starts after the left spine / stripe
SLOT_INDENT = 26     # children step in this far from the parent's left edge
DEPTH_STEP = 14      # and their right end reaches this far past the parent's
SLOT_LABEL_H = 20
CHILD_GAP = 10
SLOT_GAP = 4
BOTTOM_PAD = 10
EMPTY_SLOT_H = 24
MIN_W = 150
CHAR_W = 8.2         # Consolas 11pt at 96 dpi
MAX_TITLE_CHARS = 70
STACK_X = 40
STACK_Y = 40
STACK_GAP = 36


TITLE_LINE_H = 18
TOGGLE_W = 52       # room reserved at the right of the header for "more/less"


def is_truncatable(block: Block) -> bool:
    return len(block.title()) > MAX_TITLE_CHARS


def title_lines(block: Block) -> list[str]:
    """Header text lines: one line, cut off, unless the block is expanded."""
    t = block.title()
    if len(t) <= MAX_TITLE_CHARS:
        return [t]
    if not block.expanded:
        return [t[:MAX_TITLE_CHARS - 1] + "…"]
    return textwrap.wrap(t, MAX_TITLE_CHARS, break_long_words=True,
                         break_on_hyphens=False, drop_whitespace=False) or [t]


def display_title(block: Block) -> str:
    return title_lines(block)[0]


def header_height(block: Block) -> float:
    return (HEADER_H + TITLE_LINE_H * (len(title_lines(block)) - 1)
            + DESC_H * len(block.desc_lines))


def visible_slots(block: Block) -> list[str]:
    """Slots drawn for a block: required ones always, optional ones when used."""
    return [s for s in block.spec.slots
            if s in model._REQUIRED_SLOTS or block.slots.get(s)]


def measure(block: Block, show_desc: bool):
    block.desc_lines = []
    if show_desc:
        ins, outs = model.effective_io(block)
        if ins:
            block.desc_lines.append(f"in: {ins}")
        if outs:
            block.desc_lines.append(f"out: {outs}")
    text_w = max([len(t) for t in title_lines(block)] + [len(d) for d in block.desc_lines])
    w = max(MIN_W, text_w * CHAR_W + TEXT_X + PAD + 4)
    if is_truncatable(block):
        w += TOGGLE_W
    h = header_height(block)
    slots = visible_slots(block)
    for slot in slots:
        kids = block.slots.get(slot, [])
        h += SLOT_LABEL_H
        if kids:
            for k in kids:
                measure(k, show_desc)
                w = max(w, k.w + SLOT_INDENT - DEPTH_STEP)
                h += k.h + CHILD_GAP
        else:
            h += EMPTY_SLOT_H
        h += SLOT_GAP
    if slots:
        h += BOTTOM_PAD - SLOT_GAP
    block.w, block.h = w, h


def place(block: Block, x: float, y: float, width: float | None = None):
    block.x, block.y = x, y
    if width is not None and width > block.w:
        block.w = width
    block.slot_rects = {}
    cy = y + header_height(block)
    # Children start further right and also end further right than the
    # parent, so every nesting level forms a staircase to the right.
    inner_w = block.w - SLOT_INDENT + DEPTH_STEP
    for slot in visible_slots(block):
        top = cy
        cy += SLOT_LABEL_H
        kids = block.slots.get(slot, [])
        if kids:
            for k in kids:
                place(k, x + SLOT_INDENT, cy, inner_w)
                cy += k.h + CHILD_GAP
        else:
            cy += EMPTY_SLOT_H
        block.slot_rects[slot] = (x + SLOT_INDENT, top, x + block.w + DEPTH_STEP, cy)
        cy += SLOT_GAP


def title_parts(block: Block) -> tuple[str, str]:
    """(keyword, rest) of the first header line, so the keyword can be bold."""
    first = title_lines(block)[0]
    kw = model.LANGUAGES[block.lang].keyword(block)
    if kw and first.startswith(kw):
        return kw, first[len(kw):]
    return "", first


def layout_all(blocks, show_desc: bool):
    for b in blocks:
        measure(b, show_desc)
        place(b, b.x, b.y)


COLUMN_GAP = 150     # room for the call-line channel between the columns
DEFAULT_MAIN_W = 240
LANE_W = 14          # spacing between parallel call lines in a gutter


def column(block: Block) -> str:
    """'defs' for functions and classes, 'main' for the program flow."""
    return "defs" if block.kind in model.LANGUAGES[block.lang].def_kinds else "main"


def defs_column_x(blocks) -> float:
    """Left edge of the definitions column."""
    defs = [b for b in blocks if column(b) == "defs"]
    if defs:
        return min(b.x for b in defs)
    main_w = max([b.w for b in blocks if column(b) == "main"], default=DEFAULT_MAIN_W)
    return STACK_X + max(main_w, DEFAULT_MAIN_W) + COLUMN_GAP


def auto_arrange(blocks, show_desc: bool):
    """Program flow stacked on the left, functions and classes on the right.

    ``blocks`` is the module order and is left untouched.
    """
    for b in blocks:
        measure(b, show_desc)
    main = [b for b in blocks if column(b) == "main"]
    defs = [b for b in blocks if column(b) == "defs"]
    main_w = max([b.w for b in main], default=DEFAULT_MAIN_W)
    def_x = STACK_X + max(main_w, DEFAULT_MAIN_W) + COLUMN_GAP
    for col, x in ((main, STACK_X), (defs, def_x)):
        y = STACK_Y
        for b in col:
            b.x, b.y = x, y
            place(b, b.x, b.y)
            y += b.h + STACK_GAP


def push_apart(blocks, skip: Block | None = None):
    """Move top-level blocks down so none overlaps one above it.

    Heights change when descriptions are toggled or a block is expanded;
    this keeps each column readable without discarding the user's layout.
    """
    placed: list[Block] = []
    for b in sorted(blocks, key=lambda b: (b.y, b.x)):
        if b is skip:
            continue
        for p in placed:
            overlap_x = p.x < b.x + b.w and b.x < p.x + p.w
            if overlap_x and p.y <= b.y < p.y + p.h + CHILD_GAP:
                b.y = p.y + p.h + STACK_GAP
        placed.append(b)


def assign_lanes(spans, gap: float = 4.0) -> list[int]:
    """Lane index for each (lo, hi) vertical span so that spans sharing a lane
    never overlap (interval packing, lowest free lane first).

    Lines that run side by side in a gutter get different lanes, so their
    vertical segments never lie on top of each other.
    """
    order = sorted(range(len(spans)), key=lambda i: (min(spans[i]), max(spans[i])))
    lanes: list[list[tuple[float, float]]] = []
    result = [0] * len(spans)
    for i in order:
        lo, hi = sorted(spans[i])
        lo, hi = lo - gap, hi + gap
        for n, used in enumerate(lanes):
            if all(hi < a or lo > b for a, b in used):
                used.append((lo, hi))
                result[i] = n
                break
        else:
            lanes.append([(lo, hi)])
            result[i] = len(lanes) - 1
    return result


def next_free_y(blocks, col: str | None = None) -> float:
    ys = [b.y + b.h for b in blocks if col is None or column(b) == col]
    return max(ys, default=STACK_Y - STACK_GAP) + STACK_GAP


def root_of(block: Block) -> Block:
    while block.parent is not None:
        block = block.parent
    return block


def terminates(block: Block) -> bool:
    """True when control never continues to the next sibling."""
    return block.kind in model.LANGUAGES[block.lang].terminators


def is_loop(block: Block) -> bool:
    return block.kind in model.LANGUAGES[block.lang].loop_kinds


def enclosing_loop(block: Block) -> Block | None:
    lang = model.LANGUAGES[block.lang]
    cur = block.parent
    while cur is not None and cur.kind not in lang.def_kinds:
        if cur.kind in lang.loop_kinds and block is not cur:
            return cur
        cur = cur.parent
    return None


def _contains(rect, px, py):
    x1, y1, x2, y2 = rect
    return x1 <= px <= x2 and y1 <= py <= y2


def block_at(blocks, px: float, py: float) -> Block | None:
    """Deepest block whose rectangle contains the point."""
    best, best_area = None, None
    for b in model.walk(blocks):
        if _contains((b.x, b.y, b.x + b.w, b.y + b.h), px, py):
            area = b.w * b.h
            if best is None or area < best_area:
                best, best_area = b, area
    return best


def drop_target(blocks, px: float, py: float, moving: Block | None):
    """(container, slot, index) for the deepest slot under the point."""
    best, best_area = None, None
    for b in model.walk(blocks):
        if moving is not None and (b is moving or model.is_ancestor(moving, b)):
            continue
        for slot, rect in b.slot_rects.items():
            if _contains(rect, px, py):
                area = (rect[2] - rect[0]) * (rect[3] - rect[1])
                if best is None or area < best_area:
                    best, best_area = (b, slot), area
    if best is None:
        return None
    b, slot = best
    index = sum(1 for k in b.slots.get(slot, [])
                if k is not moving and k.y + k.h / 2 <= py)
    return b, slot, index
