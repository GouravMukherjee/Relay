---
name: Relay Design System
colors:
  surface: '#fbf8fc'
  surface-dim: '#dcd9dd'
  surface-bright: '#fbf8fc'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f6f2f7'
  surface-container: '#f0edf1'
  surface-container-high: '#eae7eb'
  surface-container-highest: '#e4e1e6'
  on-surface: '#1b1b1e'
  on-surface-variant: '#464555'
  inverse-surface: '#303033'
  inverse-on-surface: '#f3f0f4'
  outline: '#777587'
  outline-variant: '#c7c4d8'
  surface-tint: '#4d44e3'
  primary: '#3525cd'
  on-primary: '#ffffff'
  primary-container: '#4f46e5'
  on-primary-container: '#dad7ff'
  inverse-primary: '#c3c0ff'
  secondary: '#006c49'
  on-secondary: '#ffffff'
  secondary-container: '#6cf8bb'
  on-secondary-container: '#00714d'
  tertiary: '#7e3000'
  on-tertiary: '#ffffff'
  tertiary-container: '#a44100'
  on-tertiary-container: '#ffd2be'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e2dfff'
  primary-fixed-dim: '#c3c0ff'
  on-primary-fixed: '#0f0069'
  on-primary-fixed-variant: '#3323cc'
  secondary-fixed: '#6ffbbe'
  secondary-fixed-dim: '#4edea3'
  on-secondary-fixed: '#002113'
  on-secondary-fixed-variant: '#005236'
  tertiary-fixed: '#ffdbcc'
  tertiary-fixed-dim: '#ffb695'
  on-tertiary-fixed: '#351000'
  on-tertiary-fixed-variant: '#7b2f00'
  background: '#fbf8fc'
  on-background: '#1b1b1e'
  surface-variant: '#e4e1e6'
typography:
  h1:
    fontFamily: Geist
    fontSize: 30px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.02em
  h2:
    fontFamily: Geist
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  h3:
    fontFamily: Geist
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Geist
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-caps:
    fontFamily: Geist
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
  mono-sm:
    fontFamily: Geist Mono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
  container-max: 1440px
  gutter: 20px
---

## Brand & Style

The design system is engineered for high-stakes, real-time B2B environments where clarity and latency-reduction are paramount. The brand personality is authoritative yet invisible—a "silent co-pilot" that prioritizes information density and legibility over decorative elements.

Drawing heavily from **Modern Corporate Minimalism**, the aesthetic utilizes a "Functional White" workspace. The emotional response is one of professional calm and technical precision. By employing a restricted palette, hairline borders, and generous whitespace, the interface directs the user's focus entirely toward the live call data and AI-generated insights. All elements follow a strict structural grid to ensure a "production-grade" feel reminiscent of elite developer tools.

## Colors

The color strategy is strictly functional, utilizing high-contrast neutrals to establish a clear information hierarchy.

- **Primary (Indigo #4F46E5):** Reserved for primary actions, active states, and focus indicators.
- **Success (Emerald #10B981):** Used for "Live" status indicators, verified transcriptions, and positive AI confidence intervals.
- **Grayscale Hierarchy:** #18181B for maximum readability in body text; #71717A for supporting metadata; #A1A1AA for placeholder states and non-interactive icons.
- **Surfaces:** Use #FFFFFF for the primary canvas. Use #FAFAFA for sidebars and #F4F4F5 for inset code blocks or secondary utility panels.

## Typography

This design system uses **Geist** for its technical precision and exceptional legibility at small sizes. The typographic scale is optimized for a data-dense "dashboard" environment.

- **Micro-labels:** Use `label-caps` for table headers, section titles in sidebars, and overline text.
- **Monospaced Accents:** For timestamps, confidence scores, and technical IDs, use a monospaced variant to emphasize the "real-time data" nature of the product.
- **Contrast:** Ensure all text-on-background combinations meet a minimum of 4.5:1 (WCAG AA) to maintain accessibility during fast-paced live calls.

## Layout & Spacing

The layout utilizes a **Fixed Grid** approach for the main content area (max 1440px) to ensure predictive eye-tracking, while sidebar panels for live transcripts are fluid.

- **Grid:** A 12-column grid system is used for dashboard layouts.
- **The 4px Rule:** All spacing (padding, margins, gaps) must be a multiple of 4px to maintain vertical rhythm.
- **Responsive Behavior:** 
  - **Desktop:** 3-column layout (Navigation | Main Feed | AI Insights).
  - **Tablet:** 2-column layout (Main Feed | AI Insights), Navigation moves to a collapsed rail.
  - **Mobile:** Single column stack. AI insights become expandable bottom sheets.

## Elevation & Depth

This design system avoids heavy shadows and deep stacking. Depth is primarily communicated through **low-contrast outlines** and subtle tonal shifts.

- **Borders:** Every container and card is defined by a 1px hairline border (#E4E4E7). This provides structure without the visual weight of shadows.
- **Shadow-sm:** Only used on floating elements like dropdown menus, tooltips, or active modals. Use a crisp, neutral shadow: `0 1px 2px 0 rgba(0, 0, 0, 0.05)`.
- **Tonal Layering:** Use surface colors (#FAFAFA) to differentiate "background" areas from "interactive" areas (#FFFFFF).

## Shapes

The design system employs a **Rounded** shape language to soften the industrial nature of the interface. 

- **Standard Elements:** Buttons, inputs, and small cards use a 12px (0.75rem) radius (`rounded-xl`).
- **Inner Elements:** Elements nested inside a 12px container should use an 8px radius to maintain concentric visual harmony.
- **Status Indicators:** "Live" pips or small notification badges are fully circular (pill-shaped).

## Components

- **Buttons:**
  - **Primary:** Solid #4F46E5 background, white text. No gradient.
  - **Secondary:** White background, #E4E4E7 border, #18181B text.
- **Input Fields:** 1px #E4E4E7 border, 12px radius. On focus, the border shifts to #4F46E5 with a subtle 2px indigo ring at 10% opacity.
- **Cards:** White background, 1px border, 12px radius. No shadow unless interactive/hovered.
- **Live Transcript List:** Use `body-md` for text. Highlight AI-suggested phrases with a subtle #4F46E5/10% background tint and a left-side 2px indigo accent bar.
- **Chips/Status:** Use #10B981 for "Live" status with a breathing pulse animation on the icon. Use #F4F4F5 for neutral tags.
- **AI Sidebar:** A persistent utility panel using #FAFAFA background to distinguish it from the main white canvas. Contains "Action Items" as Checkbox lists.