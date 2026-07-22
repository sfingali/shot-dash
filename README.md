# Shot Dash

Local storyboard review dashboard for film production. Serves a CSV shot list as a filterable grid, inline frame previews, and a reference image browser.

**Zero dependencies** — Python stdlib only.

## Quick start

```bash
python shot_dash.py
# → http://localhost:8090
```

Point it at your own paths:

```bash
python shot_dash.py \
  --csv /path/to/shots.csv \
  --frames-dir /path/to/frames \
  --refs-dir /path/to/references \
  --port 8090
```

## Features

- **Shot grid** — filterable/sortable table from your CSV
- **Inline frame preview** — click any shot to see the generated frame
- **Status management** — approve/reject/update shot statuses (writes back to CSV)
- **Reference browser** — thumbnail strip of all reference images (costume, location, props)
- **Keyboard navigation** — arrow keys to browse, `a` to approve, `Esc` to close
- **Full-screen modal** — click any image to enlarge

## CSV format

Expected columns (any order, headers are preserved):

| Column | Description |
|--------|-------------|
| `scene_number` | Scene identifier |
| `shot_number` | Sequential shot number |
| `verbatim_instructions` | Raw user input / transcribed audio |
| `lens` | Lens spec (e.g. "35mm", "28mm anamorphic") |
| `aspect_ratio` | e.g. "2.388:1" |
| `quality` | Generation quality |
| `curated_description` | Structured shot description |
| `prompt` | Full generation prompt |
| `output_file` | Generated image filename |
| `status` | `pending` / `curated` / `generated` / `approved` / `edited` |

## API endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard UI |
| `/api/shots` | GET | CSV rows as JSON |
| `/api/frames` | GET | Frame files with lookup maps |
| `/api/refs` | GET | Reference image list |
| `/api/frame/<name>` | GET | Serve frame image |
| `/api/ref/<path>` | GET | Serve reference image |
| `/api/update` | POST | Update shot field (`{"row_index":0,"field":"status","value":"approved"}`) |

## Security

This is a local-only dashboard. It binds to `0.0.0.0` for convenience (accessible from other devices on your network) but has no authentication. Run it on a trusted network only, or use `--port` with a firewall.

No credentials, API keys, or secrets are stored in this repo. Point it at your local filesystem paths.
