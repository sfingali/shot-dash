#!/usr/bin/env python3
"""Shot Dash — local storyboard review dashboard for film production.
Serves a CSV shot list as a filterable grid, inline frame previews,
and a reference image browser. Zero dependencies beyond Python stdlib.

Usage:
    python shot_dash.py [--port 8090] [--frames-dir /path] [--refs-dir /path] [--csv /path]
"""

import csv
import json
import os
import sys
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 8090
CSV_PATH = "/opt/data/home/projects/the-waif/storyboard_shots.csv"
FRAMES_DIR = "/opt/data/home/projects/the-waif/storyboards_gpt"
REFS_DIR = "/opt/data/home/projects/the-waif/storyboard_reference"
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Fountain scene → location + character lookup (from THE WAIF numbered draft)
SCENE_LOOKUP = {
    "11": {"location": "Motel Room", "characters": "Ben (JRM)"},
    "12": {"location": "Motel Bathroom", "characters": "Ben (JRM)"},
    "14": {"location": "Motel Exterior — Catwalk", "characters": "Ben (JRM), Neighbor"},
    "15": {"location": "Pickup Truck — Highway", "characters": "Ben (JRM)"},
    "16": {"location": "Municipal Courthouse — Exterior", "characters": "Ben (JRM)"},
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(SCRIPT_DIR, "index.html")

# ── CLI ───────────────────────────────────────────────────────────────────
def parse_args():
    global PORT, CSV_PATH, FRAMES_DIR, REFS_DIR
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            i += 1; PORT = int(args[i])
        elif args[i] == "--csv" and i + 1 < len(args):
            i += 1; CSV_PATH = args[i]
        elif args[i] == "--frames-dir" and i + 1 < len(args):
            i += 1; FRAMES_DIR = args[i]
        elif args[i] == "--refs-dir" and i + 1 < len(args):
            i += 1; REFS_DIR = args[i]
        i += 1

# ── CSV ───────────────────────────────────────────────────────────────────
CSV_COLUMNS = [
    "scene_number", "shot_number", "verbatim_instructions",
    "lens", "aspect_ratio", "quality", "curated_description",
    "prompt", "output_file", "status"
]

def read_csv():
    if not os.path.exists(CSV_PATH):
        return [], CSV_COLUMNS
    with open(CSV_PATH, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or CSV_COLUMNS
    return rows, fieldnames

def write_csv(rows, fieldnames):
    # Auto-backup before every write
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(CSV_PATH)), ".csv_backups")
    os.makedirs(backup_dir, exist_ok=True)
    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"storyboard_shots.{ts}.csv")
    with open(backup_path, "w", newline="") as bf:
        bw = csv.DictWriter(bf, fieldnames=fieldnames)
        bw.writeheader()
        bw.writerows(rows)
    # Prune backups older than 7 days, keep max 50
    backups = sorted(os.listdir(backup_dir))
    if len(backups) > 50:
        for old in backups[:-50]:
            os.remove(os.path.join(backup_dir, old))
    # Write main file
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, CSV_PATH)

def update_shot_field(row_index, field, value):
    rows, fieldnames = read_csv()
    if row_index < 0 or row_index >= len(rows):
        return False
    rows[row_index][field] = value
    write_csv(rows, fieldnames)
    return True

# ── Images ────────────────────────────────────────────────────────────────
def list_images(directory):
    images = []
    base = Path(directory)
    if not base.exists():
        return images
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS:
            images.append(str(p.relative_to(base)))
    return images

def find_in_tree(base_dir, filename):
    """Search recursively for a file by basename in base_dir."""
    name_lower = filename.lower()
    for p in Path(base_dir).rglob("*"):
        if p.is_file() and p.name.lower() == name_lower:
            return str(p)
    # Fallback: substring match
    for p in Path(base_dir).rglob("*"):
        if p.is_file() and name_lower in p.name.lower():
            return str(p)
    return None

# ── Content types ─────────────────────────────────────────────────────────
CT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif", "html": "text/html; charset=utf-8",
    "json": "application/json"
}

# ── Handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def _respond(self, status, content_type, body_bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body_bytes))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body_bytes)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-transfer

    def _json(self, data, status=200):
        self._respond(status, "application/json", json.dumps(data).encode())

    def _file(self, path, content_type, status=200):
        if not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            self._respond(status, content_type, f.read())

    def _error(self, msg, status=400):
        self._json({"error": msg}, status)

    def _safe_path(self, base_dir, rel_path):
        """Resolve rel_path to a file under base_dir, preventing traversal."""
        # Strip any leading slashes and normalize
        clean = os.path.normpath(rel_path).lstrip("/")
        full = os.path.join(base_dir, clean)
        # Must be within base_dir
        if not full.startswith(os.path.abspath(base_dir)):
            return None
        return full if os.path.isfile(full) else None

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path

        if path == "/":
            self._file(HTML_PATH, CT["html"])

        elif path == "/api/shots":
            rows, _ = read_csv()
            self._json({"shots": rows})

        elif path == "/api/frames":
            images = list_images(FRAMES_DIR)
            frames = {}
            frames_lower = {}
            for img in images:
                base = os.path.basename(img)
                frames[base] = img
                frames_lower[base.lower()] = img
            self._json({"frames": frames, "frames_lower": frames_lower, "all": images})

        elif path == "/api/refs":
            self._json({"images": list_images(REFS_DIR)})

        elif path.startswith("/api/frame/"):
            filename = urllib.parse.unquote(path[len("/api/frame/"):])
            filepath = find_in_tree(FRAMES_DIR, filename)
            if filepath:
                ext = os.path.splitext(filepath)[1].lower().lstrip(".")
                self._file(filepath, CT.get(ext, "application/octet-stream"))
            else:
                self.send_error(404, "Frame not found")

        elif path.startswith("/api/ref/"):
            rel = urllib.parse.unquote(path[len("/api/ref/"):])
            filepath = self._safe_path(REFS_DIR, rel)
            if not filepath:
                filepath = find_in_tree(REFS_DIR, rel)
            if filepath:
                ext = os.path.splitext(filepath)[1].lower().lstrip(".")
                self._file(filepath, CT.get(ext, "application/octet-stream"))
            else:
                self.send_error(404, "Reference not found")

        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/reorder":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            scene = data.get("scene_number", "")
            order = data.get("order", [])  # list of output_file names in new order
            rows, fieldnames = read_csv()
            # Update shot_number for all shots in this scene
            for i, fn in enumerate(order):
                for r in rows:
                    if r.get('output_file', '').strip() == fn:
                        r['shot_number'] = str(i + 1)
                        break
            write_csv(rows, fieldnames)
            self._json({"ok": True})

        elif self.path == "/api/generate":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            row_index = data.get("row_index")
            if row_index is None:
                return self._error("Missing row_index")
            rows, fieldnames = read_csv()
            if row_index < 0 or row_index >= len(rows):
                return self._error("Row index out of range", 400)
            shot = rows[row_index]
            # Build prompt: curated_description > verbatim_instructions
            base = (shot.get("curated_description") or shot.get("verbatim_instructions") or "").strip()
            if not base:
                return self._error("Shot has no description to generate from", 400)
            prompt = base + " Photorealistic cinematic still from an indie horror film. No text, no watermark."
            # Load API key
            key = None
            env_path = os.path.expanduser("/opt/data/profiles/heavy/.env")
            if os.path.exists(env_path):
                with open(env_path) as ef:
                    for line in ef:
                        if "OPENAI_KEY" in line or "VOICE_TOOLS_OPENAI_KEY" in line:
                            key = line.strip().split("=", 1)[1].strip().strip("'").strip('"')
                            break
            if not key:
                return self._error("OpenAI API key not found", 500)
            # Call GPT Image 2
            import urllib.request, base64
            body = json.dumps({
                "model": "gpt-image-2",
                "prompt": prompt,
                "n": 1,
                "size": "2560x1072",
                "quality": "medium"
            }).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/images/generations",
                data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            )
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()[:500]
                return self._error(f"GPT Image 2 blocked: {err_body}", 400)
            except Exception as e:
                return self._error(f"API error: {str(e)}", 500)
            d = resp.get("data", [{}])
            if not d:
                return self._error("No image data in response", 500)
            if "b64_json" in d[0]:
                img_bytes = base64.b64decode(d[0]["b64_json"])
            else:
                img_bytes = urllib.request.urlopen(d[0]["url"]).read()
            # Save image
            output_file = shot.get("output_file", "").strip()
            if not output_file:
                sc = shot.get("scene_number", "XX")
                sn = shot.get("shot_number", "1")
                output_file = f"waif_sc_{sc}_v{sn}.png"
            out_path = os.path.join(FRAMES_DIR, output_file)
            with open(out_path, "wb") as of:
                of.write(img_bytes)
            # Update CSV
            shot["status"] = "generated"
            shot["output_file"] = output_file
            shot["prompt"] = prompt
            shot["aspect_ratio"] = shot.get("aspect_ratio") or "2.39:1"
            shot["quality"] = shot.get("quality") or "medium"
            shot["generation_method"] = shot.get("generation_method") or "generation"
            shot["estimated_cost"] = shot.get("estimated_cost") or "$0.04"
            if not shot.get("lens"):
                shot["lens"] = "28mm"
            write_csv(rows, fieldnames)
            self._json({"ok": True, "output_file": output_file, "size_kb": len(img_bytes) // 1024})

        elif self.path == "/api/create":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            rows, fieldnames = read_csv()
            new_row = {f: data.get(f, "") for f in fieldnames}
            if not new_row.get("status"):
                new_row["status"] = "pending"
            sc = new_row.get("scene_number", "")
            existing = [int(r.get("shot_number") or 0) for r in rows if r.get("scene_number") == sc]
            new_row["shot_number"] = str(max(existing) + 1 if existing else 1)
            # Auto-fill location and characters from fountain data
            if sc in SCENE_LOOKUP:
                if not new_row.get("location"):
                    new_row["location"] = SCENE_LOOKUP[sc]["location"]
                if not new_row.get("characters"):
                    new_row["characters"] = SCENE_LOOKUP[sc]["characters"]
            rows.append(new_row)
            write_csv(rows, fieldnames)
            self._json({"ok": True, "row": new_row})

        elif self.path == "/api/update":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            idx = data.get("row_index")
            field = data.get("field", "status")
            value = data.get("value", "")
            if idx is None:
                return self._error("Missing row_index")
            if update_shot_field(idx, field, value):
                self._json({"ok": True})
            else:
                self._error("Row index out of range", 400)
        else:
            self.send_error(404)

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    parse_args()
    print(f"🎬 Shot Dash")
    print(f"   CSV:      {CSV_PATH}")
    print(f"   Frames:   {FRAMES_DIR}")
    print(f"   Refs:     {REFS_DIR}")
    print(f"   → http://localhost:{PORT}")
    print(f"   Ctrl+C to stop\n")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()

if __name__ == "__main__":
    main()
