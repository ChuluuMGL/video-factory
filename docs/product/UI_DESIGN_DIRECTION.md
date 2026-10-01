# Installation guide design direction

## Product focus

- Route: Video Factory installation guide (`delivery/site`).
- Audience: a first-time customer or the colleague installing for them.
- First viewport: understand the product's video workflow and find the installation entry.
- Context: desktop and mobile product guide. Preserve YUEYU's navy/blue identity, plain HTML/CSS/JS, private release access and truthful test status.

## Visual thesis

For a first-time installer, the page makes the path from product information to an approved video legible through one prominent workflow illustration, large type and a single installation panel.

- Hero object: a four-stage, text-based workflow — product information, script, video, human review/download. Original HTML/CSS, no third-party imagery or license dependency. It describes a process, not a real completed task or a fabricated customer result.
- Avoid small-text architecture diagrams, repeated instructions, decorative letters, nested cards and distracting motion.

## Visual system

| Element | Decision |
|---|---|
| Base | `#02040a`, restrained navy surfaces `#0d1422` |
| Accent | Brand blue `#3b82f6`; white-on-`#2563eb` actions; light blue focus |
| Type | Native system sans, no font downloads. Desktop headline 44–64 px, section titles 32–44 px, body 18 px, supporting copy at least 16 px |
| Grid | Maximum 1280 px including 48 px margins; asymmetric hero; clear left alignment |
| Surfaces | One installation panel, thin separators and generous section spacing; no decorative nested boxes |

## Reading path

Product promise → four-stage workflow → concrete installation outcomes → highlighted download/copy actions → later projects → test status and support. Full Agent instructions, detailed setup steps, release history and server requirements remain available through native disclosure controls. Readiness, costs and material limitations remain visible or explicitly linked.

## Responsive behavior

- Narrow mobile (320–390 px): one reading column, 32–44 px headline, 18 px body, full-width actions, two-column scenario selector. Workflow below primary action.
- Tablet (820 px): stack hero and install columns; preserve font sizes rather than shrinking everything.
- Desktop (1440 px): asymmetric hero, three outcomes, install instructions beside preparation checklist.

## Motion and accessibility

Only short button color feedback (180 ms) and anchor scrolling. Reduced motion disables transitions and smooth scrolling. Visible keyboard focus, native details/summary, text labels and minimum 44 px controls. Copy fallback opens the instruction disclosure before selecting text.

## Review gate

Cloud-only Chrome checks at 320, 390, 820 and 1440 px, copy/download/scenario behavior, expanded disclosures and screenshots. These checks do not establish customer installation or real-model acceptance. No change to installation contract, account permissions, DNS or the official website production repository.
