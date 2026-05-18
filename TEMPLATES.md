# PO Template Authoring Guide

This document covers how to build an `.xlsx` template that POThang will
render into a finished purchase order. It lists every template token,
shows how the loop region expands, and walks through worked examples
including Excel formulas that reference the expanded line-item rows.

A ready-to-edit sample lives at `sample_template/po_template.xlsx`.

## How rendering works

POThang reads your template with [openpyxl] and walks every worksheet
twice:

1. **Loop expansion.** If the worksheet contains a `{{#items}}` /
   `{{/items}}` pair, the rows between the markers are treated as a
   per-line template. Those rows are duplicated once per PO line; the
   marker rows are deleted; rows below shift up or down by the net
   delta. Cell formatting, row heights, and merged ranges inside the
   block are preserved per copy.
2. **Named-cell substitution.** Every cell on the sheet is then scanned
   for `{{...}}` placeholders and rewritten in place. Cells that hold
   Excel formulas (anything whose text starts with `=`) are
   substituted the same way, which is how formulas pick up the row
   numbers the loop produced.

Lines are emitted in `line_no` order — the same `#` you see in the web
UI.

[openpyxl]: https://openpyxl.readthedocs.io/

## Token reference

### PO-level tokens (any cell, any sheet)

| Token              | Source                          | Notes                                    |
|--------------------|---------------------------------|------------------------------------------|
| `{{po_number}}`    | `PurchaseOrder.po_number`       | Empty string if unset.                   |
| `{{vendor}}`       | `PurchaseOrder.vendor`          |                                          |
| `{{ship_to}}`      | `PurchaseOrder.ship_to`         |                                          |
| `{{notes}}`        | `PurchaseOrder.notes`           |                                          |
| `{{date}}`         | `ordered_at` or `created_at`    | Formatted `YYYY-MM-DD`.                  |
| `{{total}}`        | `PurchaseOrder.total`           | Numeric — written as a number, not text. |
| `{{revision}}`     | Document revision passed in     | Empty if no revision was requested.      |

### Line-item tokens (inside `{{#items}}` / `{{/items}}`)

