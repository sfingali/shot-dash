#!/usr/bin/env python3
"""Shot Dash — local storyboard review dashboard for THE WAIF.

Serves a CSV shot list as a filterable grid, inline frame previews, and a
reference image browser. Zero dependencies beyond the Python stdlib.

Usage:
    python3 shot_dash.py [--port 8090] [--frames-dir /path] [--refs-dir /path]
                         [--csv /path] [--shotlist /path] [--canon-dir /path]
                         [--project name] [--env /path/to/.env] [--public]

    Binds to 127.0.0.1 by default; --public binds 0.0.0.0. Path overrides
    apply only to the project loaded at startup.

Reliability notes (v2):
  * ThreadingHTTPServer — long image-generation calls (60-180s) no longer
    block every other request. This was the main reason the old server
    appeared to "die": one generate call froze the whole UI and any
    keepalive probe for up to 3 minutes.
  * Every POST handler runs inside a catch-all; unhandled exceptions return
    a JSON 500 instead of killing the worker.
  * CSV read-modify-write cycles are serialized with a lock so concurrent
    requests can't corrupt the file.
  * /api/health endpoint for the keepalive cron. For real persistence, run
    under systemd:

        [Unit]
        Description=Shot Dash
        After=network.target
        [Service]
        ExecStart=/usr/bin/python3 /path/to/shot_dash.py
        Restart=always
        RestartSec=3
        [Install]
        WantedBy=default.target

Custodian notes (read before editing):
  * INDENTATION IS LOAD-BEARING. Every api_* method lives inside the Handler
    class (starts ~line 1800). Dedenting a method — easy to do when pasting —
    silently turns it into a module-level function, and the POST_ROUTES table
    below the class then crashes at import with AttributeError on Handler.<n>.
    After any edit in the Handler region run: python3 -m py_compile shot_dash.py
    and start the server once — the routes table is built at import time, so a
    dedented method fails fast, not at request time.
  * Locking discipline: read-modify-write on shots CSV under CSV_LOCK, shotlist
    under SHOTLIST_LOCK, hero_assets.json under HEROES_LOCK. NEVER hold two of
    these at once (deadlock risk); see api_shotlist_sync for the pattern of
    sequential lock scopes. Slow OpenAI calls (1-3 min) go OUTSIDE the lock;
    re-resolve the row afterwards (see api_generate).
  * output_file is the identity key of a shot row. It must stay unique across
    the CSV — api_create and api_duplicate uniquify on insert; keep that
    invariant if you add new row-creating endpoints.
  * Nothing in this app ever deletes an image file. Archive/bin are moves
    inside the refs tree; shot "archive" is a status flip. Preserve this.
"""

import base64
import csv
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Pillow is optional — used only by the /api/thumb/ endpoints. Without it
# they fall back to serving the full-resolution originals.
try:
    from PIL import Image
except ImportError:
    Image = None

# -- Config ----------------------------------------------------------------
PORT = 8090

# Network defaults: bind loopback only; --public opts into 0.0.0.0.
BIND_HOST = "127.0.0.1"
MAX_BODY_BYTES = 1048576   # POST bodies over 1 MB are rejected (413)
GEN_CONCURRENCY = 5        # max in-flight generate/edit requests per client IP

# Legacy defaults — used once to seed the default "the-waif" project.
DEFAULT_CSV_PATH = "/opt/data/home/projects/the-waif/storyboard_shots.csv"
DEFAULT_FRAMES_DIR = "/opt/data/home/projects/the-waif/storyboards_gpt"
DEFAULT_REFS_DIR = "/opt/data/home/projects/the-waif/storyboard_reference"
DEFAULT_CANON_DIR = "/opt/data/home/projects/the-waif"
ENV_PATH = "/opt/data/profiles/heavy/.env"

# Active-project paths. Everything below reads these globals exactly as it
# did before the project system existed; load_project() repoints them when
# the user switches projects.
CSV_PATH = DEFAULT_CSV_PATH
FRAMES_DIR = DEFAULT_FRAMES_DIR
REFS_DIR = DEFAULT_REFS_DIR
SHOTLIST_PATH = ""
CANON_DIR = DEFAULT_CANON_DIR

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Server-side thumbnails: cached under <frames|refs dir>/_thumbs/, mirroring
# the source tree, regenerated whenever the source file is newer.
THUMB_DIRNAME = "_thumbs"
FRAME_THUMB_WIDTH = 200    # grid cards + version strip
REF_THUMB_WIDTH = 150      # reference cards
FRAME_PREVIEW_WIDTH = 600  # viewer panel preview (/api/preview/frame/)
# Previews cache under _thumbs/_preview/ so the existing THUMB_DIRNAME
# exclusion in list_images/find_in_tree covers them too.
PREVIEW_DIRNAME = os.path.join(THUMB_DIRNAME, "_preview")

# Reference archive layout inside REFS_DIR. Archived refs are moved (never
# deleted) into _archive/; the bin is a subfolder of the archive. Nothing in
# this app ever unlinks an image file.
REF_ARCHIVE = "_archive"
REF_BIN = "_archive/_bin"

QUALITY_LEVELS = ("low", "medium", "high")
QUALITY_COST = {"low": "$0.02", "medium": "$0.07", "high": "$0.19"}

# Image model registry. Only GPT Image 2 for now; to add a model, add an
# entry here (and per-model request shaping in openai_generate_image /
# openai_edit_image if its API differs) and it appears in the UI selector.
IMAGE_MODELS = {
    "gpt-image-2": {
        "label": "GPT Image 2",
        "generate_url": "https://api.openai.com/v1/images/generations",
        "edit_url": "https://api.openai.com/v1/images/edits",
        "size": "2560x1072",
    },
}
DEFAULT_MODEL = "gpt-image-2"

# Periodic CSV/JSON snapshot cadence (seconds) — see backup_all_projects().
BACKUP_INTERVAL = 600
BACKUP_KEEP = 200  # snapshots kept per project

HOUSE_STYLE = ("Desaturated palette, cool shadows. Photorealistic cinematic "
               "still from an indie horror film. Scope 2.39:1. No text, no "
               "watermark, no logos.")

EDIT_SUFFIX = (". Keep everything else the same: composition, lighting, "
               "palette, mood. Photorealistic cinematic still from an indie "
               "horror film. No text, no watermark, no logos.")

# Vision model used by /api/describe_ref (reference image -> text).
VISION_MODEL = "gpt-4o"
VISION_URL = "https://api.openai.com/v1/chat/completions"
DESCRIBE_PROMPT = (
    "Describe this image in precise visual detail so the description could "
    "be dropped into an image-generation prompt and reproduce the subject "
    "faithfully. Cover: the main subject and its exact appearance (shape, "
    "materials, textures, wear, distinguishing details), colors (specific "
    "shades), lighting, and setting/background. Write 1-2 dense paragraphs "
    "of plain prose. No preamble, no lists, no meta-commentary about the "
    "image being a photo or render.")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(SCRIPT_DIR, "index.html")
PROJECTS_DIR = os.path.join(SCRIPT_DIR, "projects")
ACTIVE_PATH = os.path.join(PROJECTS_DIR, "active.json")
BACKUPS_DIR = os.path.join(SCRIPT_DIR, "backups")

CSV_LOCK = threading.Lock()
SHOTLIST_LOCK = threading.Lock()
HEROES_LOCK = threading.Lock()
JOBS_LOCK = threading.Lock()
PROJECT_LOCK = threading.Lock()

# CLI flags override the paths of the project loaded at startup only.
# Re-applying them after every project switch paired e.g. a --csv override
# with another project's frames dir permanently; now they stay bound to the
# startup project (CLI_BOUND_PROJECT) and switching away drops them.
CLI_OVERRIDES = {}
CLI_PROJECT = None
CLI_BOUND_PROJECT = None  # set once in main(); overrides apply to it only


def parse_args():
    global PORT, ENV_PATH, CLI_PROJECT, BIND_HOST
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        flag = args[i]
        has_val = i + 1 < len(args)
        if flag == "--port" and has_val:
            i += 1; PORT = int(args[i])
        elif flag == "--csv" and has_val:
            i += 1; CLI_OVERRIDES["csv_path"] = args[i]
        elif flag == "--frames-dir" and has_val:
            i += 1; CLI_OVERRIDES["frames_dir"] = args[i]
        elif flag == "--refs-dir" and has_val:
            i += 1; CLI_OVERRIDES["refs_dir"] = args[i]
        elif flag == "--shotlist" and has_val:
            i += 1; CLI_OVERRIDES["shotlist_path"] = args[i]
        elif flag == "--canon-dir" and has_val:
            i += 1; CLI_OVERRIDES["canon_dir"] = args[i]
        elif flag == "--project" and has_val:
            i += 1; CLI_PROJECT = args[i]
        elif flag == "--env" and has_val:
            i += 1; ENV_PATH = args[i]
        elif flag == "--public":
            BIND_HOST = "0.0.0.0"
        i += 1


