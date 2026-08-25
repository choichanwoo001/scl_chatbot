# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

## SCL prototype decisions

- The company presentation must open directly on an SCL homepage recreation, not on a standalone project landing page.
- The SCL AI chatbot is visible above the homepage content and starts expanded in presentation mode.
- The prototype is desktop-only. The chatbot is a fixed-width 432px right-side overlay with a desktop viewport-height guard; do not add mobile menus, bottom sheets, or small-screen responsive layouts.
- The prototype must keep a small label explaining that it is a demonstration and must not be presented as the live SCL service.
- Chatbot answers should prioritize scanability: lead with a direct summary, then use short section headings, compact lists or label/value rows, and visually distinct caution notes. Preserve concise one-sentence replies without unnecessary decoration.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
