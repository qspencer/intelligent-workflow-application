# UI Polish & Accessibility checklist

Practical reference for the canvas roadmap's **C8 (polish + a11y)** cut, with the bits that
also serve **C6 (the trust wedge)** called out. Web SPA only (Vite + React). Distilled from
`affaan-m/ECC` (MIT) — `make-interfaces-feel-better`, `frontend-a11y`, `accessibility`.

Use it two ways: as a **PR checklist** for any interactive component, and as the **how-to**
when a surface "feels off" or fails an a11y pass. Design tokens already exist in
`frontend/src/styles.css` (`--bg`, `--panel`, `--border`, `--text`, `--muted`, `--accent`,
`--ok`, `--warn`, `--err`, `--skip`) — reach for those, don't hardcode.

---

## Part A — Polish (the small details that compound)

- **Concentric radius.** Nested rounded surfaces: `outer = inner + padding`. If padding is
  large, treat them as separate surfaces instead of forcing the math.
- **Optical alignment.** Geometric center ≠ visual center for asymmetric glyphs (play
  triangles, chevrons). Fix the SVG; else nudge with a pixel of margin.
- **Borders vs shadows.** Borders for separation + focus rings; subtle layered shadows for
  depth (cards, popovers, dropdowns). Keep shadows translucent so they survive any background.
- **Text wrapping.** `text-wrap: balance` on headings/short titles; `text-wrap: pretty` on
  captions/descriptions/list items; neither on long prose or code.
- **Tabular numbers — C6.** `font-variant-numeric: tabular-nums` on every updating number:
  the **cost meter, token counter, run counts, durations**. Without it the digits jitter as
  values change — the single cheapest fix for the trust-wedge meters feeling unstable.
- **Image outlines.** A `1px` neutral alpha `outline` with `outline-offset: -1px` stops image
  edges blurring into the panel. Neutral black/white alpha — never tint with `--accent`.
- **Motion.** Use CSS **transitions** for interactive state changes (they retarget when the
  user changes intent mid-motion); reserve keyframes for one-shot entrances/loaders. Enter =
  opacity + small `translateY`; exit = shorter/quieter (~150ms); press = `scale(0.96)`.
- **Transition scope — never `transition: all`.** Name the properties; it's both a perf and a
  correctness rule.
  ```css
  .button {
    transition-property: transform, background-color, box-shadow;
    transition-duration: 150ms;
    transition-timing-function: ease-out;
  }
  ```
  Use `will-change` only for first-frame stutter on compositor props (`transform`/`opacity`/
  `filter`); never `will-change: all`.
- **Hit areas.** Interactive controls ≥ 40×40px (ideally 44×44). Expand a small icon's target
  with a pseudo-element; don't let expanded targets overlap.

When reporting a polish pass, use before/after rows (`Principle | Before | After`) with file
paths; omit principles you checked but didn't change.

---

## Part B — Accessibility how-to (React)

### Forms (the most-flagged issues)
- **Label ↔ input** via `htmlFor`/`id` (not a wrapping `<label>` alone).
- **Required:** `required aria-required="true"`, and hide the visual `*` with `aria-hidden`.
- **Errors:** link with `aria-describedby`, set `aria-invalid`, and mark the message
  `role="alert"`:
  ```tsx
  <input id="name" aria-invalid={!!error} aria-describedby={error ? 'name-err' : undefined} />
  {error && <span id="name-err" role="alert">{error}</span>}
  ```
  Our Create / Import dialogs are the concrete targets.

### Semantic HTML before ARIA
`<button>`/`<a>`/`<nav>`/`<main>` before `<div onClick>`. A `<div role="button">` needs
`tabIndex={0}` **and** an `onKeyDown` for Enter/Space — a `<button>` gives you all three free.

### ARIA, used sparingly (wrong ARIA is worse than none)
- `aria-label` for an icon-only control; `aria-labelledby` when a visible label exists.
- `aria-live` for content that updates without reload — **C6:** wrap dry-run / explain-this-run
  status in `role="status" aria-live="polite"` (`assertive` only for urgent errors).
- `aria-expanded` + `aria-controls` on disclosure toggles (accordions, the dev-console nav).

### Keyboard + focus
- Every interactive element reachable and operable by keyboard alone; arrow-key handling for
  custom listbox/combobox/menu.
- **Modal focus:** save `document.activeElement` on open, focus the dialog, restore on close,
  close on `Escape`. For real Tab/Shift+Tab trapping use `focus-trap-react` (don't hand-roll
  the edge cases).
- Never a positive `tabIndex`; never `aria-hidden` on a focusable element.

### Images / reduced motion
- Decorative: `alt="" aria-hidden="true"`. Meaningful: descriptive `alt` (no "image of…").
- Respect `prefers-reduced-motion` — gate transitions behind a `useReducedMotion()` hook.

---

## Part C — WCAG 2.2 AA criteria (what "AA" actually requires)

The spec behind Part B. POUR — Perceivable, Operable, Understandable, Robust.

- **Contrast:** text ≥ **4.5:1** (normal), **3:1** (large/UI). Verify our tokens against
  `--bg`/`--panel`.
- **Target size (SC 2.5.8):** ≥ **24×24 CSS px** (our 40–44px polish rule clears this).
- **Focus appearance (SC 2.4.11/2.4.13):** a clearly visible, high-contrast focus indicator —
  don't `outline: none` without a replacement.
- **Reflow:** usable at 400% zoom without loss of function or horizontal scroll.
- **Status messages (SC 4.1.3):** announce via `aria-live`, not just a visual change.
- **Don't encode meaning in color alone** (SC 1.4.1) — pair our `--ok`/`--warn`/`--err` status
  pills with text/icon (the friendly labels already do this).
- **Redundant entry (SC 3.3.7):** don't re-ask for data already provided in a flow.
- **Error suggestions (SC 3.3.3):** say how to fix it, not just that it's wrong.

---

## Combined PR checklist

- [ ] Inputs have connected labels; errors use `aria-describedby` + `role="alert"`.
- [ ] No `onClick` on `<div>`/`<span>` without `role` + `tabIndex` + `onKeyDown`.
- [ ] Icon-only buttons have `aria-label`; decorative images `alt="" aria-hidden`.
- [ ] Modals restore focus on close and close on `Escape` (focus-trap for full cycling).
- [ ] Dynamic status uses `aria-live`; status never color-only.
- [ ] Contrast ≥ 4.5:1; visible focus indicator; ≥ 24px targets.
- [ ] `prefers-reduced-motion` respected; no `transition: all` / `will-change: all`.
- [ ] Updating numbers use `tabular-nums`; nested radii coherent; hit areas ≥ 40px.

**Verify it:** assert with `axe` in component tests (see the `react-testing` skill) — but
remember jsdom has no CSS engine, so **contrast + layout checks belong in Playwright**, not
unit tests.

## See also

- `.claude/skills/react-patterns` (a11y-first composition) · `.claude/skills/react-testing` (axe).
- `docs/CANVAS_ROADMAP.md` — C8 (polish + a11y) and C6 (trust wedge: cost/dry-run surfaces).