# -- Errors ----------------------------------------------------------------
class ApiError(Exception):
    """Raise anywhere in a handler to return a JSON error with a status."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# -- Project system --------------------------------------------------------
# A project is a directory under projects/<name>/ holding project.json
# (settings), its own shots CSV, shotlist CSV, hero_assets.json and
# categories.json. The default project wraps the legacy THE WAIF paths so
# existing data keeps working untouched.
PROJECT = {}  # settings of the active project


def _slug(name):
    return re.sub(r"[^A-Za-z0-9_\-]", "_", (name or "").strip()).strip("_")


def project_dir(name):
    return os.path.join(PROJECTS_DIR, name)


def settings_path(name):
    return os.path.join(project_dir(name), "project.json")


def default_settings(name):
    pd = project_dir(name)
    return {
        "name": name,
        "csv_path": os.path.join(pd, "shots.csv"),
        "shotlist_path": os.path.join(pd, "shotlist.csv"),
        "frames_dir": os.path.join(pd, "frames"),
        "refs_dir": os.path.join(pd, "refs"),
        "canon_dir": "",
        "file_prefix": (_slug(name).replace("-", "_") or "proj"),
        "quality": "medium",
        "model": DEFAULT_MODEL,
    }


def list_projects():
    names = []
    if os.path.isdir(PROJECTS_DIR):
        for n in sorted(os.listdir(PROJECTS_DIR)):
            if os.path.isfile(settings_path(n)):
                names.append(n)
    return names


def save_settings(settings):
    name = settings["name"]
    os.makedirs(project_dir(name), exist_ok=True)
    tmp = settings_path(name) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, settings_path(name))


def ensure_default_project():
    """First run: wrap the legacy THE WAIF paths in a project directory."""
    if list_projects():
        return
    s = default_settings("the-waif")
    s.update({
        "csv_path": DEFAULT_CSV_PATH,
        "frames_dir": DEFAULT_FRAMES_DIR,
        "refs_dir": DEFAULT_REFS_DIR,
        "canon_dir": DEFAULT_CANON_DIR,
        "file_prefix": "waif",
    })
    save_settings(s)


def load_active_name():
    try:
        with open(ACTIVE_PATH) as f:
            name = json.load(f).get("active", "")
    except (OSError, json.JSONDecodeError):
        name = ""
    projects = list_projects()
    if name in projects:
        return name
    return projects[0] if projects else "the-waif"


def save_active_name(name):
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    tmp = ACTIVE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"active": name}, f)
    os.replace(tmp, ACTIVE_PATH)


def load_project(name):
    """Point the module-level path globals at a project and make sure its
    directories, shotlist and pre-populated reference categories exist.
    SIDE EFFECTS: mutates the CSV_PATH/FRAMES_DIR/... globals (under
    PROJECT_LOCK), creates project directories, rewrites active.json, and
    refreshes THIS thread's request snapshot. In-flight requests on other
    threads keep their old snapshot by design — see snapshot_ctx."""
    global PROJECT, CSV_PATH, FRAMES_DIR, REFS_DIR, SHOTLIST_PATH, CANON_DIR
    sp = settings_path(name)
    if not os.path.isfile(sp):
        raise ApiError("Unknown project: " + name, 404)
    try:
        with open(sp) as f:
            saved = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ApiError("Could not read project settings: %s" % e, 500)
    settings = default_settings(name)
    settings.update(saved)
    settings["name"] = name
    if CLI_OVERRIDES and name == CLI_BOUND_PROJECT:
        settings.update(CLI_OVERRIDES)
    with PROJECT_LOCK:
        PROJECT = settings
        CSV_PATH = settings["csv_path"]
        FRAMES_DIR = settings["frames_dir"]
        REFS_DIR = settings["refs_dir"]
        SHOTLIST_PATH = settings["shotlist_path"]
        CANON_DIR = settings.get("canon_dir") or ""
    # This thread must see the new project immediately (ensure_ref_categories
    # below reads paths through the snapshot).
    snapshot_ctx()
    for d in (settings["frames_dir"], settings["refs_dir"],
              os.path.dirname(settings["shotlist_path"])):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    try:
        ensure_ref_categories()
    except Exception as e:
        print("Warning: category pre-population failed: %s" % e)
    save_active_name(name)
    return settings


# -- Per-request context -----------------------------------------------------
# ThreadingHTTPServer serves each request on its own thread while
# load_project() repoints the module path globals above. Every request
# captures a snapshot of those globals once (snapshot_ctx, called at the top
# of do_GET/do_POST) and reads only the snapshot afterwards, so a project
# switch mid-request (e.g. during a 3-minute generate) can't make it write
# an image or CSV row into another project's files. The OpenAI key and the
# quality/model settings are captured in the same snapshot.
_TLS = threading.local()


def snapshot_ctx():
    with PROJECT_LOCK:
        c = {
            "csv_path": CSV_PATH,
            "frames_dir": FRAMES_DIR,
            "refs_dir": REFS_DIR,
            "shotlist_path": SHOTLIST_PATH,
            "canon_dir": CANON_DIR,
            "project": dict(PROJECT),
        }
    c["openai_key"] = get_openai_key()
    _TLS.ctx = c
    return c


def ctx():
    c = getattr(_TLS, "ctx", None)
    return c if c is not None else snapshot_ctx()


def set_ctx(c):
    """Adopt an existing snapshot — background workers inherit the snapshot
    of the request that spawned them instead of re-reading globals."""
    _TLS.ctx = c


def csv_path():
    return ctx()["csv_path"]


def frames_dir():
    return ctx()["frames_dir"]


def refs_dir():
    return ctx()["refs_dir"]


def shotlist_path():
    return ctx()["shotlist_path"]


def canon_dir():
    return ctx()["canon_dir"]


def active_project():
    return ctx()["project"]


def active_quality():
    q = (active_project().get("quality") or "medium").lower()
    return q if q in QUALITY_LEVELS else "medium"


def requested_quality(data):
    """Per-request quality override: use the request's 'quality' when given
    (must be a valid level), else the project default."""
    q = str(data.get("quality") or "").strip().lower()
    if not q:
        return active_quality()
    if q not in QUALITY_LEVELS:
        raise ApiError("Quality must be one of: " + ", ".join(QUALITY_LEVELS))
    return q


def active_model():
    m = active_project().get("model") or DEFAULT_MODEL
    return m if m in IMAGE_MODELS else DEFAULT_MODEL


def file_prefix():
    return active_project().get("file_prefix") or "shot"


# -- Hero assets ------------------------------------------------------------
# hero_assets.json: a list of {id, name, type, category, description,
# breakdown, colors, notes, ref_image, archived, updated}. Heroes are
# taggable on shots (hero_tags CSV column) and their descriptions are
# appended to generation prompts for consistency across renders.
def heroes_path():
    return os.path.join(project_dir(active_project().get("name", "the-waif")),
                        "hero_assets.json")


def load_heroes():
    try:
        with open(heroes_path()) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_heroes(heroes):
    # REQUIRES: HEROES_LOCK held by caller (every mutation path takes it).
    # SIDE EFFECTS: atomically rewrites hero_assets.json.
    os.makedirs(os.path.dirname(heroes_path()), exist_ok=True)
    tmp = heroes_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(heroes, f, indent=2)
    os.replace(tmp, heroes_path())


def hero_by_id(heroes, hero_id):
    for h in heroes:
        if h.get("id") == hero_id:
            return h
    return None


def hero_ref_category(hero):
    """Reference-tree subdirectory a hero's generated thumbnail belongs in.
    Prefer the hero's explicit category when it's a known root, else map
    from its type; everything else falls back to hero_props (the Props
    tab)."""
    cat = (hero.get("category") or "").strip()
    if cat in REF_ROOT_CATEGORIES:
        return cat
    t = (hero.get("type") or "").strip().lower()
    return {"character": "characters", "location": "locations",
            "vehicle": "vehicles"}.get(t, "hero_props")


def shot_hero_tags(shot):
    # CONTRACT: the hero_tags CSV column is a comma-separated list of hero
    # NAMES or IDS ("Ben — Motel, hero_3"); both are matched case-sensitively
    # by hero_fragments/taggedShotsFor. The frontend writes names.
    return [t.strip() for t in (shot.get("hero_tags") or "").split(",")
            if t.strip()]


def hero_fragments(shot):
    """Prompt fragments for every hero tagged on the shot — this is what
    keeps a hero asset looking the same across generations."""
    tags = shot_hero_tags(shot)
    if not tags:
        return []
    frags = []
    for h in load_heroes():
        if h.get("archived"):
            continue
        if h.get("id") in tags or h.get("name") in tags:
            bits = [(h.get("description") or "").strip()]
            if h.get("breakdown"):
                bits.append("Details: " + h["breakdown"])
            if h.get("colors"):
                bits.append("Color palette: " + h["colors"])
            body = ". ".join(b for b in bits if b)
            if body:
                frags.append("%s (%s): %s" % (h.get("name", "?"),
                                              h.get("type", "asset"), body))
    return frags


# -- Shotlist (24-column VFX breakdown) -------------------------------------
SHOTLIST_COLUMNS = [
    "Order", "Scene", "Setup", "I/E", "Location", "Time of Day",
    "Description", "Fountain Excerpt", "Shot Type/Framing", "Camera Movement",
    "Lens/Focal Length", "Camera Angle", "Characters in Shot",
    "Duration(seconds)", "Page(s)", "Length(8ths)", "Shoot Day",
    "Sequence", "VFX Work", "Complexity", "Asset(s)",
    "Notes/Assumptions", "Shot Count", "Cost Each", "Cost Gross",
]
SHOTLIST_FLOAT = {"Order", "Duration(seconds)", "Length(8ths)"}
SHOTLIST_INT = {"Shot Count", "Cost Each", "Cost Gross"}


def coerce_shotlist_value(col, value):
    """Normalize typed columns; blanks stay blank, garbage raises."""
    v = str(value if value is not None else "").strip()
    if not v:
        return ""
    if col in SHOTLIST_FLOAT:
        try:
            f = float(v)
        except ValueError:
            raise ApiError("%s must be a number, got %r" % (col, v))
        return ("%g" % f)
    if col in SHOTLIST_INT:
        try:
            return str(int(float(v)))
        except ValueError:
            raise ApiError("%s must be an integer, got %r" % (col, v))
    return v


def read_shotlist():
    path = shotlist_path()
    if not path or not os.path.exists(path):
        return []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    for r in rows:
        r.pop(None, None)
        for c in SHOTLIST_COLUMNS:
            if r.get(c) is None:
                r[c] = ""
    return rows


def write_shotlist(rows):
    # REQUIRES: SHOTLIST_LOCK held by caller.
    _write_csv_file(shotlist_path(), rows, SHOTLIST_COLUMNS)


def next_shotlist_order(rows):
    top = 0.0
    for r in rows:
        try:
            top = max(top, float(r.get("Order") or 0))
        except ValueError:
            pass
    return float(int(top) + 1)


def sort_shotlist(rows):
    def keyf(r):
        try:
            return float(r.get("Order") or 0)
        except ValueError:
            return 0.0
    rows.sort(key=keyf)


def parse_slugline(loc):
    """'INT. KITCHEN - HOUSE - DAY' -> ('INT', 'KITCHEN - HOUSE', 'Day')."""
    m = re.match(r"^\s*(INT\.?\s*/\s*EXT\.?|EXT\.?\s*/\s*INT\.?|I/E|INT|EXT)"
                 r"[\.\s]+(.*)$", loc or "", re.I)
    ie, rest = "", (loc or "").strip()
    if m:
        tok = m.group(1).upper().replace(" ", "").rstrip(".")
        ie = tok if tok in ("INT", "EXT") else "I/E"
        rest = m.group(2).strip()
    parts = [p.strip() for p in rest.split(" - ") if p.strip()]
    tod = ""
    if parts and parts[-1].upper() in ("DAY", "NIGHT", "SUNSET", "SUNRISE",
                                       "DUSK", "DAWN", "MORNING", "EVENING",
                                       "CONTINUOUS", "LATER"):
        tod = parts[-1].title()
        parts = parts[:-1]
    return ie, " - ".join(parts), tod


def shot_to_shotlist_row(shot, order):
    """Seed a shotlist row from a Shots-tab shot ('initially informed by
    what's created in the Shots area')."""
    ie, loc, tod = parse_slugline(shot.get("location") or "")
    desc = (shot.get("curated_description") or
            shot.get("verbatim_instructions") or "")
    scene_text = canon_scene_text(shot.get("scene_number"))
    row = {c: "" for c in SHOTLIST_COLUMNS}
    row.update({
        "Order": "%g" % order,
        "Scene": shot.get("scene_number") or "",
        "Setup": shot.get("output_file") or "",
        "I/E": ie,
        "Location": loc or (shot.get("location") or ""),
        "Time of Day": tod,
        "Description": desc.strip()[:500],
        "Fountain Excerpt": scene_text[:250],
        "Lens/Focal Length": shot.get("lens") or "",
        "Characters in Shot": shot.get("characters") or "",
        "Asset(s)": shot.get("hero_tags") or "",
        "Shot Count": "1",
        "Cost Each": "0",
        "Cost Gross": "0",
    })
    if scene_text:
        # Rough page estimate: ~2500 chars per screenplay page, floor 1/8.
        pages = max(0.125, round(len(scene_text) / 2500.0, 3))
        row["Page(s)"] = "%g" % pages
        row["Length(8ths)"] = "%g" % round(pages * 8, 3)
    return row


# -- Job registry (long-running background work) -----------------------------
JOBS = {}


def new_job(kind, total):
    job_id = "%s_%d" % (kind, int(time.time() * 1000))
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "kind": kind, "total": total,
                        "done": 0, "errors": [], "files": [],
                        "status": "running", "started": time.time()}
        # keep the registry from growing forever
        if len(JOBS) > 50:
            for k in sorted(JOBS, key=lambda k: JOBS[k]["started"])[:-50]:
                del JOBS[k]
    return job_id


def job_step(job_id, file=None, error=None):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["done"] += 1
        if file:
            job["files"].append(file)
        if error:
            job["errors"].append(error)


def job_finish(job_id, status="done"):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["status"] = status


# -- Periodic backups --------------------------------------------------------
_BACKUP_MTIMES = {}


def backup_all_projects():
    """Snapshot every project's CSVs + hero JSON into backups/<project>/
    whenever they changed since the last pass. Old snapshots are pruned by
    count, never the most recent ones."""
    for name in list_projects():
        try:
            with open(settings_path(name)) as f:
                s = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        targets = [s.get("csv_path"), s.get("shotlist_path"),
                   os.path.join(project_dir(name), "hero_assets.json")]
        dest_dir = os.path.join(BACKUPS_DIR, name)
        for p in targets:
            if not p or not os.path.isfile(p):
                continue
            try:
                mt = os.path.getmtime(p)
                if _BACKUP_MTIMES.get(p) == mt:
                    continue
                os.makedirs(dest_dir, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                dest = os.path.join(dest_dir,
                                    "%s.%s" % (ts, os.path.basename(p)))
                shutil.copy2(p, dest)
                _BACKUP_MTIMES[p] = mt
            except OSError:
                continue
        try:
            if os.path.isdir(dest_dir):
                snaps = sorted(os.listdir(dest_dir))
                for old in snaps[:-BACKUP_KEEP]:
                    os.remove(os.path.join(dest_dir, old))
        except OSError:
            pass


def _backup_loop():
    while True:
        time.sleep(BACKUP_INTERVAL)
        try:
            backup_all_projects()
        except Exception as e:
            print("Backup pass failed: %s" % e)


def start_backup_thread():
    t = threading.Thread(target=_backup_loop, daemon=True)
    t.start()


# -- CSV layer -------------------------------------------------------------
CSV_COLUMNS = [
    "scene_number", "shot_number", "generation_number", "verbatim_instructions",
    "lens", "aspect_ratio", "quality", "curated_description",
    "fountain_description", "fountain_text", "iteration_history",
    "characters", "location", "generation_method", "iteration_count",
    "source_frame", "estimated_cost", "prompt", "output_file", "status",
    "endpoint", "version_history", "hero_tags", "shotlist_ref",
]


def read_csv():
    """REQUIRES: hold CSV_LOCK for any read-modify-write cycle (a bare
    read for display may skip it, but every mutation path in this file
    reads AND writes under one CSV_LOCK acquisition).

    Read rows + fieldnames. Missing columns are added in memory only and
    persisted on the next write (the old version rewrote the CSV on every
    GET when migrating, which is wasteful and racy)."""
    path = csv_path()
    if not os.path.exists(path):
        return [], list(CSV_COLUMNS)
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames) if reader.fieldnames else list(CSV_COLUMNS)
    for c in CSV_COLUMNS:
        if c not in fieldnames:
            fieldnames.append(c)
    for r in rows:
        r.pop(None, None)  # DictReader stores overflow cells under None
        for c in fieldnames:
            if r.get(c) is None:
                r[c] = ""
    return rows, fieldnames


def _write_csv_file(path, rows, fieldnames):
    """REQUIRES: caller holds the lock guarding this file (CSV_LOCK for the
    shots CSV, SHOTLIST_LOCK for the shotlist). SIDE EFFECTS: writes the CSV
    atomically AND drops a snapshot into <dir>/.csv_backups/, pruning old
    ones.

    Atomic CSV write with a timestamped backup beside it. Prunes that
    file's backups older than 7 days, keeps at most 50 per file."""
    csv_dir = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    backup_dir = os.path.join(csv_dir, ".csv_backups")
    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, "%s.%s.csv" % (stem, ts))
        with open(backup_path, "w", newline="") as bf:
            bw = csv.DictWriter(bf, fieldnames=fieldnames, extrasaction="ignore")
            bw.writeheader()
            bw.writerows(rows)
        now = time.time()
        mine = [b for b in sorted(os.listdir(backup_dir))
                if b.startswith(stem + ".")]
        for b in mine:
            fp = os.path.join(backup_dir, b)
            if now - os.path.getmtime(fp) > 7 * 86400:
                os.remove(fp)
        mine = [b for b in sorted(os.listdir(backup_dir))
                if b.startswith(stem + ".")]
        if len(mine) > 50:
            for old in mine[:-50]:
                os.remove(os.path.join(backup_dir, old))
    except OSError:
        pass  # a backup failure must never block the main write

    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def write_csv(rows, fieldnames):
    # REQUIRES: CSV_LOCK held by caller.
    _write_csv_file(csv_path(), rows, fieldnames)