| Token                  | Source                              |
|------------------------|-------------------------------------|
| `{{item.name}}`        | `Item.name`                         |
| `{{item.description}}` | `Item.description`                  |
| `{{item.model}}`       | `Item.model`                        |
| `{{item.vendor}}`      | `Item.vendor`                       |
| `{{item.vendor_sku}}`  | `Item.vendor_sku`                   |
| `{{item.url}}`         | `Item.url`                          |
| `{{item.qty}}`         | `POLine.qty`                        |
| `{{item.unit_cost}}`   | `POLine.unit_cost`                  |
| `{{item.line_total}}`  | `POLine.line_total`                 |
| `{{item.index}}`       | `POLine.line_no` (stable per-PO #)  |
| `{{item.notes}}`       | `POLine.notes`                      |
| `{{row}}`              | Absolute row number of this line    |

A purely numeric substitution (`{{item.qty}}`, `{{item.line_total}}`,
`{{total}}`, …) is written to the cell as a number so SUM/AVERAGE can
reach it. Mixed text (`"Qty: {{item.qty}}"`) stays a string.

### Block markers and control flow

| Token                                 | Meaning                                                |
|---------------------------------------|--------------------------------------------------------|
| `{{#items}}` … `{{/items}}`           | Defines the line-item loop region (markers consumed).  |
| `{{#if var}}` … `{{/if}}`             | Renders body iff `var` is truthy.                      |
| `{{#if var}}` … `{{else}}` … `{{/if}}`| Two-branch conditional. Else branch is optional.       |

The `{{#if}}` block does not nest and must live inside a single cell —
opener, body, and closer are all in the same cell's text.

`var` may be any token name. Common patterns:

- `{{#if item.vendor_sku}}{{item.model}}/{{item.vendor_sku}}{{else}}{{item.model}}{{/if}}`
- `{{#if revision}}Rev {{revision}}{{/if}}`
- `{{#if items}}=SUM({{items.range.E}}){{else}}0{{/if}}` (see below)

### Formula tokens (for cells with `=` formulas)

| Token                  | Where               | Resolves to                                                 |
|------------------------|---------------------|-------------------------------------------------------------|
| `{{row}}`              | Inside the loop     | Absolute row number of the current line.                    |
| `{{items.first_row}}`  | Anywhere            | First row of the expanded items block. `""` if empty PO.    |
| `{{items.last_row}}`   | Anywhere            | Last row of the expanded items block. `""` if empty PO.     |
| `{{items.count}}`      | Anywhere            | Number of line items (integer).                             |
| `{{items.range.X}}`    | Anywhere            | `X<first_row>:X<last_row>` (e.g. `E5:E10`). `0` on empty PO.|
| `{{relative:DR:DC}}`   | Anywhere            | A1 reference offset `DR` rows / `DC` cols from this cell.   |
| `{{#if items}}…{{/if}}`| Anywhere            | True iff the PO has at least one line.                      |

`{{relative:DR:DC}}` is the portable replacement for
`INDIRECT(ADDRESS(ROW()+DR, COLUMN()+DC))` — the in-browser editor
cannot evaluate `INDIRECT`/`ADDRESS`, so use this token instead. `DR`
and `DC` are signed offsets resolved against the cell's **final**
position (after the items block has expanded). Example: in cell `I26`,
`=SUM({{relative:-3:0}}:{{relative:-1:0}})` becomes `=SUM(I23:I25)`. An
offset that lands off-sheet (row/col &lt; 1) is left as the literal
token.

`{{items.range.X}}` accepts any column letter, including multi-letter
columns (`AA`, `BC`, …). The collapse-to-`0` behaviour means
`=SUM({{items.range.E}})` and `=AVERAGE({{items.range.E}})` stay valid
on an empty PO without a guard; if you need a different fallback, wrap
the formula in `{{#if items}}…{{else}}…{{/if}}`.

Formulas are otherwise opaque to the renderer — POThang does not parse
or rewrite cell references. Anything you need to vary across the loop
must use `{{row}}`.

## Worked examples

The examples below use concrete row numbers from this template skeleton:

```
Row 1   PO: {{po_number}}                                Vendor: {{vendor}}
Row 2   Ship to: {{ship_to}}                             Date: {{date}}
Row 3
Row 4   #             Description                   Qty   Unit       Line total
Row 5   {{#items}}
Row 6   {{item.index}}  {{item.description}}        {{item.qty}}  {{item.unit_cost}}  =C{{row}}*D{{row}}
Row 7   {{/items}}
Row 8                                               Subtotal       =SUM({{items.range.E}})
Row 9                                               Tax (8.25%)    =SUM({{items.range.E}})*0.0825
Row 10                                              Total          =E8+E9
```

### 1. Per-line math with `{{row}}`

Cell `E6` holds `=C{{row}}*D{{row}}`. After expansion with two PO
lines, the marker rows are gone and what was row 6 becomes rows 5 and
6:

```
Row 5   1  Widget A    2   10.50   =C5*D5
Row 6   2  Widget B    3    2.00   =C6*D6
```

Use `{{row}}` whenever a formula needs to point at another cell on the
same line. Plain numeric tokens like `{{item.line_total}}` work fine
too if you just want the computed value — `{{row}}` exists for cases
where Excel needs to do the math (so the user can tweak `qty` in the
finished file and the line total recomputes).

### 2. Aggregates over the expanded range

Cell `E8` holds `=SUM({{items.range.E}})`. With items expanded into
rows 5–6 the template becomes:

```
Row 7   Subtotal     =SUM(E5:E6)
Row 8   Tax (8.25%)  =SUM(E5:E6)*0.0825
Row 9   Total        =E7+E8
```

(Note that `E10`'s `=E8+E9` shifted along with the rest of the rows
below the loop — Excel's built-in reference adjustment handles that
when openpyxl deletes/inserts rows, *unless* a reference straddles the
loop block. Keep formulas that reach into the loop using
`{{items.range.X}}` or `{{row}}` rather than literal cell refs.)

### 3. Empty PO

If the PO has no lines, the loop block is removed and rows below shift
up. `{{items.range.E}}` collapses to `0`, so:

```
Row 4   Subtotal     =SUM(0)
Row 5   Tax (8.25%)  =SUM(0)*0.0825
Row 6   Total        =E4+E5
```

All three cells evaluate to `0` — no `#REF!` errors.

### 4. Conditional model/SKU display

In a single cell inside the loop:

```
{{#if item.vendor_sku}}{{item.model}} ({{item.vendor_sku}}){{else}}{{item.model}}{{/if}}
```

Rendered:
- Line with both → `MDL-1 (SKU-99)`
- Line with only model → `MDL-1`
- Line with neither → empty string

### 5. Revision marker that only appears on revised docs

In a header cell anywhere on the sheet:

```
{{#if revision}}Revision {{revision}}{{/if}}
```

When the renderer is called without a revision, the cell ends up
empty.

### 6. Hand-built range using boundary tokens

If you need a non-aggregate formula over the items region, you can
splice the boundaries directly:

```
=INDEX(D{{items.first_row}}:D{{items.last_row}}, MATCH(MAX({{items.range.E}}), {{items.range.E}}, 0))
```

(Finds the description of the highest-cost line.) On an empty PO
`items.first_row` and `items.last_row` are empty strings, which would
produce a broken formula — guard with `{{#if items}}` if this matters.

## Authoring tips and pitfalls

- **Markers consume their row.** The `{{#items}}` and `{{/items}}` rows
  themselves are deleted, not preserved. Don't put header text on a
  marker row.
- **One loop per sheet.** Only the first `{{#items}}` / `{{/items}}`
  pair on a sheet is expanded. Additional pairs are ignored.
- **Multiple sheets are fine.** Each worksheet is processed
  independently and gets its own `{{items.range.X}}` resolution.
- **Don't span the loop with literal cell references.** A formula like
  `=E4+E12` that points across the loop block can't be safely shifted
  when the block grows or shrinks. Anchor anything that reaches into
  the items region with `{{items.range.X}}` or `{{row}}`.
- **Merged cells.** Merges fully inside the loop block are replicated
  per line. Merges fully outside are row-shifted with everything else.
  Merges that straddle the loop boundary are dropped — split them.
- **Numeric coercion.** A cell containing only a numeric placeholder
  (e.g. `{{item.qty}}`) is written as a number. To force a string,
  prepend any non-numeric text (`"# {{item.qty}}"`).
- **Unknown placeholders are left alone.** If you typo `{{vender}}`
  it'll appear verbatim in the output, which is usually how you'll
  spot the typo.
