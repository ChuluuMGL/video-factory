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
| Base | `#02040a`, official AI surfaces `#0f1219` / `#0f1623` |
| Accent | Brand blue `#3b82f6`; white-on-`#2563eb` actions; light blue focus |
| Type | Exact official Simplified Chinese system font stack, including Hiragino Sans GB and Noto Sans SC; no webfont downloads. Headline `clamp(1.9rem, 7.8vw, 4.5rem)`, weight 500, line-height 1.2 and tracking −0.03em. Section titles 30–48 px; body 18 px and supporting copy at least 16 px |
| Grid | Maximum 1152 px of content plus 24 px gutters; asymmetric hero; clear left alignment |
| Surfaces | 28 px installation panel corners, pill actions, 12 px inputs, thin separators and 64–96 px section spacing; no decorative nested boxes |

## Reading path

Product promise → four-stage workflow → concrete installation outcomes → highlighted download/copy actions → later projects → test status and support. Full Agent instructions, detailed setup steps, release history and server requirements remain available through native disclosure controls. Readiness, costs and material limitations remain visible or explicitly linked.

## Responsive behavior

- Narrow mobile (320–390 px): one reading column, official responsive headline, 18 px body, full-width actions, two-column scenario selector. Workflow below primary action.
- Tablet (820 px): stack hero and install columns; preserve font sizes rather than shrinking everything.
- Desktop (1440 px): asymmetric hero, three outcomes, install instructions beside preparation checklist.

## Motion and accessibility

Only short button color feedback (180 ms) and anchor scrolling. Reduced motion disables transitions and smooth scrolling. Visible keyboard focus, native details/summary, text labels and minimum 44 px controls. Copy fallback opens the instruction disclosure before selecting text.

## Review gate

Cloud-only Chrome checks at 320, 390, 820 and 1440 px, copy/download/scenario behavior, expanded disclosures and screenshots. These checks do not establish customer installation or real-model acceptance. No change to installation contract, account permissions, DNS or the official website production repository.

## Official-site parity

Reference: https://www.yueyu.tech/zh/solutions/ai-workflow-models (hydrated AI page, not its SEO fallback). Installation, setup, credential wizard and employee review surfaces share its font family, medium heading weight, white/gray text, navy backgrounds and blue accent. Task forms retain smaller headings appropriate to their layout. Guide controls remain larger than the marketing site's 14 px controls, and white primary-action labels use #2563eb for contrast. These are intentional readability adaptations.

The cloud `brand-parity-smoke.py` compares the live official page and guide in the same Chrome at 1440 and 390 px: computed heading family/size/weight/leading/tracking, accent/background and actual rendered Chinese font. It also verifies the shipped application stylesheets using synthetic text. The regular cloud regression covers actual installed UI behavior. Screenshots and computed styles are retained as evidence; a failed or unavailable live reference must not be described as verified parity. This does not deploy either website.
