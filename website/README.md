# Doppel website redesign

Drop-in replacement for these files in `website/`:

- src/app/globals.css, src/app/page.tsx
- src/components/landing/*  (new: bits, Marquee, StatsBand, PromptDemo, DashboardPreview; rewritten: the rest)

Delete `src/components/landing/BeforeAfterCard.tsx` (no longer used). No new dependencies.
`layout.tsx`, `lib/site.ts` and `ui/*` are unchanged.
