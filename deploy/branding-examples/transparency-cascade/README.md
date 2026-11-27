# Transparency Cascade — example branding folder

Example branding configuration for deploying a Pyrite instance under
the Transparency Cascade Press identity. The hostname
`investigate.transparencycascade.org` is an example, not a live deployment.

## Usage

Point a Pyrite deployment at this folder:

```bash
export PYRITE_BRANDING_DIR=/path/to/deploy/branding-examples/transparency-cascade
```

See `docs/deployment/white-labeling.md` for the full branding-folder
contract.

## Contents

| File | Source | Purpose |
|---|---|---|
| `branding.yaml` | hand-written | Brand config |
| `logo.png` | `https://transparencycascade.org/img/logo-black.png` | Square icon, 512×512, black-on-transparent |
| `wordmark.png` | `https://transparencycascade.org/img/wordmark-black.png` | Horizontal wordmark, 1344×256, black-on-transparent |

Assets are black-on-transparent. `branding.yaml` sets
`invert_on_dark: true`, so the frontend applies CSS `filter: invert(1)`
for dark-theme rendering. One asset per surface, no light/dark
duplication.

## Provenance

Assets downloaded from `https://transparencycascade.org/` on
2026-04-23. The site is Hugo-generated and serves these files from
`/img/logo-black.png` and `/img/wordmark-black.png`. Licensing:
first-party assets belonging to Transparency Cascade Press, LLC;
reused here under operator authority (the same owner runs both sites).
