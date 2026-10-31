# HENRY_3D_SCENE_AUDIT.md

Source audit of the reference 3D workstation environment used as the visual
foundation for the DocsDrive frontend.

- **Reference:** https://github.com/henryjeff/portfolio-website (outer 3D world)
- **Vendored copy:** `vendor/henry-portfolio-outer/` (shallow clone, audited 2026-11-01)
- **License:** MIT, © 2024 Henry Heffernan (`LICENSE.md` in the vendored copy).
  Reuse/modification permitted with the copyright notice preserved. Attribution
  is recorded in `THIRD_PARTY_NOTICES.md`.

## 1. Stack of the original

| Layer | Detail |
|---|---|
| Bundler | Webpack 5 (`bundler/webpack.{common,dev,prod}.js`), entry `src/script.ts` |
| Framework | React 17 (UI overlay only), Three.js **0.137.5**, GSAP 3, Framer Motion 6 |
| Shaders | GLSL via glslify (`screen`, `coffee`) |
| Assets | GLB models (Draco-compressed), baked 4096×4096 JPG atlases, 22 MB audio, 57 MB textures |

## 2. How the app boots

1. `src/script.ts` → `new Application()` (`src/Application/Application.ts`).
2. `Application` constructs singletons: `Sizes`, `Mouse`, `Loading`, `Time`,
   `Scene`, `Resources(sources)`, `Camera`, `Renderer`, `World`, `UI`.
3. `UI` mounts React 17 into `#ui` — `LoadingScreen.tsx` renders the **BIOS
   boot screen** (this is where the "HHBIOS" branding lives), waits for the
   `Resources` loader (`eventBus` `loadedSource` events), then removes the
   overlay on user click ("click to start").
4. `World` waits for `resources.on('ready')`, then builds: `Environment`,
   `Decor`, `Computer` (BakedModel from `computer_setup.glb` +
   `baked_computer.jpg`), `MonitorScreen`, `CoffeeSteam`, `AudioManager`.

## 3. The scene composition

- `models/World/environment.glb` + `baked_environment.jpg` — room, walls, desk.
- `models/Decor/decor.glb` + `baked_decor_modified.jpg` — props.
- `models/Computer/computer_setup.glb` + `baked_computer.jpg` (4096×4096
  baked atlas) — CRT computer, keyboard, mouse as **one baked mesh**; the
  branding is painted into the texture atlas, not separate decals.
- `textures/monitor/layers/compressed/smudges.jpg`, `shadow-compressed.png` —
  CRT glass layers.
- `textures/monitor/video/base-static.mp4`, `static-texture-layer.mp4` —
  CRT static noise video textures (`#video-1`, `#video-2` in `index.html`).
- GLSL shaders: `Shaders/screen` (CRT display), `Shaders/coffee` (steam).

## 4. Camera system

`Camera.ts` + `CameraKeyframes.ts`: custom keyframe camera with
`enterMonitor` / `leftMonitor` triggers, GSAP tweens, and an
orbit-controls mode entered by pressing a key. **Do not replace with
OrbitControls** — the monitor interaction choreography depends on it.

## 5. Monitor screen implementation (the integration keystone)

`World/MonitorScreen.ts`:

- Screen size constant: `1280 × 1024` px, positioned at `(0, 950, 255)`,
  rotated −3° on X.
- A plain `div` container + **same-origin `<iframe>`** are mounted via
  `CSS3DObject` on a separate `cssScene`, rendered by `CSS3DRenderer`
  (see `Renderer.ts`). A `NoBlending` invisible GL plane in the main scene
  occludes the DOM plane correctly during camera moves.
- Depth layers above the iframe: smudge (additive, 0.12), inner shadow,
  two CRT static video textures — plus enclosing box planes and a
  perspective dimming plane whose opacity depends on camera distance/angle.
- **Interaction bridge (iframe → 3D):** the outer app listens for
  `window.message` events whose `data.type` is `mousemove`, `keydown`,
  `keyup` and re-dispatches them as `CustomEvent`s on the iframe element
  with `inComputer = true`. For `mousemove` it rescales
  `clientX/clientY` by the iframe's bounding rect against the logical
  `1248 × 992` (1280−32 padding) inner size. `enterMonitor`/`leftMonitor`
  camera transitions fire when the pointer moves in/out of the screen.
- In the original, the iframe points at `https://os.henryheffernan.com/`
  (the separate inner-site repo). **For DocsDrive this URL is replaced with
  `/terminal?embed=1`** — our own terminal route — and the inner app must
  post the same three event types to `window.parent`.

## 6. Runtime asset URL sources (all must work under a base path)

1. `sources.ts` — all resource paths are **relative** (`models/...`,
   `textures/...`, `audio/...`), resolved against the *page URL*.
2. `index.html` — two `<video>` srcs use `../static/textures/...` and the
   favicon uses `../static/images/favicon.ico` (original served from `/`,
   so `../static` → `/static`). **These must be rewritten to relative
   paths for DocsDrive.**
3. Webpack emitted assets (bundle CSS/JS, images) — controlled by
   `output.publicPath`.
4. Google Analytics `gtag` script — **removed** for DocsDrive.

## 7. Henry-specific branding found (to rebrand)

- `LoadingScreen.tsx` — "Heffernan, Henry Inc.", "HHBIOS (C)2000",
  "Henry Heffernan Portfolio Showcase" strings, start popup, footer.
- `InfoOverlay.tsx` — `NAME_TEXT = 'Henry Heffernan'`.
- `MonitorScreen.ts` — iframe src + `title="HeffernanOS"`.
- `index.html` — personal meta/OG tags, GA tag, favicon.
- `baked_computer.jpg` — any UV-painted personal branding (inspect before
  touching; keep a backup; rebrand only if clearly personal).

## 8. Incompatibilities & decisions

| Issue | Decision |
|---|---|
| React 17 + Next.js 16 (React 19) in one tree | Keep the 3D app **fully isolated**: build with its own webpack config, serve statically from `public/docsdrive-world/`, embed via same-origin iframe on `/workstation` |
| Relative asset paths + webpack output | Build outputs to `public/docsdrive-world/`; assets stay relative; serve `/docsdrive-world/index.html` as the embed target |
| `../static/...` video/favicon URLs | Rewritten to relative `textures/...` / `images/...` in our copy |
| Personal analytics/branding | Removed; MIT license + copyright preserved in `THIRD_PARTY_NOTICES.md` |
| Inner app (HeffernanOS) | Not copied. We build the DocsDrive terminal at `/terminal?embed=1` |
