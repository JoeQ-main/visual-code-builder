# Visual Code Builder — design

Date: 2026-10-15

## Intent (from the request)

A desktop program that opens a Python source file and shows it as a canvas of
drag-and-drop blocks. Every statement becomes a block; nested statements are
drawn as blocks inside their parent block. Solid lines show control flow between
blocks; dashed lines show potential flow such as a call to a function defined
elsewhere on the canvas. Every block is editable, blocks can be created from a
side tab of reserved words, blocks can optionally show descriptions of their
inputs and outputs, and the program can browse the file system, open any
Python file, and save or create files.

## Assumptions made without asking

- **Stack:** Python 3 with tkinter. It ships with Python, needs no install,
  and provides the canvas, file dialogs, and tree views needed.
- **Granularity:** one block per *statement*. Expressions inside a statement
  (conditions, call arguments, values) are editable text fields on the block,
  not sub-blocks. Expression-level blocks would make real files unreadable.
- **Round trip:** the file is parsed with the `ast` module and regenerated from
  the block tree. Comments and blank lines are not preserved; formatting is
  normalised. The generated code is shown live and syntax-checked.
- **Ordering:** module-level blocks are emitted top-to-bottom by their canvas
  position. Inside a container, children are emitted in slot order.
- **Control flow lines:** solid arrows go from a block to the next block in the
  same slot and from a container to the first block of each of its slots.
  Dashed lines go from any block whose fields call a name to the `def` or
  `class` block with that name.

## Modules

| Module | Responsibility |
|---|---|
| `vcb/model.py` | `Block` tree, block specs per kind, parse source → blocks, blocks → source, call-edge detection, default input/output descriptions. No tkinter. |
| `vcb/layout.py` | Sizes and absolute positions for every block, recursively. No tkinter. |
| `vcb/canvas.py` | Canvas drawing, selection, drag and drop, re-parenting, arrows. |
| `vcb/editor.py` | Right-hand property panel for the selected block. |
| `vcb/palette.py` | Left-hand tabs: reserved-word palette and file explorer tree. |
| `vcb/app.py` | Main window, menus, open/save/new, live code preview, status bar. |
| `main.py` | Entry point. |
| `tests/` | Parse/regenerate round-trip tests and layout tests. |

## Block kinds

Statement kinds: `def`, `class`, `if`, `elif`, `for`, `while`, `try`,
`except`, `with`, `match`, `case`, `return`, `assign`, `augassign`,
`annassign`, `call`, `expr`, `pass`, `break`, `continue`, `raise`, `assert`,
`import`, `from`, `global`, `nonlocal`, `del`, `type`.

Slots (nested bodies): `body`, `orelse`, `handlers`, `finalbody`, `cases`.

Palette entries exist for every hard keyword and the soft keywords `match`,
`case`, `type`. Keywords that are not statements on their own (`and`, `or`,
`not`, `in`, `is`, `lambda`, `yield`, `await`, `True`, `False`, `None`, `as`,
`async`, `else`, `finally`) create a sensible host block: an expression block
with a template, or they add the matching slot to the selected container.

## Testing

`pytest` over the model and layout: every statement kind round-trips through
parse → generate → parse with an equal AST dump; call edges are found; the
layout nests children inside parents. The GUI is checked by running it.