def csv_bytes(rows, fieldnames):
    """Serialize rows to CSV bytes (for exports)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def find_row_by_file(rows, output_file):
    """Return (row_index, row) or (None, None)."""
    target = (output_file or "").strip()
    for i, r in enumerate(rows):
        if (r.get("output_file") or "").strip() == target:
            return i, r
    return None, None


def resolve_row(rows, data):
    """Locate a shot by output_file (preferred — stable across client-side
    sorting) or row_index (CSV order). Raises ApiError if not found.

    CONTRACT with the frontend (payloadFor() in index.html): the payload is
    EITHER {"output_file": "<name>"} for a normally-keyed row, OR
    {"row_index": <int>} for rows whose output_file is blank or a duplicate
    (output_file resolves to the FIRST match only, so duplicates must come
    in by index). row_index is an index into raw CSV order — the order
    GET /api/shots returns — NOT the sorted/filtered grid order."""
    output_file = (data.get("output_file") or "").strip()
    if output_file:
        idx, row = find_row_by_file(rows, output_file)
        if idx is None:
            raise ApiError("Shot not found: " + output_file, 404)
        return idx, row
    idx = data.get("row_index")
    if idx is None:
        raise ApiError("Missing output_file or row_index")
    if not isinstance(idx, int) or idx < 0 or idx >= len(rows):
        raise ApiError("Row index out of range")
    return idx, rows[idx]


# -- Scene data ------------------------------------------------------------
SCENE_TEXT = {}


def _load_scene_text():
    global SCENE_TEXT
    sp = os.path.join(SCRIPT_DIR, "scene_text.json")
    try:
        if os.path.exists(sp):
            with open(sp) as sf:
                SCENE_TEXT = json.load(sf)
    except (OSError, json.JSONDecodeError) as e:
        print("Warning: could not load scene_text.json: %s" % e)


# Fountain scene -> location lookup (from THE WAIF numbered draft).
# NOTE: intentionally sparse — scenes 79, 81, 83, 86, 88, 90 and 231 have no
# slug of their own in the draft, and nothing exists past 278. Unknown
# scenes fall back to keyword inference from the shot instructions
# (see infer_location).
SCENE_LOOKUP = {
    "1": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "2": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "3": {"location": "INT. JACK'S BEDROOM - HOUSE - NIGHT"},
    "4": {"location": "INT. JACK'S BEDROOM - HOUSE - DAY"},
    "5": {"location": 'INT. KITCHEN - HOUSE - DAY'},
    "6": {"location": 'EXT. SUBURBAN HOME - DAY'},
    "7": {"location": 'EXT. UPSTATE NEW YORK - DAY'},
    "8": {"location": 'INT. FORESTER (MOVING) - DAY'},
    "9": {"location": 'EXT. NEW YORK - DAY'},
    "10": {"location": 'INT. FORESTER (MOVING) - DAY'},
    "11": {"location": 'INT. MOTEL ROOM - DAY'},
    "12": {"location": 'INT. BATHROOM - MOTEL ROOM - DAY'},
    "13": {"location": 'INT. MOTEL ROOM - DAY'},
    "14": {"location": 'EXT. MOTEL - DAY'},
    "15": {"location": 'INT. PICKUP (MOVING) - NEW YORK STATE - DAY'},
    "16": {"location": 'EXT. MUNICIPAL COURT BUILDING - DAY'},
    "17": {"location": 'INT. CONFERENCE ROOM - COURT BUILDING - DAY'},
    "18": {"location": 'INT. CAFETERIA - COURT BUILDING - DAY'},
    "19": {"location": 'INT. CORRIDOR - COURT BUILDING - DAY'},
    "20": {"location": 'EXT. PARKING LOT - COURT BUILDING - DAY'},
    "21": {"location": 'INT. PICKUP (MOVING) - COURT BUILDING - DAY'},
    "22": {"location": 'EXT. DARK WATER - DAY'},
    "23": {"location": 'EXT. BRIDGE - UPSTATE NEW YORK - DAY'},
    "24": {"location": 'INT. PICKUP (MOVING) - BRIDGE - DAY'},
    "25": {"location": 'EXT. UPSTATE NEW YORK - DAY'},
    "26": {"location": 'EXT. WORN ROAD - DAY'},
    "27": {"location": 'EXT. BROKEN BOW - DAY'},
    "28": {"location": 'INT. PICKUP (MOVING) - BROKEN BOW - DAY'},
    "29": {"location": 'EXT. PICKUP (MOVING) - FOREST ROAD - DAY'},
    "30": {"location": 'EXT. ACCESS ROAD - DAY'},
    "31": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "32": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "33": {"location": 'INT. KITCHENETTE - CABIN - DAY'},
    "34": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "35": {"location": 'INT. BATHROOM - CABIN - DAY'},
    "36": {"location": 'INT. KITCHENETTE - CABIN - DAY'},
    "37": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "38": {"location": 'EXT. REAR - CABIN - DAY'},
    "39": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "40": {"location": "INT. JACK'S ROOM - CABIN - DAY"},
    "41": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "42": {"location": 'INT. LIVING ROOM - HOUSE - DAY'},
    "43": {"location": 'INT. PICKUP (MOVING) - WOODS - DAY'},
    "44": {"location": 'EXT. MAIN STREET - BROKEN BOW - DAY'},
    "45": {"location": 'EXT. LAST CHANCE SUPPLY - DAY'},
    "46": {"location": 'INT. LAST CHANCE SUPPLY - DAY'},
    "47": {"location": 'INT. PICKUP (MOVING) - DAY'},
    "48": {"location": 'EXT. SCENIC STOP - DAY'},
    "49": {"location": 'INT. FOREST CLEARING - DAY'},
    "50": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "51": {"location": 'INT. LODGE ROOM - CABIN - SUNSET'},
    "52": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "53": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "54": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "55": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "56": {"location": 'INT. PICKUP (MOVING) - FRONT YARD - DAY'},
    "57": {"location": 'INT. ATTIC - CABIN - DAY'},
    "58": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "59": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "60": {"location": "INT. JACK'S ROOM - CABIN - DAY"},
    "61": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "62": {"location": 'INT. LAST CHANCE SUPPLY - DAY'},
    "63": {"location": 'EXT. LAST CHANCE SUPPLY - DAY'},
    "64": {"location": 'INT. CHEVROLET - DAY'},
    "65": {"location": 'INT. PICKUP (MOVING) - FOREST ROAD - DAY'},
    "66": {"location": 'EXT. FOREST TURN - DAY'},
    "67": {"location": 'INT. PICKUP (MOVING) - FOREST ROAD - DAY'},
    "68": {"location": 'EXT. FOREST PATH - DAY'},
    "69": {"location": 'INT. PICKUP - DAY'},
    "70": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "71": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "72": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "73": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "74": {"location": "INT. JACK'S ROOM - CABIN - NIGHT"},
    "75": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "76": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "77": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "78": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "80": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "82": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "84": {"location": "INT. JACK'S ROOM - CABIN - NIGHT"},
    "85": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "87": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "89": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "91": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "92": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "93": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "94": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "95": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "96": {"location": 'INT. BATHROOM - CABIN - NIGHT'},
    "97": {"location": 'INT. LODGE ROOM - CABIN - CONTINUOUS'},
    "98": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "99": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "100": {"location": 'INT. BATHROOM - CABIN - NIGHT'},
    "101": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "102": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "103": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "104": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "105": {"location": 'INT. LODGE ROOM - CABIN - CONTINUOUS'},
    "106": {"location": 'EXT. FRONT PORCH - CABIN - NIGHT'},
    "107": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "108": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "109": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "110": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "111": {"location": 'INT. PICKUP - NIGHT'},
    "112": {"location": 'EXT. PICKUP - NIGHT'},
    "113": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "114": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "115": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "116": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "117": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "118": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "119": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "120": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "121": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "122": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "123": {"location": 'INT. KITCHENETTE - CABIN - DAY'},
    "124": {"location": 'EXT. PICKUP - DAY'},
    "125": {"location": 'INT. PICKUP (MOVING) - BROKEN BOW - DAY'},
    "126": {"location": 'EXT. MAIN STREET - DAY'},
    "127": {"location": 'INT. SECOND LAST CHANCE SUPPLY - DAY'},
    "128": {"location": 'INT. BACKROOM - SECOND LAST CHANCE SUPPLY - DAY'},
    "129": {"location": 'INT. SECOND LAST CHANCE SUPPLY - DAY'},
    "130": {"location": 'EXT. SECOND LAST CHANCE SUPPLY - DAY'},
    "131": {"location": 'EXT. CLEARING - NEAR CABIN - DAY'},
    "132": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "133": {"location": 'INT. INSTITUTION - DAY'},
    "134": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "135": {"location": 'INT. BEDROOM - DAY'},
    "136": {"location": 'EXT. DRIVEWAY - DAY'},
    "137": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "138": {"location": 'INT. HALLWAY - DAY'},
    "139": {"location": 'INT. BEDROOM - ON WAIF - DAY'},
    "140": {"location": 'EXT. NEW YORK STREET - DAY'},
    "141": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "142": {"location": 'INT. OFFICE RECEPTION - DAY'},
    "143": {"location": 'INT. CORNER OFFICE - NEW YORK - DAY'},
    "144": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "145": {"location": 'INT. INTENSIVE CARE BED - HOSPITAL - DAY'},
    "146": {"location": 'INT. INTENSIVE CARE BED - NIGHT'},
    "147": {"location": 'INT. STORAGE ROOM - HOSPITAL - NIGHT'},
    "148": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "149": {"location": 'INT. CORRIDOR - INSTITUTION - DAY'},
    "150": {"location": 'INT. PATIENT ROOM - NIGHT'},
    "151": {"location": 'EXT. INSTITUTION - NIGHT'},
    "152": {"location": 'INT. DRIVER COCKPIT - NIGHT'},
    "153": {"location": 'EXT. FREEWAY - NIGHT'},
    "154": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "155": {"location": 'EXT. FREEWAY - NIGHT'},
    "156": {"location": 'INT. PADDED ROOM - DAY'},
    "157": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "158": {"location": 'INT. PADDED ROOM - DAY'},
    "159": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "160": {"location": 'INT. PICKUP (MOVING) - FOREST ROAD - DAY'},
    "161": {"location": 'EXT. UPSTATE NEW YORK - DAY'},
    "162": {"location": 'EXT. NORTH HAVEN - DAY'},
    "163": {"location": 'INT. PICKUP (MOVING) - NORTH HAVEN - DAY'},
    "164": {"location": 'EXT. MANSION - DAY'},
    "165": {"location": 'INT. KITCHENETTE - MANSION - DAY'},
    "166": {"location": "INT. PRESTON'S LAB - DAY"},
    "167": {"location": 'EXT. BRIDGE - DAY'},
    "168": {"location": 'EXT. SUBURB - DAY'},
    "169": {"location": 'INT. PICKUP (MOVING) - SUBURB - DAY'},
    "170": {"location": 'EXT. SUBURB - DAY'},
    "171": {"location": 'INT. PICKUP - DAY'},
    "172": {"location": 'EXT. DRIVEWAY - HOUSE - DAY'},
    "173": {"location": 'INT. PICKUP - DAY'},
    "174": {"location": 'EXT. HOUSE - DAY'},
    "175": {"location": 'INT. KITCHEN - HOUSE - DAY'},
    "176": {"location": 'EXT. BACKYARD - HOUSE - DAY'},
    "177": {"location": "EXT. NEIGHBOR'S GARDEN - DAY"},
    "178": {"location": "EXT. SIDE OF NEIGHBOR'S HOUSE - CONTINUOUS"},
    "179": {"location": 'EXT. SUBURB - CONTINUOUS'},
    "180": {"location": 'EXT. SCENIC PULLOUT - DAY'},
    "181": {"location": 'INT. PICKUP - DAY'},
    "182": {"location": 'INT. PICKUP (MOVING) - FRONT YARD - DAY'},
    "183": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "184": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "185": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "186": {"location": 'EXT. CABIN - DAY'},
    "187": {"location": 'EXT. FOREST - DAY'},
    "188": {"location": 'EXT. LAKESIDE - DAY'},
    "189": {"location": 'EXT. FOREST TRAIL - DAY'},
    "190": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "191": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "192": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "193": {"location": 'INT. FORESTER (MOVING) - BROKEN BOW - DAY'},
    "194": {"location": 'INT. T.R. GENERAL SUPPLIES - DAY'},
    "195": {"location": 'INT. FORESTER (MOVING) - BROKEN BOW - DAY'},
    "196": {"location": 'EXT. HIGHWAY - DAY'},
    "197": {"location": 'EXT. SUBURB - DAY'},
    "198": {"location": 'INT. FORESTER (MOVING) - SUBURB - SUNSET'},
    "199": {"location": 'EXT. SUBURBAN HOME - SUNSET'},
    "200": {"location": 'INT. HALLWAY - HOUSE - NIGHT'},
    "201": {"location": 'INT. HOUSE - FIRST FLOOR - NIGHT'},
    "202": {"location": "INT. JACK'S ROOM - HOUSE - NIGHT"},
    "203": {"location": 'INT. KITCHEN - HOUSE - NIGHT'},
    "204": {"location": 'INT. LIVING ROOM - HOUSE - NIGHT'},
    "205": {"location": 'EXT. HOUSE - NIGHT'},
    "206": {"location": 'INT. LIVING ROOM - HOUSE - NIGHT'},
    "207": {"location": 'EXT. HOUSE - NIGHT'},
    "208": {"location": 'INT. LIVING ROOM - HOUSE - NIGHT'},
    "209": {"location": "INT. JACK'S BEDROOM - HOUSE - NIGHT"},
    "210": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "211": {"location": 'EXT. FRONT LAWN - HOUSE - NIGHT'},
    "212": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "213": {"location": "INT. JACK'S ROOM - HOUSE - NIGHT"},
    "214": {"location": 'INT. LANDING - HOUSE - NIGHT'},
    "215": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "216": {"location": 'INT. STAIRCASE/GROUND FLOOR - HOUSE - NIGHT'},
    "217": {"location": 'INT. BASEMENT - HOUSE - NIGHT'},
    "218": {"location": 'INT. HALLWAY - HOUSE - NIGHT'},
    "219": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "220": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "221": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "222": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "223": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "224": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "225": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "226": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "227": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "228": {"location": 'INT. DOWNSTAIRS - HOUSE - NIGHT'},
    "229": {"location": 'EXT. FRONT LAWN - HOUSE - NIGHT'},
    "230": {"location": 'INT. FORESTER - NIGHT'},
    "232": {"location": 'EXT. HIGHWAY - NIGHT'},
    "233": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "234": {"location": 'INT. / EXT. FORESTER - NIGHT'},
    "235": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "236": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "237": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "238": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "239": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "240": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "241": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "242": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "243": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "244": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "245": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "246": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "247": {"location": 'INT. LODGE ROOM - NIGHT'},
    "248": {"location": 'INT. PICKUP - NIGHT'},
    "249": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "250": {"location": 'INT. PICKUP - NIGHT'},
    "251": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "252": {"location": 'INT. PICKUP - NIGHT'},
    "253": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "254": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "255": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "256": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "257": {"location": 'INT. CORNER OFFICE (FLASHBACK) - DAY'},
    "258": {"location": 'INT. CORNER OFFICE (FLASHBACK) - DAY'},
    "259": {"location": 'EXT. SKYSCRAPER (FLASHBACK)- DAY'},
    "260": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "261": {"location": 'EXT. SKYSCRAPER (FLASHBACK) - DAY'},
    "262": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "263": {"location": 'EXT. AVENUE (FLASHBACK) - DAY'},
    "264": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "265": {"location": 'INT. FORESTER (FLASHBACK) - DAY'},
    "266": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "267": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "268": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "269": {"location": "INT. JACK'S ROOM - CABIN - NIGHT"},
    "270": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "271": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "272": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "273": {"location": "INT. JACK'S BEDROOM - HOUSE - DAY"},
    "274": {"location": 'INT. KITCHEN - HOUSE - DAY'},
    "275": {"location": 'EXT. SUBURBAN HOME - DAY'},
    "276": {"location": 'EXT. UPSTATE NEW YORK - DAY'},
    "277": {"location": 'INT. FORESTER (MOVING) - DAY'},
    "278": {"location": 'EXT. INTERSECTION - DAY'},
}

# Keyword -> character mapping. JRM aliases are checked before plain "ben"
# so "Jonathan" / "JRM" shots don't tag both variants.
CHAR_KEYWORDS = [
    ("jrm", "Ben (JRM)"), ("jonathan", "Ben (JRM)"), ("ben", "Ben"),
    ("marie", "Marie"), ("waif", "Waif"), ("jack", "Jack"),
    ("neighbor", "Neighbor"), ("mother", "The Mother"), ("lawyer", "Lawyer"),
    ("jamie", "Jamie"), ("ricky", "Ricky"),
    ("schr\u00f6dinger", "Schr\u00f6dinger"), ("schrodinger", "Schr\u00f6dinger"),
]

LOC_KEYWORDS = [
    ("broken bow", "Broken Bow"), ("cabin", "Cabin - Broken Bow"),
    ("court", "Municipal Courthouse"), ("motel", "Motel"),
    ("pickup", "Pickup Truck"), ("suburban", "Suburban House"),
    ("intersection", "The Intersection"),
]


def detect_characters(instructions):
    text = (instructions or "").lower()
    chars = []
    for kw, name in CHAR_KEYWORDS:
        if kw in text and name not in chars:
            if kw == "ben" and "Ben (JRM)" in chars:
                continue
            chars.append(name)
    return ", ".join(chars)


def infer_location(scene_number, instructions):
    if scene_number in SCENE_LOOKUP:
        return SCENE_LOOKUP[scene_number]["location"]
    text = (instructions or "").lower()
    for kw, loc in LOC_KEYWORDS:
        if kw in text:
            return loc
    return ""


def autofill_shot(shot):
    """Fill fountain_text / location / characters when blank."""
    sc = (shot.get("scene_number") or "").strip()
    instr = shot.get("verbatim_instructions") or ""
    if sc in SCENE_TEXT and not shot.get("fountain_text"):
        shot["fountain_text"] = SCENE_TEXT[sc]
    if not shot.get("location"):
        loc = infer_location(sc, instr)
        if loc:
            shot["location"] = loc
    if not shot.get("characters"):
        chars = detect_characters(instr)
        if chars:
            shot["characters"] = chars


# -- Images ----------------------------------------------------------------
def list_images(directory):
    """Relative POSIX-style paths of all images under directory. The
    _thumbs cache tree is excluded — thumbnails are derivatives, not
    frames/refs of their own."""
    images = []
    base = Path(directory)
    if not base.exists():
        return images
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS:
            rel = p.relative_to(base)
            if THUMB_DIRNAME in rel.parts:
                continue
            images.append(rel.as_posix())
    return images


def refs_by_category():
    """Group reference images by their directory path (so nested
    sub-categories like locations/cabin work). The _archive tree is
    excluded — see archived_refs(). Known categories pre-populated from
    canon appear even when empty."""
    cats = {}
    for c in known_categories():
        cats.setdefault(c, [])
    for rel in list_images(refs_dir()):
        if rel == REF_ARCHIVE or rel.startswith(REF_ARCHIVE + "/"):
            continue
        d = os.path.dirname(rel)
        cats.setdefault(d or "uncategorized", []).append(rel)
    return cats


def archived_refs():
    """(archived, binned) lists of ref paths relative to the refs dir."""
    base = os.path.join(refs_dir(), REF_ARCHIVE)
    arch, binned = [], []
    for rel in list_images(base):
        full_rel = REF_ARCHIVE + "/" + rel
        if rel.startswith("_bin/"):
            binned.append(full_rel)
        else:
            arch.append(full_rel)
    return arch, binned


# -- Reference categories (canon pre-population) -----------------------------
REF_ROOT_CATEGORIES = ["locations", "characters", "hero_props", "vehicles"]
VEHICLE_WORDS = ("pickup", "forester", "chevrolet", "truck", "car", "van",
                 "ambulance", "bus")
_TOD_WORDS = {"DAY", "NIGHT", "SUNSET", "SUNRISE", "DUSK", "DAWN", "MORNING",
              "EVENING", "CONTINUOUS", "LATER"}


def categories_path():
    return os.path.join(project_dir(active_project().get("name", "the-waif")),
                        "categories.json")


def known_categories():
    try:
        with open(categories_path()) as f:
            cats = json.load(f)
        return cats if isinstance(cats, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_categories(cats):
    os.makedirs(os.path.dirname(categories_path()), exist_ok=True)
    tmp = categories_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(set(cats)), f, indent=2)
    os.replace(tmp, categories_path())


def _canon_texts(exts=(".fountain", ".txt", ".md")):
    """Raw text of fountain / canon / markdown docs in the project's canon
    dir. Best effort — the canon dir may not exist for new projects."""
    texts = []
    cd = canon_dir()
    if cd and os.path.isdir(cd):
        try:
            for p in sorted(Path(cd).iterdir()):
                if (p.is_file() and
                        p.suffix.lower() in exts):
                    try:
                        texts.append(p.read_text(errors="replace")[:800000])
                    except OSError:
                        pass
        except OSError:
            pass
    return texts


def canon_scenes():
    """Canonical scene list parsed from .fountain files in the canon dir.
    Scene headers are lines that start with a scene number followed by a
    dot — '1. INT. KITCHEN - DAY', '2A. EXT. YARD - NIGHT' — with a
    secondary pattern for standard fountain '#12#' slugline suffixes.
    Returns an ordered [{id, label}] list; falls back to the built-in
    SCENE_LOOKUP when the canon dir has no fountain scene headers."""
    scenes, seen = [], set()
    head_re = re.compile(r"^\s*(\d+[A-Za-z]?)\.\s+(.+?)\s*$")
    slug_num_re = re.compile(r"^\s*((?:INT|EXT|EST|I/E)[\./\s].*?)\s*"
                             r"#(\d+[A-Za-z]?)#\s*$", re.I)

    def add(sid, label):
        sid = sid.upper()
        if sid not in seen:
            seen.add(sid)
            scenes.append({"id": sid, "label": label})

    cd = canon_dir()
    if cd and os.path.isdir(cd):
        try:
            for p in sorted(Path(cd).iterdir()):
                if not (p.is_file() and p.suffix.lower() == ".fountain"):
                    continue
                try:
                    text = p.read_text(errors="replace")[:800000]
                except OSError:
                    continue
                for line in text.splitlines():
                    m = head_re.match(line)
                    if m:
                        add(m.group(1), m.group(2))
                        continue
                    m = slug_num_re.match(line)
                    if m:
                        add(m.group(2), m.group(1))
        except OSError:
            pass
    if not scenes:
        def skey(k):
            m = re.match(r"(\d+)([A-Za-z]*)", k)
            return (int(m.group(1)), m.group(2)) if m else (10**9, k)
        for sid in sorted(SCENE_LOOKUP, key=skey):
            add(sid, SCENE_LOOKUP[sid]["location"])
    return scenes


def canon_scene_text(scene_number):
    """Raw action/dialogue text of one scene: the body between its scene
    header and the next header in the canon dir's .fountain files, falling
    back to scene_text.json (SCENE_TEXT). The header line itself is
    stripped so callers get body text only; '' when the scene is unknown."""
    sc = str(scene_number or "").strip().upper()
    if not sc:
        return ""
    head_re = re.compile(r"^\s*(\d+[A-Za-z]?)\.\s+\S")
    slug_num_re = re.compile(r"^\s*(?:INT|EXT|EST|I/E)[\./\s].*?"
                             r"#(\d+[A-Za-z]?)#\s*$", re.I)

    def line_scene(line):
        m = head_re.match(line) or slug_num_re.match(line)
        return m.group(1).upper() if m else None

    for text in _canon_texts(exts=(".fountain",)):
        lines = text.splitlines()
        start = None
        for i, line in enumerate(lines):
            lsc = line_scene(line)
            if start is None:
                if lsc == sc:
                    start = i + 1
            elif lsc is not None:
                return "\n".join(lines[start:i]).strip()
        if start is not None:
            return "\n".join(lines[start:]).strip()
    body = (SCENE_TEXT.get(sc) or SCENE_TEXT.get(sc.lower()) or "").strip()
    if body:
        # scene_text.json entries start with the slugline ('INT. ... #N#')
        first, _, rest = body.partition("\n")
        if re.match(r"^\s*(INT|EXT|EST|I/E)[\.\s/]", first, re.I):
            return rest.strip()
        return body
    return ""


def canon_sluglines():
    """All INT./EXT. sluglines found in canon docs, falling back to the
    built-in scene lookup (and scene_text.json) for THE WAIF."""
    lines = []
    slug_re = re.compile(r"^\s*(?:INT|EXT|I/E)[\./].*$|^\s*(?:INT|EXT)\s.*$",
                         re.M | re.I)
    for t in _canon_texts():
        lines.extend(m.group(0).strip() for m in slug_re.finditer(t))
    if not lines:
        for t in SCENE_TEXT.values():
            first = (t or "").split("\n", 1)[0]
            if re.match(r"^\s*(INT|EXT|I/E)[\.\s/]", first, re.I):
                lines.append(re.sub(r"\s*#\d+#\s*$", "", first).strip())
        lines.extend(v["location"] for v in SCENE_LOOKUP.values())
    return lines


def canon_location_names():
    """Ordered unique major-location names ('house', 'cabin', ...) parsed
    from canon sluglines. Vehicle sluglines are diverted to vehicles."""
    locs, vehicles, seen = [], [], set()
    for line in canon_sluglines():
        _, loc, _ = parse_slugline(line)
        if not loc:
            continue
        # major location = last ' - ' segment; strip parentheticals
        major = loc.split(" - ")[-1]
        major = re.sub(r"\([^)]*\)", "", major).strip()
        if not major or major.upper() in _TOD_WORDS:
            continue
        slug = _slug(major.lower())
        if not slug or slug in seen:
            continue
        seen.add(slug)
        if any(w in major.lower() for w in VEHICLE_WORDS):
            vehicles.append(slug)
        else:
            locs.append(slug)
    return locs, vehicles


def canon_character_names():
    """Character names: dialogue cues found in canon docs, merged with the
    built-in keyword table."""
    names, seen = [], set()
    cue_re = re.compile(r"^\s*@?([A-Z][A-Z\.\-' ]{1,28})(?:\s*\(.*\))?\s*$",
                        re.M)
    counts = {}
    for t in _canon_texts():
        for m in cue_re.finditer(t):
            cue = m.group(1).strip().rstrip(".")
            if cue.upper() in ("INT", "EXT", "FADE IN", "FADE OUT", "CUT TO",
                               "THE END") or len(cue) < 2:
                continue
            counts[cue] = counts.get(cue, 0) + 1
    for cue, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n >= 3:  # a real character speaks more than twice
            slug = _slug(cue.lower())
            if slug and slug not in seen:
                seen.add(slug)
                names.append(slug)
    for _, name in CHAR_KEYWORDS:
        slug = _slug(name.lower().split("(")[0].strip())
        if slug and slug not in seen:
            seen.add(slug)
            names.append(slug)
    return names


def ensure_ref_categories():
    """Pre-populate the reference category tree (Locations > sub-locations,
    Characters, Hero Props, Vehicles) from canon documents. Runs once per
    project; user-added categories are preserved."""
    if known_categories():
        return
    cats = list(REF_ROOT_CATEGORIES)
    locs, vehicles = canon_location_names()
    cats.extend("locations/" + l for l in locs[:40])
    cats.extend("vehicles/" + v for v in vehicles[:15])
    cats.extend("characters/" + c for c in canon_character_names()[:25])
    save_categories(cats)


def find_in_tree(base_dir, filename):
    """Search recursively for an image by exact basename in base_dir.
    Only ALLOWED_IMAGE_EXTS are matched and only exact (case-insensitive)
    basename matches count — no substring fallback."""
    name_lower = os.path.basename(filename or "").lower()
    if not name_lower or not os.path.isdir(base_dir):
        return None
    if os.path.splitext(name_lower)[1] not in ALLOWED_IMAGE_EXTS:
        return None
    base = Path(base_dir)
    for p in base.rglob("*"):
        if p.is_file() and p.name.lower() == name_lower:
            if THUMB_DIRNAME in p.relative_to(base).parts:
                continue  # never resolve to a cached thumbnail
            return str(p)
    return None


def frame_exists(filename):
    fn = (filename or "").strip()
    if not fn:
        return False
    fd = frames_dir()
    if os.path.exists(os.path.join(fd, fn)):
        return True
    return find_in_tree(fd, os.path.basename(fn)) is not None


def make_thumbnail(src_path, base_dir, width, cache_dirname=THUMB_DIRNAME):
    """SIDE EFFECTS: writes/refreshes cache files under
    base_dir/<cache_dirname>/ (atomic tmp+replace, safe under concurrency).

    Return a cached width-px-wide thumbnail of src_path, generating it
    under base_dir/<cache_dirname>/ (mirroring the source tree) when missing
    or stale. Different widths must use different cache_dirnames so they
    don't overwrite each other. Falls back to src_path when Pillow is
    unavailable, the source is already narrower than width, or the resize
    fails for any reason."""
    if Image is None:
        return src_path
    base = os.path.realpath(base_dir)
    rel = os.path.relpath(os.path.realpath(src_path), base)
    if rel.startswith(".."):
        return src_path
    thumb = os.path.join(base, cache_dirname, rel)
    try:
        if (os.path.isfile(thumb) and
                os.path.getmtime(thumb) >= os.path.getmtime(src_path)):
            return thumb
        with Image.open(src_path) as im:
            if im.width <= width:
                return src_path
            fmt = im.format or "PNG"
            if fmt == "JPEG" and im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            elif im.mode == "P":
                im = im.convert("RGBA")
            h = max(1, round(im.height * width / im.width))
            im = im.resize((width, h), Image.LANCZOS)
            os.makedirs(os.path.dirname(thumb), exist_ok=True)
            # unique tmp + atomic replace: concurrent requests for the same
            # thumbnail can't serve a half-written file
            tmp = "%s.tmp%s" % (thumb, os.urandom(4).hex())
            im.save(tmp, format=fmt)
            os.replace(tmp, thumb)
        return thumb
    except Exception:
        return src_path


def safe_path(base_dir, rel_path, must_exist=True):
    """Resolve rel_path strictly inside base_dir. Uses realpath on both
    sides so a relative base_dir or a symlink can't be used to escape
    (the old startswith check compared a relative join against an absolute
    base, so it always failed open for relative dirs)."""
    base = os.path.realpath(base_dir)
    clean = os.path.normpath(rel_path or "").lstrip("/\\")
    full = os.path.realpath(os.path.join(base, clean))
    if full != base and not full.startswith(base + os.sep):
        return None
    if must_exist and not os.path.isfile(full):
        return None
    return full


CT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif",
    "html": "text/html; charset=utf-8", "json": "application/json",
}


# -- OpenAI client ---------------------------------------------------------
def get_openai_key():
    """OPENAI_API_KEY env var wins; otherwise read the configured .env."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH) as ef:
                for line in ef:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() in ("OPENAI_KEY", "VOICE_TOOLS_OPENAI_KEY",
                                     "OPENAI_API_KEY"):
                        return v.strip().strip("'").strip('"')
        except OSError:
            pass
    return None


