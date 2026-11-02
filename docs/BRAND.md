# Brand

The web and mobile clients follow the visual identity of UNDB (Centro Universitário Dom Bosco),
the institution where this project started as a course assignment. This is an unofficial
academic project: it is not affiliated with UNDB and UNDB does not endorse it.

## Sources

| Asset | Source | Used for |
| --- | --- | --- |
| Color logo, 204x62 PNG | https://undb.edu.br/wp-content/uploads/2024/11/logo.png (`2024-11-logo.png`) | Light backgrounds (operator login) |
| White logo, 1209x363 PNG | https://undb.edu.br/, media upload of June 2026 (`2026-06-logo-branca.png`; exact file URL to be confirmed) | Dark header (#770131) |
| Favicon, 192x192 PNG | https://undb.edu.br/wp-content/uploads/2024/11/cropped-favicon-pos-192x192.png (`favicon-192.png`, white grid on #93070C) | Browser tab icon, Expo icon and splash |
| Color tokens | Elementor kit CSS, https://undb.edu.br/wp-content/uploads/elementor/css/post-8.css (`.elementor-kit-8` global colors) | Design tokens below |

The site publishes PNG logos only (no SVG), so the apps ship the PNGs. The web app keeps them in
`apps/web/public/brand/`. Every logo has the alt text "UNDB". The co-branded white logo
(`2025-07-LOGO-UNDB-BRANCA.png`) is not used.

## Tokens

Contrast ratios follow WCAG 2.x and are measured against the background each token is used on.
AA needs 4.5 for normal text and 3 for large text and graphics.

| Token | Value | Kit origin | Use | Contrast |
| --- | --- | --- | --- | --- |
| primary | #CF004E | primary | Buttons, focus ring, links on light tint | White text on it 5.58; on white 5.58 |
| primary-strong | #770131 | global color | Header background | White text on it 11.46 |
| primary-tint | #FCE8EF | derived (light tint of primary) | Info banners | Ink 16.06, primary-strong 9.78, muted 6.03 |
| purple | #7A1F5C | global color | Second chart series | White text on it 9.64 |
| ink | #1B0D17 | derived | Body text | 18.81 on white, 16.84 on surface-alt |
| muted | #54595F | secondary | Secondary text, axis labels | 7.07 on white, 6.33 on surface-alt |
| accent | #FFBA00 | accent | Active nav underline, skip link text, focus ring in the header | 10.99 on ink, 6.70 on primary-strong |
| surface | #FFFFFF | | Cards, panels | |
| surface-alt | #F6F1F4 | derived | Page background | See ink and muted |

Status colors are additions, not part of the brand. They mark a lot as free, busy or full, always
next to a text label so color is never the only signal:

| Token | Value | Contrast on white |
| --- | --- | --- |
| status-free | #1F7A4D | 5.32 |
| status-busy | #9A5B00 | 5.43 |
| status-full | #B42318 | 6.57 |

A lot is full when no space is free or occupancy is at least 95%, busy from 75%, otherwise free.
Each app keeps this rule in one helper (`apps/web/src/lib/status.ts` on the web).

## What was adjusted and why

- The kit's text color #7A7A7A has a contrast of 4.29 on white, below AA for body text. It is not
  used for text; secondary text uses #54595F (the kit's secondary color, 7.07).
- The accent #FFBA00 is 1.7 on white, so it never appears as text or as a thin graphic on white.
  It is used only on ink or on #770131.
- The status colors were added because the kit has no green, amber or red that passes AA as
  text on white.
- Chart series use the brand colors first and then the status colors, all at 3:1 or more on white.

## Fonts

Montserrat (600 and 700) for headings and Roboto (400 and 500) for body text, as on the site. The
web app bundles them with `@fontsource/montserrat` and `@fontsource/roboto` (no request to a font
CDN).

## Unofficial-project notice

Every screen footer and the operator login screen show, in pt-BR:

> Projeto acadêmico não oficial, sem vínculo com a UNDB nem endosso dela.

The README carries the same notice in English.

## Mobile

The mobile app uses the same tokens, thresholds, fonts and notice; its theme lives in
`apps/mobile/src/theme.ts`.
