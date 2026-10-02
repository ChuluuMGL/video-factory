# Installation guide design direction

## Product focus

- Route: Video Factory installation guide (`delivery/site`).
- Audience: prospective users, customer administrators and implementation colleagues.
- First viewport: understand the path from product information to scripts and videos, then choose real cases or installation.
- Deliverable: customer-hosted workflows, configured through Setup Skill. The review webpage is an operating interface, not the entire product.

## Visual thesis

Explain the product with concise typography and a single gallery of real project videos, then lead into one installation panel.

- Media: the four anonymous project videos and posters already publicly displayed on the user's official AI services page. The user identified those workflows as the product's origins and requested their reuse. Link to those exact official assets; do not copy private project data or substitute synthetic demo media.
- Provenance: legacy project examples, not evidence that the current installer has passed real-model or independent customer acceptance.
- Avoid decorative hero panels, black test videos, mock apps, performance promises, repeated business pitches and invented project-to-brand attribution.

## Visual system

| Element | Decision |
|---|---|
| Base | Official `#02040a`, surfaces `#0f1219` / `#0f1623` |
| Accent | Brand `#3b82f6`; white-on-`#2563eb` primary action |
| Type | Official Chinese system sans stack, medium headings, no downloaded fonts; headline `clamp(1.9rem, 7.8vw, 4.5rem)` |
| Grid | Maximum 1152 px content, 24 px outer padding, full-width sections |
| Media | Four 9:16 players on desktop; two columns at 850 px and below; contain original frames, native controls, no autoplay |
| Surfaces | One installation panel, quiet separators; no nested promotional cards |

## Reading path

1. Product purpose and three concise components.
2. Four real project videos, one origin sentence and one link to complete official case records.
3. Full Skill download, four operation choices, always-visible Agent instructions and compact top-right copy button.
4. Preparation checklist and five full-width maintenance questions.

The official AI page retains business-wide offerings and case metrics. This page provides the reusable product and installation path. The official product page is now deployed. It links to the MIT public alpha; the customer runtime and model credentials remain on the customer server.

## Responsive behavior and accessibility

Desktop keeps the gallery in one row. Tablet and mobile use two columns with readable labels and full-frame videos. At 320–390 px the primary installation action spans the column; the case link stays secondary. Navigation remains one row. Controls have visible focus and accessible names; native players expose controls and every video has a direct-open fallback. No video preloading or autoplay; 180 ms button feedback and anchor scrolling respect reduced motion.

## Review gate

Cloud Chrome checks at 320, 390, 820 and 1440 px: no horizontal overflow, copy/fallback/download, four operations, real video metadata and advancing playback, source/poster parity with the hydrated official page, and screenshots. Separate existing brand checks compare computed typography and actual Chinese fonts against the live official AI page. These checks do not prove customer access, independent installation or real-model acceptance.

## Reference

https://www.yueyu.tech/zh/solutions/ai-workflow-models

This direction supersedes older workflow mockups and the screenshot-demo layout. Preserve published installer and Skill hashes; page source and legacy-case provenance are recorded separately. The current public release is recorded in STATUS.md.