def require_openai_key():
    key = ctx().get("openai_key") or get_openai_key()
    if not key:
        raise ApiError("OpenAI API key not found — set OPENAI_API_KEY or "
                       "point --env at a file containing OPENAI_KEY=...", 500)
    return key


def _openai_call(req, timeout, label="GPT Image 2"):
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode(errors="replace")[:300]
        except Exception:
            pass
        raise ApiError("%s error (HTTP %d): %s" % (label, e.code, detail), 400)
    except urllib.error.URLError as e:
        raise ApiError("Could not reach the OpenAI API: %s" % e.reason, 502)
    except (TimeoutError, OSError) as e:
        raise ApiError("OpenAI API timed out or dropped: %s" % e, 504)


def _extract_image_bytes(resp):
    data = resp.get("data") or []
    if not data:
        raise ApiError("The API returned no image data", 502)
    item = data[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        try:
            return urllib.request.urlopen(item["url"], timeout=120).read()
        except Exception as e:
            raise ApiError("Could not download generated image: %s" % e, 502)
    raise ApiError("Unrecognized image payload from API", 502)


def openai_generate_image(prompt, key, quality=None, model=None):
    """Text-to-image via the active project's model + quality settings."""
    model = model if model in IMAGE_MODELS else active_model()
    spec = IMAGE_MODELS[model]
    quality = quality if quality in QUALITY_LEVELS else active_quality()
    body = json.dumps({
        "model": model, "prompt": prompt, "n": 1,
        "size": spec["size"], "quality": quality,
    }).encode()
    req = urllib.request.Request(
        spec["generate_url"], data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    return _extract_image_bytes(_openai_call(req, 180))


def openai_edit_image(source_bytes, prompt, key, quality=None, model=None):
    model = model if model in IMAGE_MODELS else active_model()
    spec = IMAGE_MODELS[model]
    quality = quality if quality in QUALITY_LEVELS else active_quality()
    boundary = "----Boundary" + os.urandom(16).hex()

    def part(lines):
        return "\r\n".join(lines).encode() + b"\r\n"

    body = b""
    body += part(["--" + boundary,
                  'Content-Disposition: form-data; name="image"; filename="source.png"',
                  "Content-Type: image/png", ""])
    body += source_bytes + b"\r\n"
    for name, value in (("prompt", prompt), ("model", model),
                        ("size", spec["size"]), ("quality", quality), ("n", "1")):
        body += part(["--" + boundary,
                      'Content-Disposition: form-data; name="%s"' % name,
                      "", value])
    body += ("--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(
        spec["edit_url"], data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    return _extract_image_bytes(_openai_call(req, 180))


def openai_describe_image(img_bytes, mime, key):
    """Image -> text via the vision chat model. Returns the description."""
    data_url = "data:%s;base64,%s" % (mime,
                                      base64.b64encode(img_bytes).decode())
    body = json.dumps({
        "model": VISION_MODEL,
        "max_tokens": 600,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": DESCRIBE_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
    }).encode()
    req = urllib.request.Request(
        VISION_URL, data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    resp = _openai_call(req, 120, label="GPT-4o")
    try:
        text = (resp["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise ApiError("Unrecognized response from GPT-4o", 502)
    if not text:
        raise ApiError("GPT-4o returned an empty description", 502)
    return text


# -- Versioning ------------------------------------------------------------
def next_version_name(old_file, scene_number):
    """Return (new_output_file, version_number) for a shot's next render.

    A version token (``_vN``) may be followed by a suffix such as ``_copy`` on
    a duplicated shot. That suffix must be preserved so the duplicate keeps its
    own naming chain (``_v6_copy`` -> ``_v7_copy``) instead of shedding the
    suffix and colliding with the original's chain (``_v6`` -> ``_v7``), which
    left duplicate rows pointing at files the CSV no longer matched."""
    if old_file:
        ext = os.path.splitext(old_file)[1] or ".png"
        stem = (old_file[:-len(ext)] if ext and old_file.endswith(ext)
                else os.path.splitext(old_file)[0])
        m = re.search(r"_v(\d+)", stem)
        if m:
            new_v = int(m.group(1)) + 1
            new_stem = "%s_v%d%s" % (stem[:m.start()], new_v, stem[m.end():])
            return new_stem + ext, new_v
        return "%s_v2%s" % (stem, ext), 2
    return "%s_sc_%s_v1.png" % (file_prefix(), scene_number or "XX"), 1


def push_version(shot, old_file):
    """Append old_file to the shot's version_history JSON list.
    CONTRACT: version_history is stored in the CSV as a JSON-encoded list
    string ('["a.png","b.png"]') — both sides must json-parse it, never
    split on commas (filenames could contain them)."""
    try:
        history = json.loads(shot.get("version_history") or "[]")
    except json.JSONDecodeError:
        history = []
    if old_file and old_file not in history:
        history.append(old_file)
    shot["version_history"] = json.dumps(history)
    return history


def clean_version_history(shot):
    """Drop history entries whose files no longer exist on disk, so the UI
    never renders broken thumbnails."""
    try:
        history = json.loads(shot.get("version_history") or "[]")
    except json.JSONDecodeError:
        history = []
    cleaned = [h for h in history if frame_exists(h)]
    if cleaned != history:
        shot["version_history"] = json.dumps(cleaned)
    return cleaned


def build_generation_prompt(shot):
    """Prompt from curated_description + lens/location/heroes/house style."""
    if shot.get("prompt"):
        return shot["prompt"]
    curated = (shot.get("curated_description") or "").strip()
    if not curated:
        raise ApiError("Shot needs curation first — fill in the curated "
                       "description, then generate", 400)
    # If curated already ends with the house-style marker, it was built
    # during create — use it as-is to avoid double-injection.
    if "No text, no watermark, no logos" in curated:
        return curated
    # Legacy shots: build from curated + components
    parts = [curated]
    lens = (shot.get("lens") or "28mm").strip()
    if lens:
        parts.append("Shot with " + lens + " lens")
    loc = (shot.get("location") or "").strip()
    if loc:
        parts.append(loc)
    for frag in hero_fragments(shot):
        parts.append("Keep this element exactly consistent — " + frag)
    parts.append("Desaturated palette, cool shadows")
    parts.append("Photorealistic cinematic still from an indie horror film")
    ratio = (shot.get("aspect_ratio") or "2.39:1").strip()
    parts.append("Scope " + ratio + ". No text, no watermark, no logos.")
    return ". ".join(parts)


def hero_regen_prompt(hero):
    """Image-to-image instruction used by mass regeneration."""
    bits = ['Update the %s "%s" in this frame so it matches this exact '
            "description: %s" % (hero.get("type", "asset"),
                                 hero.get("name", ""),
                                 (hero.get("description") or "").strip())]
    if hero.get("breakdown"):
        bits.append("Details: " + hero["breakdown"])
    if hero.get("colors"):
        bits.append("Color palette: " + hero["colors"])
    return ". ".join(bits) + EDIT_SUFFIX


def _commit_edit(old_file, img_bytes, edit_prompt, quality=None):
    """Write an edited frame to disk and version-bump its CSV row. Shared
    by /api/edit and the mass-regeneration worker.
    REQUIRES: caller must NOT hold CSV_LOCK (this function acquires it).
    SIDE EFFECTS: writes the new frame file AND rewrites the shots CSV."""
    quality = quality if quality in QUALITY_LEVELS else active_quality()
    with CSV_LOCK:
        rows, fieldnames = read_csv()
        idx, shot = find_row_by_file(rows, old_file)
        if idx is None:
            raise ApiError("Shot vanished during edit: " + old_file, 409)
        output_file, new_v = next_version_name(old_file,
                                               shot.get("scene_number", "XX"))
        fd = frames_dir()
        out_path = os.path.join(fd, output_file)
        os.makedirs(fd, exist_ok=True)
        with open(out_path, "wb") as of:
            of.write(img_bytes)
        push_version(shot, old_file)
        shot["output_file"] = output_file
        shot["status"] = "edited"
        shot["generation_method"] = "edit"
        shot["endpoint"] = "/v1/images/edits (multipart/form-data POST)"
        shot["estimated_cost"] = QUALITY_COST.get(active_quality(), "$0.07")
        shot["quality"] = active_quality()
        shot["prompt"] = edit_prompt
        shot["source_frame"] = old_file
        shot["iteration_count"] = str(new_v)
        history = clean_version_history(shot)
        write_csv(rows, fieldnames)
    return output_file, new_v, history


# -- Request throttling ------------------------------------------------------
# Generate/edit calls hold a worker thread for up to 3 minutes each; a
# per-IP semaphore caps how many can be in flight at once (503 beyond that).
THROTTLED_ROUTES = {"/api/generate", "/api/edit", "/api/generate_ref",
                    "/api/ref_edit", "/api/mass_regen", "/api/describe_ref"}
_GEN_SEMS = {}
_GEN_SEMS_LOCK = threading.Lock()


def _gen_semaphore(ip):
    with _GEN_SEMS_LOCK:
        sem = _GEN_SEMS.get(ip)
        if sem is None:
            sem = threading.BoundedSemaphore(GEN_CONCURRENCY)
            _GEN_SEMS[ip] = sem
        return sem


# -- HTTP handler ----------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ShotDash/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # quiet

    # === RESPONSE PLUMBING ===
    # Shared send helpers. Everything user-visible goes through _respond;
    # JSON errors through _error. No endpoint writes to self.wfile directly.
    def _respond(self, status, content_type, body_bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body_bytes)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-transfer

    def _json(self, data, status=200):
        self._respond(status, "application/json", json.dumps(data).encode())

    def _error(self, msg, status=400):
        self._json({"error": msg}, status)

    def _file(self, path, content_type):
        if not os.path.isfile(path):
            return self._error("File not found", 404)
        try:
            with open(path, "rb") as f:
                self._respond(200, content_type, f.read())
        except OSError as e:
            self._error("Could not read file: %s" % e, 500)

    def _serve_image(self, filepath):
        ext = os.path.splitext(filepath)[1].lower().lstrip(".")
        self._file(filepath, CT.get(ext, "application/octet-stream"))

    def _download(self, body, content_type, filename):
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % filename)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise ApiError("Bad Content-Length")
        if length > MAX_BODY_BYTES:
            # body is left unread — don't reuse this connection
            self.close_connection = True
            raise ApiError("Request body too large (max 1 MB)", 413)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError("Invalid JSON")
        if not isinstance(data, dict):
            raise ApiError("Expected a JSON object")
        return data

    # === GET ENDPOINTS (read-only + image serving) ===
    def do_GET(self):
        snapshot_ctx()
        try:
            self._route_get()
        except ApiError as e:
            self._error(str(e), e.status)
        except Exception as e:
            self._error("Server error: %r" % e, 500)

    def _route_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._file(HTML_PATH, CT["html"])

        elif path == "/api/health":
            self._json({"ok": True, "file": thumb_file, "time": time.time()})

        elif path == "/favicon.ico":
            self._respond(204, "image/x-icon", b"")

        elif path == "/api/shots":
            with CSV_LOCK:
                rows, _ = read_csv()
            self._json({"shots": rows})

        elif path == "/api/frames":
            images = list_images(frames_dir())

            def prefer(new, old):
                # basename collisions: root frames dir wins, then the
                # alphabetically first subdir
                if old is None:
                    return True
                new_root, old_root = "/" not in new, "/" not in old
                if new_root != old_root:
                    return new_root
                return new < old

            frames, frames_lower = {}, {}
            for img in images:
                base = os.path.basename(img)
                if prefer(img, frames.get(base)):
                    frames[base] = img
                low = base.lower()
                if prefer(img, frames_lower.get(low)):
                    frames_lower[low] = img
            self._json({"frames": frames, "frames_lower": frames_lower,
                        "all": images})

        elif path == "/api/refs":
            cats = refs_by_category()
            flat = [f for imgs in cats.values() for f in imgs]
            self._json({"categories": cats, "images": flat})

        elif path == "/api/archived_refs":
            arch, binned = archived_refs()
            self._json({"archived": arch, "binned": binned})

        elif path == "/api/projects":
            proj = active_project()
            self._json({
                "projects": list_projects(),
                "active": proj.get("name", ""),
                "settings": {k: proj.get(k, "") for k in
                             ("quality", "model", "file_prefix", "canon_dir")},
                "models": [{"id": mid, "label": IMAGE_MODELS[mid]["label"]}
                           for mid in IMAGE_MODELS],
                "qualities": list(QUALITY_LEVELS),
                "costs": dict(QUALITY_COST),
            })

        elif path == "/api/canon":
            scenes = canon_scenes()
            self._json({"scenes": scenes,
                        "map": {s["id"]: s["label"] for s in scenes}})

        elif path == "/api/shotlist":
            with SHOTLIST_LOCK:
                rows = read_shotlist()
            sort_shotlist(rows)
            self._json({"rows": rows, "columns": SHOTLIST_COLUMNS})

        elif path == "/api/heroes":
            self._json({"heroes": load_heroes()})

        elif path == "/api/categories":
            # Pre-populated category tree (locations > sub-locations,
            # characters, hero_props, vehicles) plus any category that has
            # images on disk. Flat 'a/b' paths; the client builds the tree.
            cats = sorted(set(known_categories()) | set(refs_by_category()))
            self._json({"categories": cats, "roots": REF_ROOT_CATEGORIES})

        elif path == "/api/job_status":
            q = urllib.parse.parse_qs(parsed.query)
            jid = (q.get("id") or [""])[0]
            with JOBS_LOCK:
                job = dict(JOBS.get(jid) or {})
            if job:
                self._json(job)
            else:
                self._error("Unknown job: " + jid, 404)

        elif path == "/api/export":
            self._export(parsed)

        elif path.startswith("/api/thumb/frame/"):
            rel = urllib.parse.unquote(path[len("/api/thumb/frame/"):])
            filepath = safe_path(frames_dir(), rel)
            if not filepath:
                filepath = find_in_tree(frames_dir(), os.path.basename(rel))
            if filepath:
                self._serve_image(make_thumbnail(filepath, frames_dir(), 200))
            else:
                self._error("Frame not found: " + rel, 404)

        elif path.startswith("/api/thumb/ref/"):
            rel = urllib.parse.unquote(path[len("/api/thumb/ref/"):])
            filepath = safe_path(refs_dir(), rel)
            if not filepath:
                filepath = find_in_tree(refs_dir(), os.path.basename(rel))
            if filepath:
                self._serve_image(make_thumbnail(filepath, refs_dir(), 150))
            else:
                self._error("Reference not found: " + rel, 404)

        elif path.startswith("/api/preview/frame/"):
            rel = urllib.parse.unquote(path[len("/api/preview/frame/"):])
            filepath = safe_path(frames_dir(), rel)
            if not filepath:
                filepath = find_in_tree(frames_dir(), os.path.basename(rel))
            if filepath:
                self._serve_image(make_thumbnail(
                    filepath, frames_dir(), FRAME_PREVIEW_WIDTH,
                    cache_dirname=PREVIEW_DIRNAME))
            else:
                self._error("Frame not found: " + rel, 404)

        elif path.startswith("/api/frame/"):
            rel = urllib.parse.unquote(path[len("/api/frame/"):])
            filepath = safe_path(frames_dir(), rel)
            if not filepath:
                filepath = find_in_tree(frames_dir(), os.path.basename(rel))
            if filepath:
                self._serve_image(filepath)
            else:
                self._error("Frame not found: " + rel, 404)

        elif path.startswith("/api/ref/"):
            rel = urllib.parse.unquote(path[len("/api/ref/"):])
            filepath = safe_path(refs_dir(), rel)
            if not filepath:
                filepath = find_in_tree(refs_dir(), os.path.basename(rel))
            if filepath:
                self._serve_image(filepath)
            else:
                self._error("Reference not found: " + rel, 404)

        else:
            self._error("Not found", 404)

    # === EXPORT ENDPOINTS ===
    def _export(self, parsed):
        """GET /api/export?what=shots|shotlist|all&format=csv|json"""
        q = urllib.parse.parse_qs(parsed.query)
        what = (q.get("what") or ["all"])[0]
        fmt = (q.get("format") or ["json"])[0]
        stamp = time.strftime("%Y%m%d_%H%M%S")
        proj = active_project()
        pname = _slug(proj.get("name", "project")) or "project"
        if fmt == "csv":
            if what == "shots":
                with CSV_LOCK:
                    rows, fieldnames = read_csv()
                body = csv_bytes(rows, fieldnames)
            elif what == "shotlist":
                with SHOTLIST_LOCK:
                    rows = read_shotlist()
                sort_shotlist(rows)
                body = csv_bytes(rows, SHOTLIST_COLUMNS)
            else:
                raise ApiError("CSV export needs what=shots or what=shotlist")
            self._download(body, "text/csv",
                           "%s_%s_%s.csv" % (pname, what, stamp))
            return
        # JSON: full project dump (shots + shotlist + heroes + categories)
        with CSV_LOCK:
            shots, _ = read_csv()
        with SHOTLIST_LOCK:
            shotlist = read_shotlist()
        sort_shotlist(shotlist)
        payload = {
            "project": proj.get("name", ""),
            "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "settings": {k: v for k, v in proj.items()},
            "shots": shots,
            "shotlist": shotlist,
            "heroes": load_heroes(),
            "reference_categories": refs_by_category(),
        }
        if what == "shots":
            payload = {"project": payload["project"],
                       "exported": payload["exported"], "shots": shots}
        elif what == "shotlist":
            payload = {"project": payload["project"],
                       "exported": payload["exported"], "shotlist": shotlist}
        self._download(json.dumps(payload, indent=2).encode(),
                       "application/json",
                       "%s_%s_%s.json" % (pname, what, stamp))

    # === POST DISPATCH ===
    # GOTCHA: POST_ROUTES is populated AFTER the class body (bottom of this
    # file). Any method accidentally dedented out of Handler makes that table
    # raise AttributeError at import — the server won't start. That's the
    # failure mode of careless pasting in this class; keep 4-space method
    # indentation and re-run py_compile after edits here.
    POST_ROUTES = {}  # filled in below the class body

    def do_POST(self):
        snapshot_ctx()
        parsed = urllib.parse.urlparse(self.path)
        self.post_query = urllib.parse.parse_qs(parsed.query)
        handler = self.POST_ROUTES.get(parsed.path)
        if handler is None:
            return self._error("Unknown endpoint: " + parsed.path, 404)
        # CSRF / DNS-rebinding hardening: browsers can't send a cross-origin
        # application/json POST without a preflight, so require it.
        ct = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ct.lower() != "application/json":
            self.close_connection = True
            return self._error("Content-Type must be application/json", 415)
        sem = None
        if parsed.path in THROTTLED_ROUTES:
            sem = _gen_semaphore(self.client_address[0])
            if not sem.acquire(blocking=False):
                return self._error("Too many concurrent generate/edit "
                                   "requests — try again shortly", 503)
        try:
            data = self._read_body()
            handler(self, data)
        except ApiError as e:
            self._error(str(e), e.status)
        except Exception as e:
            self._error("Server error: %r" % e, 500)
        finally:
            if sem is not None:
                sem.release()

    # === SHOT CSV ENDPOINTS (reorder / create / update / archive) ===
    def api_reorder(self, data):
        """Reorder within a visible subset: the listed rows exchange their
        existing shot_number values to match the new order; every row not
        listed keeps its number. Use /api/reorder_all to renumber the whole
        board."""
        order = data.get("order") or []
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            by_file = {}
            for r in rows:
                by_file.setdefault((r.get("output_file") or "").strip(), r)
            targets, seen = [], set()
            for fn in order:
                r = by_file.get((fn or "").strip())
                if r is not None and id(r) not in seen:
                    seen.add(id(r))
                    targets.append(r)
            if not targets:
                raise ApiError("No matching shots in order list")

            def numkey(v):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0

            numbers = sorted((r.get("shot_number") or "" for r in targets),
                             key=numkey)
            for r, n in zip(targets, numbers):
                r["shot_number"] = n
            write_csv(rows, fieldnames)
        self._json({"ok": True, "file": thumb_file, "reordered": len(targets)})

    def api_reorder_all(self, data):
        """Global sequential renumbering: 'order' lists output_files in the
        new order; anything not listed is appended after."""
        order = data.get("order") or []
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            by_file = {}
            for r in rows:
                by_file.setdefault((r.get("output_file") or "").strip(), r)
            n = 0
            seen = set()
            for fn in order:
                r = by_file.get((fn or "").strip())
                if r is not None and id(r) not in seen:
                    n += 1
                    r["shot_number"] = str(n)
                    seen.add(id(r))
            for r in rows:
                if id(r) not in seen:
                    n += 1
                    r["shot_number"] = str(n)
            write_csv(rows, fieldnames)
        self._json({"ok": True})

    def api_create(self, data):
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            new_row = {f: str(data.get(f, "") or "") for f in fieldnames}
            if not new_row.get("status"):
                new_row["status"] = "pending"
            sc = new_row.get("scene_number", "")
            existing = [int(r.get("shot_number") or 0) for r in rows
                        if r.get("scene_number") == sc]
            new_row["shot_number"] = str(max(existing) + 1 if existing else 1)
            # Uniquify the output filename — duplicate output_files would
            # break file-keyed lookups everywhere else.
            of = (new_row.get("output_file") or "").strip()
            if of:
                taken = {(r.get("output_file") or "").strip() for r in rows}
                if of in taken:
                    base, ext = os.path.splitext(of)
                    n = 2
                    while "%s_%d%s" % (base, n, ext) in taken:
                        n += 1
                    new_row["output_file"] = "%s_%d%s" % (base, n, ext)
            autofill_shot(new_row)
            # Build the full curated description on creation — lens, heroes,
            # house style, everything — so the user sees exactly what generate
            # will send. The marker at the end tells build_generation_prompt()
            # to use it as-is instead of re-assembling.
            if not (new_row.get("curated_description") or "").strip():
                verbatim = (new_row.get("verbatim_instructions") or "").strip()
                if verbatim:
                    curated = verbatim
                    tags = (new_row.get("hero_tags") or "").strip()
                    if tags:
                        heroes = load_heroes()
                        for h in heroes:
                            if not h.get("archived"):
                                if h["name"] in [t.strip() for t in tags.split(",")] or h["id"] in [t.strip() for t in tags.split(",")]:
                                    desc = (h.get("description") or "").strip()
                                    if desc:
                                        curated += ". Keep this element exactly consistent — " + desc
                    lens = (new_row.get("lens") or "28mm").strip()
                    if lens:
                        curated += ". Shot with " + lens + " lens"
                    loc = (new_row.get("location") or "").strip()
                    if loc:
                        curated += ". " + loc
                    curated += ". Desaturated palette, cool shadows. Photorealistic cinematic still from an indie horror film. Scope " + (new_row.get("aspect_ratio") or "2.39:1").strip() + ". No text, no watermark, no logos."
                    new_row["curated_description"] = curated
            # Insert after a specific row if after_file is provided
            after_file = (data.get("after_file") or "").strip()
            pos = len(rows)
            if after_file:
                for i, r in enumerate(rows):
                    if (r.get("output_file") or "").strip() == after_file:
                        pos = i + 1
                        break
            rows.insert(pos, new_row)
            write_csv(rows, fieldnames)
        self._json({"ok": True, "file": thumb_file, "row": new_row})

    def api_update(self, data):
        """Set one field. Accepts {field, value} plus a resolve_row payload
        (output_file | row_index). Also accepts the legacy payload
        {row_index, status: X}. GOTCHA: field can be ANY CSV column,
        including output_file — writing a duplicate output_file here breaks
        the identity invariant; the frontend never does this, keep it so."""
        field = data.get("field")
        value = data.get("value")
        if field is None and "status" in data:
            field, value = "status", data.get("status")
        if not field:
            raise ApiError("Missing field")
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            if field not in fieldnames:
                raise ApiError("Unknown field: " + str(field))
            idx, row = resolve_row(rows, data)
            row[field] = str(value if value is not None else "")
            write_csv(rows, fieldnames)
        self._json({"ok": True})

    api_edit_text = api_update  # same semantics, kept for URL compatibility

    def api_archive(self, data):
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, row = resolve_row(rows, data)
            prev = row.get("status") or "pending"
            row["status"] = "archived"
            write_csv(rows, fieldnames)
        self._json({"ok": True, "file": thumb_file, "previous_status": prev})

    def api_unarchive(self, data):
        """Restore an archived shot. 'restore_status' lets the client undo
        to the exact pre-archive status; otherwise a sensible default is
        chosen (generated if an image exists, else pending)."""
        restore = (data.get("restore_status") or "").strip()
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, row = resolve_row(rows, data)
            if restore and restore != "archived":
                row["status"] = restore
            elif frame_exists(row.get("output_file")):
                row["status"] = "generated"
            else:
                row["status"] = "pending"
            write_csv(rows, fieldnames)
        self._json({"ok": True, "file": thumb_file, "status": row["status"]})

    # === GENERATION ENDPOINTS (OpenAI image calls) ===
    # The OpenAI call happens OUTSIDE the CSV lock (it can take 3 minutes);
    # the row is re-resolved afterwards in case the CSV changed meanwhile.

    def api_generate(self, data):
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, shot = resolve_row(rows, data)
            # Fresh shots arrive with no curated_description (the create
            # flow never sets it) — seed it from the verbatim instructions
            # plus scene autofill BEFORE the prompt builder checks it, and
            # persist so it sticks even if the API call fails.
            if not (shot.get("curated_description") or "").strip():
                autofill_shot(shot)
                verbatim = (shot.get("verbatim_instructions") or "").strip()
                if verbatim:
                    shot["curated_description"] = verbatim[:300]
                    write_csv(rows, fieldnames)
            prompt = build_generation_prompt(shot)
            identity = (shot.get("output_file") or "").strip()
            scene = shot.get("scene_number", "XX")
        key = require_openai_key()
        quality = requested_quality(data)
        model = active_model()
        img_bytes = openai_generate_image(prompt, key, quality, model)

        with CSV_LOCK:
            rows, fieldnames = read_csv()
            if identity:
                idx, shot = resolve_row(rows, {"output_file": identity})
            else:
                idx, shot = resolve_row(rows, data)
            old_file = (shot.get("output_file") or "").strip()
            output_file, new_v = next_version_name(old_file, scene)
            fd = frames_dir()
            out_path = os.path.join(fd, output_file)
            os.makedirs(fd, exist_ok=True)
            with open(out_path, "wb") as of:
                of.write(img_bytes)
            if old_file:
                push_version(shot, old_file)
            shot["status"] = "generated"
            shot["output_file"] = output_file
            shot["prompt"] = prompt
            shot["aspect_ratio"] = shot.get("aspect_ratio") or "2.39:1"
            shot["quality"] = quality
            shot["generation_method"] = shot.get("generation_method") or "generate"
            shot["estimated_cost"] = QUALITY_COST.get(quality, "$0.07")
            shot["iteration_count"] = str(new_v)
            if not shot.get("lens"):
                shot["lens"] = "28mm"
            if not shot.get("endpoint"):
                shot["endpoint"] = "/v1/images/generations (JSON POST)"
            if not shot.get("curated_description"):
                shot["curated_description"] = (shot.get("verbatim_instructions") or "")[:300]
            autofill_shot(shot)
            history = clean_version_history(shot)
            write_csv(rows, fieldnames)
        self._json({"ok": True, "file": thumb_file, "output_file": output_file,
                    "size_kb": len(img_bytes) // 1024, "version": new_v,
                    "history": history})

    def api_edit(self, data):
        # CONTRACT: body = {edit_instructions, edit_suffix?, hero_tags?,
        # quality?} + resolve_row payload (output_file | row_index).
        # hero_tags is comma-separated names/ids; edit_suffix replaces the
        # built-in EDIT_SUFFIX when non-empty (frontend "Edit behaviour"
        # field). The client loops for multi-edit (count is client-side).
        edit_instructions = (data.get("edit_instructions") or "").strip()
        if not edit_instructions:
            raise ApiError("Missing edit_instructions")
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, shot = resolve_row(rows, data)
            old_file = (shot.get("output_file") or "").strip()
            if not old_file:
                raise ApiError("Shot has no image to edit")
            source_path = os.path.join(frames_dir(), old_file)
            if not os.path.exists(source_path):
                source_path = find_in_tree(frames_dir(),
                                           os.path.basename(old_file))
                if not source_path:
                    raise ApiError("Source image not found: " + old_file)
            with open(source_path, "rb") as sf:
                source_data = sf.read()
        # Assemble the final prompt: hero fragments (consistency), then the
        # edit itself, then the behaviour suffix. The old code built the
        # hero-injected string into a variable it never sent (so tagged
        # heroes had no effect on the edit) and dropped the client's
        # edit_suffix field entirely.
        parts = []
        hero_tags = (data.get("hero_tags") or "").strip()
        if hero_tags:
            fragments = hero_fragments({"hero_tags": hero_tags})
            if fragments:
                parts.append(". ".join(
                    "Keep this element exactly consistent — " + f
                    for f in fragments))
        parts.append(edit_instructions)
        edit_prompt = ". ".join(parts).rstrip(".")
        suffix = (data.get("edit_suffix") or "").strip()
        if suffix:
            edit_prompt += ". " + suffix
        else:
            edit_prompt += EDIT_SUFFIX
        key = require_openai_key()
        quality = requested_quality(data)
        img_bytes = openai_edit_image(source_data, edit_prompt, key, quality)
        output_file, new_v, history = _commit_edit(old_file, img_bytes,
                                                   edit_prompt, quality)
        self._json({"ok": True, "file": thumb_file, "output_file": output_file,
                    "size_kb": len(img_bytes) // 1024, "version": new_v,
                    "history": history})

    def api_swap_version(self, data):
        swap_file = (data.get("swap_file") or "").strip()
        if not swap_file:
            raise ApiError("Missing swap_file")
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, shot = resolve_row(rows, data)
            try:
                history = json.loads(shot.get("version_history") or "[]")
            except json.JSONDecodeError:
                history = []
            if swap_file not in history:
                raise ApiError("File not in version history")
            if not frame_exists(swap_file):
                raise ApiError("File not found on disk: " + swap_file)
            current = shot.get("output_file") or ""
            history.remove(swap_file)
            if current:
                history.append(current)
            shot["output_file"] = swap_file
            shot["version_history"] = json.dumps(history)
            cleaned = clean_version_history(shot)
            write_csv(rows, fieldnames)
        self._json({"ok": True, "file": thumb_file, "current": swap_file, "history": cleaned})

    def api_generate_ref(self, data):
        verbatim = (data.get("verbatim_instructions") or "").strip()
        if not verbatim:
            raise ApiError("Missing verbatim_instructions")
        # Slashes are allowed so sub-categories (locations/cabin) keep their
        # directory structure; the filename uses only the last segment.
        category = re.sub(r"[^A-Za-z0-9_\-/]", "_",
                          (data.get("category") or "misc").strip()).strip("/")
        category = "/".join(seg for seg in category.split("/") if seg)
        category = category or "misc"
        cat_leaf = category.split("/")[-1]
        key = require_openai_key()
        quality = requested_quality(data)
        prompt = verbatim + ". " + HOUSE_STYLE
        count = max(1, min(9, int(data.get("count") or 1)))
        files = []
        stamp = time.strftime("%Y%m%d_%H%M%S")
        cat_dir = os.path.join(refs_dir(), category)
        os.makedirs(cat_dir, exist_ok=True)
        for n in range(count):
            img_bytes = openai_generate_image(prompt, key, quality)
            fname = "ref_%s_%s_%d.png" % (cat_leaf, stamp, n + 1)
            with open(os.path.join(cat_dir, fname), "wb") as of:
                of.write(img_bytes)
            files.append(category + "/" + fname)
        self._json({"ok": True, "file": thumb_file, "files": files, "file": files[0],
                    "category": category, "size_kb": len(img_bytes) // 1024})

    # === REFERENCE FILE ENDPOINTS (archive / bin / move — never delete) ===
    @staticmethod
    def _strip_archive_prefix(rel):
        if rel.startswith(REF_BIN + "/"):
            return rel[len(REF_BIN) + 1:]
        if rel.startswith(REF_ARCHIVE + "/"):
            return rel[len(REF_ARCHIVE) + 1:]
        return rel

    def _move_ref(self, rel, dest_rel):
        """Move a ref inside the refs dir, uniquifying on collision.
        SIDE EFFECTS: renames a file on disk (never copies, never deletes).
        Returns the possibly-uniquified dest rel path — callers must report
        THAT to the client, not the requested one."""
        rd = refs_dir()
        filepath = safe_path(rd, rel)
        if not filepath:
            raise ApiError("Reference not found: " + rel, 404)
        dest = os.path.join(rd, dest_rel)
        if os.path.abspath(dest) == os.path.abspath(filepath):
            return rel
        os.makedirs(os.path.dirname(dest) or rd, exist_ok=True)
        if os.path.exists(dest):
            base, ext = os.path.splitext(dest_rel)
            n = 2
            while os.path.exists(os.path.join(rd, "%s_%d%s" % (base, n, ext))):
                n += 1
            dest_rel = "%s_%d%s" % (base, n, ext)
            dest = os.path.join(rd, dest_rel)
        os.replace(filepath, dest)
        return dest_rel

    def api_ref_archive(self, data):
        rel = (data.get("file") or "").strip()
        orig = self._strip_archive_prefix(rel)
        moved = self._move_ref(rel, REF_ARCHIVE + "/" + orig)
        self._json({"ok": True, "file": thumb_file, "file": moved})

    def api_ref_bin(self, data):
        """Move a reference to the bin (a subfolder of the archive). This
        replaces deletion — the file stays on disk forever."""
        rel = (data.get("file") or "").strip()
        orig = self._strip_archive_prefix(rel)
        moved = self._move_ref(rel, REF_BIN + "/" + orig)
        self._json({"ok": True, "file": thumb_file, "file": moved})

    api_ref_delete = api_ref_bin  # legacy URL — now bins instead of deleting

    def api_ref_restore(self, data):
        rel = (data.get("file") or "").strip()
        orig = self._strip_archive_prefix(rel)
        if orig == rel:
            raise ApiError("Reference is not archived: " + rel)
        moved = self._move_ref(rel, orig)
        self._json({"ok": True, "file": thumb_file, "file": moved})

    def api_ref_move(self, data):
        rel = (data.get("file") or "").strip()
        category = re.sub(r"[^A-Za-z0-9_\-/]", "_",
                          (data.get("category") or "").strip()).strip("/")
        if not category:
            raise ApiError("Missing category")
        if category == REF_ARCHIVE or category.startswith(REF_ARCHIVE + "/"):
            raise ApiError("Use archive/bin actions instead of moving into "
                           + REF_ARCHIVE)
        filepath = safe_path(refs_dir(), rel)
        if not filepath:
            raise ApiError("Reference not found: " + rel, 404)
        dest_rel = category + "/" + os.path.basename(filepath)
        dest = os.path.join(refs_dir(), dest_rel)
        if os.path.abspath(dest) == os.path.abspath(filepath):
            return self._json({"ok": True, "file": thumb_file, "file": rel})
        if os.path.exists(dest):
            raise ApiError("A file with that name already exists in " + category, 409)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(filepath, dest)
        cats = known_categories()
        if category not in cats:
            save_categories(cats + [category])
        self._json({"ok": True, "file": thumb_file, "file": dest_rel})

    def api_category_add(self, data):
        category = re.sub(r"[^A-Za-z0-9_\-/]", "_",
                          (data.get("category") or "").strip()).strip("/")
        if not category:
            raise ApiError("Missing category")
        cats = known_categories()
        if category not in cats:
            save_categories(cats + [category])
        self._json({"ok": True, "file": thumb_file, "category": category})


    def api_ref_edit(self, data):
        rel = (data.get("file") or "").strip()
        instructions = (data.get("edit_instructions") or "").strip()
        if not instructions:
            raise ApiError("Missing edit_instructions")
        filepath = safe_path(refs_dir(), rel)
        if not filepath:
            raise ApiError("Reference not found: " + rel, 404)
        count = max(1, min(9, int(data.get("count") or 1)))
        quality = requested_quality(data)
        key = require_openai_key()
        with open(filepath, "rb") as f:
            source_bytes = f.read()
        prompt = instructions + ". " + EDIT_SUFFIX
        rel_dir = os.path.dirname(rel)
        stem = os.path.basename(filepath).rsplit(".", 1)[0]
        stamp = time.strftime("%Y%m%d_%H%M%S")
        files = []
        for n in range(count):
            img_bytes = openai_edit_image(source_bytes, prompt, key, quality)
            fname = "%s_edit_%s_%d.png" % (stem, stamp, n + 1)
            with open(os.path.join(os.path.dirname(filepath), fname), "wb") as of:
                of.write(img_bytes)
            files.append((rel_dir + "/" if rel_dir else "") + fname)
        self._json({"ok": True, "file": thumb_file, "files": files, "file": files[0]})

    def api_describe_ref(self, data):
        """Reference image -> textual description via GPT-4o vision. The
        result is returned for the client to copy or save onto a hero
        asset's canonical description."""
        rel = (data.get("file") or "").strip()
        if not rel:
            raise ApiError("Missing file")
        filepath = safe_path(refs_dir(), rel)
        if not filepath:
            raise ApiError("Reference not found: " + rel, 404)
        key = require_openai_key()
        with open(filepath, "rb") as f:
            img_bytes = f.read()
        ext = os.path.splitext(filepath)[1].lower()
        mime = CT.get(ext.lstrip(".")) if ext in ALLOWED_IMAGE_EXTS else None
        description = openai_describe_image(img_bytes, mime or "image/png",
                                            key)
        self._json({"ok": True, "file": thumb_file, "file": rel, "description": description,
                    "model": VISION_MODEL})

    # === SHOT DUPLICATION ===
    def api_duplicate(self, data):
        """Copy a shot row under a new unique output_file; the frame file
        (if any) is copied too so the duplicate previews immediately."""
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, row = resolve_row(rows, data)
            new_row = {f: row.get(f, "") or "" for f in fieldnames}
            taken = {(r.get("output_file") or "").strip() for r in rows}
            of = (row.get("output_file") or "").strip()
            if of:
                base, ext = os.path.splitext(of)
                new_of = "%s_copy%s" % (base, ext)
                n = 2
                while new_of in taken:
                    new_of = "%s_copy%d%s" % (base, n, ext)
                    n += 1
            else:
                new_of = "%s_dup_%d.png" % (file_prefix(), int(time.time()))
            new_row["output_file"] = new_of
            new_row["version_history"] = "[]"
            new_row["shotlist_ref"] = ""
            new_row["shot_number"] = row.get("shot_number", "1")
            src = None
            fd = frames_dir()
            if of:
                src = os.path.join(fd, of)
                if not os.path.isfile(src):
                    src = find_in_tree(fd, os.path.basename(of))
            if src and os.path.isfile(src):
                os.makedirs(fd, exist_ok=True)
                try:
                    shutil.copy2(src, os.path.join(fd, new_of))
                except OSError:
                    new_row["status"] = "pending"
            else:
                new_row["status"] = "pending"
            rows.insert(idx + 1, new_row)
            write_csv(rows, fieldnames)
        self._json({"ok": True, "file": thumb_file, "row": new_row})

    # === HERO MASS REGENERATION (background job) ===
    def api_mass_regen(self, data):
        """Queue image-to-image edits for every shot tagged with a hero, so
        an updated hero description propagates across the board. Runs in a
        background thread; poll /api/job_status?id=<job_id>."""
        hero_id = (data.get("hero_id") or "").strip()
        if not hero_id:
            raise ApiError("Missing hero_id")
        hero = hero_by_id(load_heroes(), hero_id)
        if hero is None:
            raise ApiError("Unknown hero: " + hero_id, 404)
        with CSV_LOCK:
            rows, _ = read_csv()
            targets = []
            for r in rows:
                if r.get("status") == "archived":
                    continue
                tags = shot_hero_tags(r)
                if hero_id not in tags and (hero.get("name") or "") not in tags:
                    continue
                of = (r.get("output_file") or "").strip()
                if of and frame_exists(of):
                    targets.append(of)
        if not targets:
            raise ApiError("No shots with rendered frames are tagged with "
                           + (hero.get("name") or hero_id), 404)
        key = require_openai_key()
        prompt = hero_regen_prompt(hero)
        job_id = new_job("mass_regen", len(targets))
        req_ctx = ctx()  # worker keeps writing into THIS project's dirs

        def worker():
            set_ctx(req_ctx)
            fd = frames_dir()
            for of in targets:
                try:
                    src = os.path.join(fd, of)
                    if not os.path.isfile(src):
                        src = find_in_tree(fd, os.path.basename(of))
                    if not src:
                        raise ApiError("Source frame missing: " + of)
                    with open(src, "rb") as f:
                        source_bytes = f.read()
                    img_bytes = openai_edit_image(source_bytes, prompt, key)
                    new_file, _, _ = _commit_edit(of, img_bytes, prompt)
                    job_step(job_id, file=new_file)
                except Exception as e:
                    job_step(job_id, error="%s: %s" % (of, e))
            job_finish(job_id)

        threading.Thread(target=worker, daemon=True).start()
        self._json({"ok": True, "file": thumb_file, "job_id": job_id, "count": len(targets),
                    "hero": hero.get("name", hero_id)})

    # GOTCHA: this staticmethod and everything below it MUST stay indented
    # inside Handler. A past manual edit left a column-0 comment here that
    # made the region look dedented — if a method actually IS dedented, the
    # POST_ROUTES table at the bottom of the file fails at import.
    @staticmethod
    def create_shot_row(data, fieldnames):
        """Build a new CSV row dict with defaults for all fieldnames.
        Callers pass the fieldnames from their own locked read_csv() so this
        never reads the CSV outside CSV_LOCK."""
        row = {f: "" for f in fieldnames}
        row.update(data)
        row.setdefault("status", "pending")
        row.setdefault("generation_method", "generate")
        return row

    # === GENERATE FROM HERO ===
    def api_generate_from_hero(self, data):
        # CONTRACT: body = {hero_id, prompt, quality?}. Creates BOTH a new
        # shots-CSV row (scene_number="ref") AND a stable hero_<id>.png copy
        # in the reference tree recorded as the hero's thumb_file. The
        # client loops for multi-generate.
        hero_id = (data.get("hero_id") or "").strip()
        prompt = (data.get("prompt") or "").strip()
        if not hero_id:
            raise ApiError("Missing hero_id")
        if not prompt:
            raise ApiError("Missing prompt")
        hero = hero_by_id(load_heroes(), hero_id)
        if hero is None:
            raise ApiError("Unknown hero: " + hero_id, 404)
        key = require_openai_key()
        quality = requested_quality(data)
        final_prompt = prompt + ". " + HOUSE_STYLE if data.get("apply_style") else prompt
        img_bytes = openai_generate_image(final_prompt, key, quality)
        # Save directly to the reference tree — no storyboard row. This is for
        # generating reference images, not storyboards. The image lands in the
        # hero's reference category and becomes the hero card thumbnail.
        thumb_file = None
        try:
            cat = hero_ref_category(hero)
            cat_dir = os.path.join(refs_dir(), cat)
            os.makedirs(cat_dir, exist_ok=True)
            ref_name = "hero_%s_%s.png" % (_slug(hero_id) or "asset", time.strftime("%Y%m%d_%H%M%S"))
            with open(os.path.join(cat_dir, ref_name), "wb") as rf:
                rf.write(img_bytes)
            thumb_file = cat + "/" + ref_name
            with HEROES_LOCK:
                heroes = load_heroes()
                h = hero_by_id(heroes, hero_id)
                if h is not None:
                    h["thumb_file"] = thumb_file
                    h["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    save_heroes(heroes)
        except OSError as e:
            print("Warning: could not save hero thumbnail: %s" % e)
        self._json({"ok": True, "file": thumb_file,
                    "thumb_file": thumb_file})

    # === REFERENCE VARIATIONS ===
    def api_ref_variations(self, data):
        ref_file = (data.get("file") or "").strip()
        prompt = (data.get("prompt") or "").strip()
        quality = data.get("quality", "").strip()
        count = min(9, max(1, int(data.get("count", 1) or 1)))
        if not ref_file:
            raise ApiError("Missing file parameter")
        if not prompt:
            raise ApiError("No prompt — enter one manually")
        key = require_openai_key()
        q = quality if quality in QUALITY_LEVELS else active_quality()
        cat = os.path.dirname(ref_file) or "misc"
        base_name = os.path.splitext(os.path.basename(ref_file))[0]
        out_dir = os.path.join(refs_dir(), cat)
        os.makedirs(out_dir, exist_ok=True)
        saved = []
        ts = str(int(time.time()))
        for i in range(count):
            img_bytes = openai_generate_image(prompt, key, q)
            out_name = f"{base_name}_var_{ts}_{i+1}.png"
            out_path = os.path.join(out_dir, out_name)
            with open(out_path, "wb") as of:
                of.write(img_bytes)
            rel = (cat + "/" + out_name) if cat not in ("", ".") else out_name
            saved.append(rel)
        self._json({"ok": True, "file": thumb_file, "files": saved, "category": cat, "count": len(saved)})

    # === SHOTLIST ENDPOINTS (24-column VFX breakdown) ===
    def api_shotlist_create(self, data):
        with SHOTLIST_LOCK:
            rows = read_shotlist()
            row = {c: "" for c in SHOTLIST_COLUMNS}
            for c in SHOTLIST_COLUMNS:
                if c in data:
                    row[c] = coerce_shotlist_value(c, data[c])
            if not row["Order"]:
                row["Order"] = "%g" % next_shotlist_order(rows)
            if not row["Shot Count"]:
                row["Shot Count"] = "1"
            rows.append(row)
            sort_shotlist(rows)
            write_shotlist(rows)
        self._json({"ok": True, "file": thumb_file, "row": row})

    def api_shotlist_update(self, data):
        """Update one cell. Rows are addressed by row_index into the
        Order-sorted list (the same order GET /api/shotlist returns); the
        row's Order value is sent along as a staleness check. Duplicate
        Order values can no longer hit the wrong row the way the old
        first-float-match did."""
        field = data.get("field")
        if field not in SHOTLIST_COLUMNS:
            raise ApiError("Unknown shotlist column: %r" % field)
        value = coerce_shotlist_value(field, data.get("value"))
        idx = data.get("row_index")
        order = str(data.get("Order") if data.get("Order") is not None
                    else "").strip()
        with SHOTLIST_LOCK:
            rows = read_shotlist()
            sort_shotlist(rows)
            target = None
            if isinstance(idx, int) and not isinstance(idx, bool):
                if idx < 0 or idx >= len(rows):
                    raise ApiError("Shotlist row index out of range", 404)
                target = rows[idx]
                if order and (target.get("Order") or "").strip() != order:
                    raise ApiError("Shotlist changed on disk — refresh and "
                                   "retry", 409)
            elif order:
                # legacy fallback: exact Order string match, unique only
                matches = [r for r in rows
                           if (r.get("Order") or "").strip() == order]
                if len(matches) > 1:
                    raise ApiError("Multiple rows share Order %s — update "
                                   "by row_index" % order, 409)
                target = matches[0] if matches else None
            else:
                raise ApiError("Missing row_index")
            if target is None:
                raise ApiError("Shotlist row not found", 404)
            target[field] = value
            sort_shotlist(rows)
            write_shotlist(rows)
        self._json({"ok": True, "file": thumb_file, "row": target})

    def api_shotlist_delete(self, data):
        """Delete one row, addressed by row_index into the Order-sorted
        list (the same scheme as /api/shotlist_update); the row's Order
        value may ride along as a staleness check."""
        idx = data.get("row_index")
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise ApiError("Missing row_index")
        order = str(data.get("Order") if data.get("Order") is not None
                    else "").strip()
        with SHOTLIST_LOCK:
            rows = read_shotlist()
            sort_shotlist(rows)
            if idx < 0 or idx >= len(rows):
                raise ApiError("Shotlist row index out of range", 404)
            if order and (rows[idx].get("Order") or "").strip() != order:
                raise ApiError("Shotlist changed on disk — refresh and "
                               "retry", 409)
            rows.pop(idx)
            write_shotlist(rows)
        self._json({"ok": True})

    def api_shotlist_delete_bulk(self, data):
        """Delete several rows at once, addressed by row_index into the
        Order-sorted list (the same scheme as /api/shotlist_delete).
        Indices are removed highest-first so earlier pops don't shift
        the remaining targets."""
        idxs = data.get("row_indices")
        if not isinstance(idxs, list) or not idxs:
            raise ApiError("Missing row_indices")
        for i in idxs:
            if not isinstance(i, int) or isinstance(i, bool):
                raise ApiError("row_indices must be integers")
        with SHOTLIST_LOCK:
            rows = read_shotlist()
            sort_shotlist(rows)
            uniq = sorted(set(idxs), reverse=True)
            if uniq[-1] < 0 or uniq[0] >= len(rows):
                raise ApiError("Shotlist row index out of range", 404)
            for i in uniq:
                rows.pop(i)
            write_shotlist(rows)
        self._json({"ok": True, "file": thumb_file, "deleted": len(uniq)})

    def api_shotlist_sync(self, data):
        """Create a shotlist row for every Shots-tab shot that doesn't have
        one yet (matched on Setup == output_file)."""
        with CSV_LOCK:
            shots, _ = read_csv()
        created, refmap = 0, {}
        with SHOTLIST_LOCK:
            rows = read_shotlist()
            setups = {(r.get("Setup") or "").strip() for r in rows}
            order = next_shotlist_order(rows)
            for s in shots:
                if s.get("status") == "archived":
                    continue
                of = (s.get("output_file") or "").strip()
                if not of or of in setups:
                    continue
                rows.append(shot_to_shotlist_row(s, order))
                setups.add(of)
                refmap[of] = "%g" % order
                order += 1
                created += 1
            if created:
                sort_shotlist(rows)
                write_shotlist(rows)
        # Back-link the shots to their shotlist rows (separate lock scope —
        # never hold CSV_LOCK and SHOTLIST_LOCK at once).
        if refmap:
            with CSV_LOCK:
                rows2, fn2 = read_csv()
                changed = False
                for r in rows2:
                    of = (r.get("output_file") or "").strip()
                    if of in refmap and r.get("shotlist_ref") != refmap[of]:
                        r["shotlist_ref"] = refmap[of]
                        changed = True
                if changed:
                    write_csv(rows2, fn2)
        self._json({"ok": True, "file": thumb_file, "created": created, "updated": 0})

    def api_shotlist_import(self, data):
        """Import CSV text into the shotlist. The CSV rides in the JSON
        body as {"csv": "..."} (the CSRF guard requires application/json).
        ?mode=replace clears the shotlist first; ?mode=merge (default)
        upserts by Order — matching rows are updated column-by-column,
        unmatched rows are appended. Unknown CSV columns are ignored."""
        text = data.get("csv")
        if not isinstance(text, str) or not text.strip():
            raise ApiError("Missing csv text")
        mode = (self.post_query.get("mode") or [data.get("mode") or "merge"])[0]
        mode = (mode or "merge").strip().lower()
        if mode not in ("merge", "replace"):
            raise ApiError("mode must be 'merge' or 'replace'")

        reader = csv.DictReader(io.StringIO(text))
        header = [h.strip() for h in (reader.fieldnames or []) if h]
        known = [h for h in header if h in SHOTLIST_COLUMNS]
        if not known:
            raise ApiError("CSV has no recognized shotlist columns — "
                           "expected headers like: "
                           + ", ".join(SHOTLIST_COLUMNS[:6]) + ", ...")

        def order_key(v):
            v = str(v or "").strip()
            try:
                return "%g" % float(v)
            except ValueError:
                return v

        imported = []
        for n, raw in enumerate(reader, start=2):  # 1 = header line
            raw.pop(None, None)
            row = {c: "" for c in SHOTLIST_COLUMNS}
            for h in known:
                try:
                    row[h] = coerce_shotlist_value(h, raw.get(h))
                except ApiError as e:
                    raise ApiError("CSV line %d: %s" % (n, e))
            if any(row.values()):
                imported.append(row)
        if not imported:
            raise ApiError("CSV contained no data rows")

        created = updated = 0
        with SHOTLIST_LOCK:
            rows = [] if mode == "replace" else read_shotlist()
            by_order = {}
            for r in rows:
                k = order_key(r.get("Order"))
                if k and k not in by_order:
                    by_order[k] = r
            next_order = next_shotlist_order(rows)
            for row in imported:
                k = order_key(row.get("Order"))
                target = by_order.get(k) if k else None
                if target is not None:
                    for h in known:
                        target[h] = row[h]
                    updated += 1
                else:
                    if not row["Order"]:
                        row["Order"] = "%g" % next_order
                        next_order += 1
                    if not row["Shot Count"]:
                        row["Shot Count"] = "1"
                    rows.append(row)
                    if k or row["Order"]:
                        by_order.setdefault(order_key(row["Order"]), row)
                    created += 1
            sort_shotlist(rows)
            write_shotlist(rows)
        self._json({"ok": True, "file": thumb_file, "mode": mode, "created": created,
                    "updated": updated, "total": len(rows)})

    # === HERO ASSET ENDPOINTS ===
    HERO_FIELDS = ("name", "type", "category", "description", "breakdown",
                   "colors", "notes", "ref_image", "thumb_file")

    def api_hero_create(self, data):
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError("Missing name")
        with HEROES_LOCK:
            heroes = load_heroes()
            top = 0
            for h in heroes:
                m = re.match(r"hero_(\d+)$", h.get("id") or "")
                if m:
                    top = max(top, int(m.group(1)))
            hero = {"id": "hero_%d" % (top + 1), "archived": False,
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
            for f in self.HERO_FIELDS:
                hero[f] = str(data.get(f, "") or "").strip()
            hero["name"] = name
            if not hero["type"]:
                hero["type"] = "prop"
            heroes.append(hero)
            save_heroes(heroes)
        self._json({"ok": True, "file": thumb_file, "hero": hero})

    def api_hero_update(self, data):
        hero_id = (data.get("id") or "").strip()
        field = data.get("field")
        if field != "archived" and field not in self.HERO_FIELDS:
            raise ApiError("Unknown hero field: %r" % field)
        with HEROES_LOCK:
            heroes = load_heroes()
            hero = hero_by_id(heroes, hero_id)
            if hero is None:
                raise ApiError("Unknown hero: " + hero_id, 404)
            if field == "archived":
                hero["archived"] = bool(data.get("value"))
            else:
                hero[field] = str(data.get("value", "") or "").strip()
            hero["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            save_heroes(heroes)
        self._json({"ok": True, "file": thumb_file, "hero": hero})

    def api_hero_delete(self, data):
        """Archive, never delete — the hero stays in hero_assets.json and
        can be restored by flipping archived back off."""
        hero_id = (data.get("id") or "").strip()
        with HEROES_LOCK:
            heroes = load_heroes()
            hero = hero_by_id(heroes, hero_id)
            if hero is None:
                raise ApiError("Unknown hero: " + hero_id, 404)
            hero["archived"] = not data.get("restore", False)
            hero["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            save_heroes(heroes)
        self._json({"ok": True, "file": thumb_file, "hero": hero})

    # === PROJECT ENDPOINTS ===
    def api_project_create(self, data):
        name = _slug((data.get("name") or "").strip().lower()
                     .replace(" ", "-"))
        if not name:
            raise ApiError("Project name must contain letters or digits")
        if os.path.isfile(settings_path(name)):
            raise ApiError("Project already exists: " + name, 409)
        save_settings(default_settings(name))
        load_project(name)
        self._json({"ok": True, "file": thumb_file, "name": name, "projects": list_projects()})

    def api_project_switch(self, data):
        name = (data.get("name") or "").strip()
        if not name:
            raise ApiError("Missing name")
        settings = load_project(name)
        self._json({"ok": True, "file": thumb_file, "name": name,
                    "settings": {k: settings.get(k, "") for k in
                                 ("quality", "model", "file_prefix",
                                  "canon_dir")}})

    def api_project_update(self, data):
        """Persist one project setting (quality / model / file_prefix) —
        backs the toolbar selectors. canon_dir is deliberately NOT writable
        here: it points the server at arbitrary directories to read, so it
        can only be set via the --canon-dir CLI flag."""
        field = data.get("field")
        value = str(data.get("value", "") or "").strip()
        if field not in ("quality", "model", "file_prefix"):
            raise ApiError("Unknown project setting: %r" % field)
        if field == "quality" and value not in QUALITY_LEVELS:
            raise ApiError("Quality must be one of: "
                           + ", ".join(QUALITY_LEVELS))
        if field == "model" and value not in IMAGE_MODELS:
            raise ApiError("Unknown model: " + value)
        with PROJECT_LOCK:
            if not PROJECT.get("name"):
                raise ApiError("No project loaded — restart or switch to a "
                               "project first", 500)
            PROJECT[field] = value
            settings = dict(PROJECT)
        save_settings(settings)
        self._json({"ok": True, "file": thumb_file,
                    "settings": {k: settings.get(k, "") for k in
                                 ("quality", "model", "file_prefix",
                                  "canon_dir")}})


Handler.POST_ROUTES = {
    "/api/reorder": Handler.api_reorder,
    "/api/reorder_all": Handler.api_reorder_all,
    "/api/create": Handler.api_create,
    "/api/update": Handler.api_update,
    "/api/edit_text": Handler.api_edit_text,
    "/api/archive": Handler.api_archive,
    "/api/unarchive": Handler.api_unarchive,
    "/api/generate": Handler.api_generate,
    "/api/edit": Handler.api_edit,
    "/api/swap_version": Handler.api_swap_version,
    "/api/generate_ref": Handler.api_generate_ref,
    "/api/ref_delete": Handler.api_ref_delete,
    "/api/ref_archive": Handler.api_ref_archive,
    "/api/ref_bin": Handler.api_ref_bin,
    "/api/ref_restore": Handler.api_ref_restore,
    "/api/ref_move": Handler.api_ref_move,
    "/api/ref_edit": Handler.api_ref_edit,
    "/api/ref_variations": Handler.api_ref_variations,
    "/api/describe_ref": Handler.api_describe_ref,
    "/api/category_add": Handler.api_category_add,
    "/api/duplicate": Handler.api_duplicate,
    "/api/mass_regen": Handler.api_mass_regen,
    "/api/generate_from_hero": Handler.api_generate_from_hero,
    "/api/shotlist_create": Handler.api_shotlist_create,
    "/api/shotlist_update": Handler.api_shotlist_update,
    "/api/shotlist_delete": Handler.api_shotlist_delete,
    "/api/shotlist_delete_bulk": Handler.api_shotlist_delete_bulk,
    "/api/shotlist_sync": Handler.api_shotlist_sync,
    "/api/shotlist_import": Handler.api_shotlist_import,
    "/api/hero_create": Handler.api_hero_create,
    "/api/hero_update": Handler.api_hero_update,
    "/api/hero_delete": Handler.api_hero_delete,
    "/api/project_create": Handler.api_project_create,
    "/api/project_switch": Handler.api_project_switch,
    "/api/project_update": Handler.api_project_update,
}


# -- Main ------------------------------------------------------------------
def main():
    global CLI_BOUND_PROJECT
    parse_args()
    _load_scene_text()
    ensure_default_project()
    CLI_BOUND_PROJECT = CLI_PROJECT or load_active_name()
    try:
        load_project(CLI_BOUND_PROJECT)
    except ApiError as e:
        print("Warning: %s — using legacy default paths" % e)
    start_backup_thread()
    print("Shot Dash v2")
    print("   Project:  %s" % PROJECT.get("name", "?"))
    print("   CSV:      %s%s" % (CSV_PATH, "" if os.path.exists(CSV_PATH) else "  (missing — starting empty)"))
    print("   Frames:   %s" % FRAMES_DIR)
    print("   Refs:     %s" % REFS_DIR)
    if not get_openai_key():
        print("   WARNING:  no OpenAI key found — generate/edit will fail")
    if BIND_HOST != "127.0.0.1":
        print("   PUBLIC:   listening on %s — reachable from the network" % BIND_HOST)
    print("   -> http://localhost:%d" % PORT)
    print("   Ctrl+C to stop\n")
    server = ThreadingHTTPServer((BIND_HOST, PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
