# Doppel

A Next.js 16 + TypeScript + Tailwind CSS 4 marketing website for the **AI Privacy Gateway** (Doppel) project — a local privacy layer that intercepts prompts and file uploads before they reach AI tools, masks sensitive data on the user's device with realistic stand-ins, and restores the real values in the reply.

## Stack

- **Framework:** Next.js 16 (App Router)
- **Language:** TypeScript 5
- **Styling:** Tailwind CSS 4
- **Components:** shadcn/ui (New York style) + Lucide icons
- **Animations:** Framer Motion (subtle scroll-reveal + hover lift only — no gimmicks)
- **Fonts:** Fraunces (serif headlines) + Inter (body) + JetBrains Mono (code) — loaded via `next/font`

## Palette — Graphite and Emerald

| Token | Hex | Role |
|------|-----|------|
| `--bg` | `#0C0D0C` | graphite black — page background |
| `--surface` | `#16181A` | elevated graphite — cards |
| `--border` | `#262827` | hairline borders |
| `--ink` | `#E8E8E5` | warm off-white — headlines |
| `--body` | `#A8A8A3` | muted warm grey — body text |
| `--primary` | `#2FD58F` | **THE accent** — emerald (single accent) |
| `--danger` | `#B85450` | muted brick red — blocked states only |

All tokens are defined as CSS variables in `src/app/globals.css` and aliased into Tailwind via `@theme inline`.

## Getting started

```bash
# Install dependencies
npm install
# or: pnpm install / bun install / yarn install

# Start the dev server
npm run dev

# Build for production
npm run build && npm start
```

The site runs on `http://localhost:3000`.

## Project structure

```
.
├── src/
│   ├── app/
│   │   ├── layout.tsx       # Root layout — loads Fraunces + Inter + JetBrains Mono
│   │   ├── page.tsx         # Landing page (assembles all 10 sections)
│   │   ├── globals.css      # Tailwind + Graphite+Emerald palette + utility classes
│   │   └── api/route.ts     # Default route handler (placeholder)
│   └── components/
│       ├── landing/         # 14 marketing components
│       │   ├── Navbar.tsx
│       │   ├── ScrollProgress.tsx
│       │   ├── Hero.tsx
│       │   ├── BeforeAfterCard.tsx    # The hero centerpiece visual
│       │   ├── Reveal.tsx             # Scroll-reveal helper
│       │   ├── Pillars.tsx            # Intercept / Mask / Restore
│       │   ├── Difference.tsx
│       │   ├── PIITypes.tsx           # What we protect
│       │   ├── FileFormats.tsx
│       │   ├── Architecture.tsx
│       │   ├── Testimonials.tsx
│       │   ├── FAQ.tsx
│       │   ├── CTA.tsx
│       │   └── Footer.tsx
│       └── ui/              # shadcn/ui components (Toaster, Accordion, etc.)
├── public/                 # logo.svg + robots.txt
├── package.json
├── tailwind.config.ts
├── postcss.config.mjs
├── tsconfig.json
├── next.config.ts
├── components.json
└── eslint.config.mjs
```

## Page sections

1. **Hero** — eyebrow → "Use AI without exposing your data." → subline → one emerald Book a demo button + one text link → trust line (GDPR · EU AI Act · HIPAA) → before/after card
2. **Pillars** — Intercept / Mask / Restore (three steps, hairline cards)
3. **Difference** — four short value props
4. **Coverage** — four categories of data Doppel masks
5. **File formats** — TXT / PDF / DOCX / XLSX
6. **Architecture** — Browser / Your device / Optional cloud
7. **Testimonials** — three placeholder quotes (replace with real ones)
8. **FAQ** — six common questions
9. **CTA** — final "Book a demo" call
10. **Footer** — brand + Product / Resources / Company columns

## Design rules

- **One emerald button per screen.** Everything else is hairline borders + text.
- **No glow effects, no second accent color, no animated gimmicks.** Just clean fade-ins, hover lifts, and a scroll progress bar.
- **Fraunces serif for headlines (weight 500, no italic).** Inter for everything else.
- **1120px max content width. 96–128px vertical spacing between sections. 12px corner radii.**
- **Hairline borders only.** No heavy shadows.

## License

See the parent repo's LICENSE.
