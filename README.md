# KRMP Contrast Checker — v1.0.0

Checks every text element on every page for readable colour contrast (WCAG AA: 4.5:1 normal text,
3:1 large text). It works out each element's final text colour and the background really behind it,
including inheritance, CSS variables, semi-transparent layers, gradients, opacity, and the floating
header in all three states (top of page, scrolled over light, scrolled over dark).

## Use
```bash
pip install beautifulsoup4 lxml          # once
cd path/to/website-folder
python3 contrast_lint.py                  # checks every *.html in the folder
python3 contrast_lint.py about.html       # or specific pages
python3 test_linter.py                    # the checker's own 24 tests
```
Exit output lists each failing combination (text colour on background, ratio, page, where) and writes
`contrast_report.json`. **Run it whenever you add or restyle a page.** 0 failures = nothing it can see is unreadable.

## What it cannot see (so still look with your own eyes)
Hover/focus colours, `@media` (phone-only) overrides, text drawn by `::before/::after`, text over photographs,
and anything changed by JavaScript other than the header. It also cannot judge whether a layout *looks* balanced —
only whether text is readable.

## History
It found, among others: a header invisible on four blog pages (1.0:1), white headings on white value cards,
and footer text that passed on black but not on green. See the website changelog (v1.5.0).
