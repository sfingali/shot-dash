#!/usr/bin/env python3
"""Shot Dash — local storyboard review dashboard for THE WAIF.

Serves a CSV shot list as a filterable grid, inline frame previews, and a
reference image browser. Zero dependencies beyond the Python stdlib.

Usage:
    python3 shot_dash.py [--port 8090] [--frames-dir /path] [--refs-dir /path]
                         [--csv /path] [--env /path/to/.env]

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
"""

import base64
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# -- Config ----------------------------------------------------------------
PORT = 8090
CSV_PATH = "/opt/data/home/projects/the-waif/storyboard_shots.csv"
FRAMES_DIR = "/opt/data/home/projects/the-waif/storyboards_gpt"
REFS_DIR = "/opt/data/home/projects/the-waif/storyboard_reference"
ENV_PATH = "/opt/data/profiles/heavy/.env"

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

HOUSE_STYLE = ("Desaturated palette, cool shadows. Photorealistic cinematic "
               "still from an indie horror film. Scope 2.39:1. No text, no "
               "watermark, no logos.")

EDIT_SUFFIX = (". Keep everything else the same: composition, lighting, "
               "palette, mood. Photorealistic cinematic still from an indie "
               "horror film. No text, no watermark, no logos.")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(SCRIPT_DIR, "index.html")

CSV_LOCK = threading.Lock()


def parse_args():
    global PORT, CSV_PATH, FRAMES_DIR, REFS_DIR, ENV_PATH
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        flag = args[i]
        has_val = i + 1 < len(args)
        if flag == "--port" and has_val:
            i += 1; PORT = int(args[i])
        elif flag == "--csv" and has_val:
            i += 1; CSV_PATH = args[i]
        elif flag == "--frames-dir" and has_val:
            i += 1; FRAMES_DIR = args[i]
        elif flag == "--refs-dir" and has_val:
            i += 1; REFS_DIR = args[i]
        elif flag == "--env" and has_val:
            i += 1; ENV_PATH = args[i]
        i += 1


# -- Errors ----------------------------------------------------------------
class ApiError(Exception):
    """Raise anywhere in a handler to return a JSON error with a status."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# -- CSV layer -------------------------------------------------------------
CSV_COLUMNS = [
    "scene_number", "shot_number", "generation_number", "verbatim_instructions",
    "lens", "aspect_ratio", "quality", "curated_description",
    "fountain_description", "fountain_text", "iteration_history",
    "characters", "location", "generation_method", "iteration_count",
    "source_frame", "estimated_cost", "prompt", "output_file", "status",
    "endpoint", "version_history",
]


def read_csv():
    """Read rows + fieldnames. Missing columns are added in memory only and
    persisted on the next write (the old version rewrote the CSV on every
    GET when migrating, which is wasteful and racy)."""
    if not os.path.exists(CSV_PATH):
        return [], list(CSV_COLUMNS)
    with open(CSV_PATH, "r", newline="") as f:
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


def write_csv(rows, fieldnames):
    """Atomic write with timestamped backup. Prunes backups older than 7
    days, keeps at most 50."""
    csv_dir = os.path.dirname(os.path.abspath(CSV_PATH))
    backup_dir = os.path.join(csv_dir, ".csv_backups")
    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, "storyboard_shots.%s.csv" % ts)
        with open(backup_path, "w", newline="") as bf:
            bw = csv.DictWriter(bf, fieldnames=fieldnames, extrasaction="ignore")
            bw.writeheader()
            bw.writerows(rows)
        now = time.time()
        for b in sorted(os.listdir(backup_dir)):
            fp = os.path.join(backup_dir, b)
            if now - os.path.getmtime(fp) > 7 * 86400:
                os.remove(fp)
        backups = sorted(os.listdir(backup_dir))
        if len(backups) > 50:
            for old in backups[:-50]:
                os.remove(os.path.join(backup_dir, old))
    except OSError:
        pass  # a backup failure must never block the main write

    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, CSV_PATH)


def find_row_by_file(rows, output_file):
    """Return (row_index, row) or (None, None)."""
    target = (output_file or "").strip()
    for i, r in enumerate(rows):
        if (r.get("output_file") or "").strip() == target:
            return i, r
    return None, None


def resolve_row(rows, data):
    """Locate a shot by output_file (preferred — stable across client-side
    sorting) or row_index (CSV order). Raises ApiError if not found."""
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
    """Relative POSIX-style paths of all images under directory."""
    images = []
    base = Path(directory)
    if not base.exists():
        return images
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS:
            images.append(p.relative_to(base).as_posix())
    return images


def refs_by_category():
    """Group reference images by their first path segment. Files sitting
    directly in REFS_DIR go under 'uncategorized'."""
    cats = {}
    for rel in list_images(REFS_DIR):
        parts = rel.split("/", 1)
        cat = parts[0] if len(parts) > 1 else "uncategorized"
        cats.setdefault(cat, []).append(rel)
    return cats


def find_in_tree(base_dir, filename):
    """Search recursively for a file by basename in base_dir."""
    name_lower = (filename or "").lower()
    if not name_lower or not os.path.isdir(base_dir):
        return None
    for p in Path(base_dir).rglob("*"):
        if p.is_file() and p.name.lower() == name_lower:
            return str(p)
    for p in Path(base_dir).rglob("*"):
        if p.is_file() and name_lower in p.name.lower():
            return str(p)
    return None


def frame_exists(filename):
    fn = (filename or "").strip()
    if not fn:
        return False
    if os.path.exists(os.path.join(FRAMES_DIR, fn)):
        return True
    return find_in_tree(FRAMES_DIR, os.path.basename(fn)) is not None


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
    key = get_openai_key()
    if not key:
        raise ApiError("OpenAI API key not found — set OPENAI_API_KEY or "
                       "point --env at a file containing OPENAI_KEY=...", 500)
    return key


def _openai_call(req, timeout):
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode(errors="replace")[:300]
        except Exception:
            pass
        raise ApiError("GPT Image 2 error (HTTP %d): %s" % (e.code, detail), 400)
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


def openai_generate_image(prompt, key):
    body = json.dumps({
        "model": "gpt-image-2", "prompt": prompt, "n": 1,
        "size": "2560x1072", "quality": "medium",
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations", data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    return _extract_image_bytes(_openai_call(req, 180))


def openai_edit_image(source_bytes, prompt, key):
    boundary = "----Boundary" + os.urandom(16).hex()

    def part(lines):
        return "\r\n".join(lines).encode() + b"\r\n"

    body = b""
    body += part(["--" + boundary,
                  'Content-Disposition: form-data; name="image"; filename="source.png"',
                  "Content-Type: image/png", ""])
    body += source_bytes + b"\r\n"
    for name, value in (("prompt", prompt), ("model", "gpt-image-2"),
                        ("size", "2560x1072"), ("quality", "medium"), ("n", "1")):
        body += part(["--" + boundary,
                      'Content-Disposition: form-data; name="%s"' % name,
                      "", value])
    body += ("--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/edits", data=body,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "multipart/form-data; boundary=" + boundary})
    return _extract_image_bytes(_openai_call(req, 180))


# -- Versioning ------------------------------------------------------------
def next_version_name(old_file, scene_number):
    """Return (new_output_file, version_number) for a shot's next render."""
    if old_file:
        m = re.search(r"_v(\d+)", old_file)
        ext = os.path.splitext(old_file)[1] or ".png"
        if m:
            return "%s_v%d%s" % (old_file[:m.start()], int(m.group(1)) + 1, ext), int(m.group(1)) + 1
        return "%s_v2%s" % (os.path.splitext(old_file)[0], ext), 2
    return "waif_sc_%s_v1.png" % (scene_number or "XX"), 1


def push_version(shot, old_file):
    """Append old_file to the shot's version_history JSON list."""
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
    """Prompt from curated_description + lens/location/house style."""
    if shot.get("prompt"):
        return shot["prompt"]
    curated = (shot.get("curated_description") or "").strip()
    if not curated:
        raise ApiError("Shot needs curation first — fill in the curated "
                       "description, then generate", 400)
    parts = [curated]
    lens = (shot.get("lens") or "28mm").strip()
    if lens:
        parts.append("Shot with " + lens + " lens")
    loc = (shot.get("location") or "").strip()
    if loc:
        parts.append(loc)
    parts.append("Desaturated palette, cool shadows")
    parts.append("Photorealistic cinematic still from an indie horror film")
    ratio = (shot.get("aspect_ratio") or "2.39:1").strip()
    parts.append("Scope " + ratio + ". No text, no watermark, no logos.")
    return ". ".join(parts)


# -- HTTP handler ----------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ShotDash/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # quiet

    # - plumbing -
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

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise ApiError("Bad Content-Length")
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

    # - GET -
    def do_GET(self):
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
            self._json({"ok": True, "time": time.time()})

        elif path == "/favicon.ico":
            self._respond(204, "image/x-icon", b"")

        elif path == "/api/shots":
            with CSV_LOCK:
                rows, _ = read_csv()
            self._json({"shots": rows})

        elif path == "/api/frames":
            images = list_images(FRAMES_DIR)
            frames, frames_lower = {}, {}
            for img in images:
                base = os.path.basename(img)
                frames[base] = img
                frames_lower[base.lower()] = img
            self._json({"frames": frames, "frames_lower": frames_lower,
                        "all": images})

        elif path == "/api/refs":
            cats = refs_by_category()
            flat = [f for imgs in cats.values() for f in imgs]
            self._json({"categories": cats, "images": flat})

        elif path.startswith("/api/frame/"):
            rel = urllib.parse.unquote(path[len("/api/frame/"):])
            filepath = safe_path(FRAMES_DIR, rel)
            if not filepath:
                filepath = find_in_tree(FRAMES_DIR, os.path.basename(rel))
            if filepath:
                self._serve_image(filepath)
            else:
                self._error("Frame not found: " + rel, 404)

        elif path.startswith("/api/ref/"):
            rel = urllib.parse.unquote(path[len("/api/ref/"):])
            filepath = safe_path(REFS_DIR, rel)
            if not filepath:
                filepath = find_in_tree(REFS_DIR, os.path.basename(rel))
            if filepath:
                self._serve_image(filepath)
            else:
                self._error("Reference not found: " + rel, 404)

        else:
            self._error("Not found", 404)

    # - POST -
    POST_ROUTES = {}  # filled in below the class body

    def do_POST(self):
        handler = self.POST_ROUTES.get(self.path)
        if handler is None:
            return self._error("Unknown endpoint: " + self.path, 404)
        try:
            data = self._read_body()
            handler(self, data)
        except ApiError as e:
            self._error(str(e), e.status)
        except Exception as e:
            self._error("Server error: %r" % e, 500)

    # - simple CSV endpoints -
    def api_reorder(self, data):
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
            rows.append(new_row)
            write_csv(rows, fieldnames)
        self._json({"ok": True, "row": new_row})

    def api_update(self, data):
        """Set one field. Accepts {field, value} plus output_file/row_index.
        Also accepts the legacy payload {row_index, status: X}."""
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
        self._json({"ok": True, "previous_status": prev})

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
        self._json({"ok": True, "status": row["status"]})

    # - generation endpoints -
    # The OpenAI call happens OUTSIDE the CSV lock (it can take 3 minutes);
    # the row is re-resolved afterwards in case the CSV changed meanwhile.

    def api_generate(self, data):
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, shot = resolve_row(rows, data)
            prompt = build_generation_prompt(shot)
            identity = (shot.get("output_file") or "").strip()
            scene = shot.get("scene_number", "XX")
        key = require_openai_key()
        img_bytes = openai_generate_image(prompt, key)

        with CSV_LOCK:
            rows, fieldnames = read_csv()
            if identity:
                idx, shot = resolve_row(rows, {"output_file": identity})
            else:
                idx, shot = resolve_row(rows, data)
            old_file = (shot.get("output_file") or "").strip()
            output_file, new_v = next_version_name(old_file, scene)
            out_path = os.path.join(FRAMES_DIR, output_file)
            os.makedirs(FRAMES_DIR, exist_ok=True)
            with open(out_path, "wb") as of:
                of.write(img_bytes)
            if old_file:
                push_version(shot, old_file)
            shot["status"] = "generated"
            shot["output_file"] = output_file
            shot["prompt"] = prompt
            shot["aspect_ratio"] = shot.get("aspect_ratio") or "2.39:1"
            shot["quality"] = shot.get("quality") or "medium"
            shot["generation_method"] = shot.get("generation_method") or "generation"
            shot["estimated_cost"] = shot.get("estimated_cost") or "$0.04"
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
        self._json({"ok": True, "output_file": output_file,
                    "size_kb": len(img_bytes) // 1024, "version": new_v,
                    "history": history})

    def api_edit(self, data):
        edit_instructions = (data.get("edit_instructions") or "").strip()
        if not edit_instructions:
            raise ApiError("Missing edit_instructions")
        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, shot = resolve_row(rows, data)
            old_file = (shot.get("output_file") or "").strip()
            if not old_file:
                raise ApiError("Shot has no image to edit")
            source_path = os.path.join(FRAMES_DIR, old_file)
            if not os.path.exists(source_path):
                source_path = find_in_tree(FRAMES_DIR, os.path.basename(old_file))
                if not source_path:
                    raise ApiError("Source image not found: " + old_file)
            with open(source_path, "rb") as sf:
                source_data = sf.read()
        edit_prompt = edit_instructions + EDIT_SUFFIX
        key = require_openai_key()
        img_bytes = openai_edit_image(source_data, edit_prompt, key)

        with CSV_LOCK:
            rows, fieldnames = read_csv()
            idx, shot = resolve_row(rows, {"output_file": old_file})
            output_file, new_v = next_version_name(old_file, shot.get("scene_number", "XX"))
            out_path = os.path.join(FRAMES_DIR, output_file)
            with open(out_path, "wb") as of:
                of.write(img_bytes)
            push_version(shot, old_file)
            shot["output_file"] = output_file
            shot["status"] = "edited"
            shot["generation_method"] = "edit"
            shot["endpoint"] = "/v1/images/edits (multipart/form-data POST)"
            shot["estimated_cost"] = shot.get("estimated_cost") or "$0.04"
            shot["prompt"] = edit_prompt
            shot["source_frame"] = old_file
            shot["iteration_count"] = str(new_v)
            history = clean_version_history(shot)
            write_csv(rows, fieldnames)
        self._json({"ok": True, "output_file": output_file,
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
        self._json({"ok": True, "current": swap_file, "history": cleaned})

    def api_generate_ref(self, data):
        verbatim = (data.get("verbatim_instructions") or "").strip()
        if not verbatim:
            raise ApiError("Missing verbatim_instructions")
        category = re.sub(r"[^A-Za-z0-9_\-]", "_",
                          (data.get("category") or "misc").strip()) or "misc"
        key = require_openai_key()
        prompt = verbatim + ". " + HOUSE_STYLE
        img_bytes = openai_generate_image(prompt, key)
        cat_dir = os.path.join(REFS_DIR, category)
        os.makedirs(cat_dir, exist_ok=True)
        fname = "ref_%s_%s.png" % (category, time.strftime("%Y%m%d_%H%M%S"))
        with open(os.path.join(cat_dir, fname), "wb") as of:
            of.write(img_bytes)
        self._json({"ok": True, "file": category + "/" + fname,
                    "category": category, "size_kb": len(img_bytes) // 1024})

    def api_ref_delete(self, data):
        rel = (data.get("file") or "").strip()
        filepath = safe_path(REFS_DIR, rel)
        if not filepath:
            raise ApiError("Reference not found: " + rel, 404)
        os.remove(filepath)
        self._json({"ok": True})

    def api_ref_move(self, data):
        rel = (data.get("file") or "").strip()
        category = re.sub(r"[^A-Za-z0-9_\-]", "_",
                          (data.get("category") or "").strip())
        if not category:
            raise ApiError("Missing category")
        filepath = safe_path(REFS_DIR, rel)
        if not filepath:
            raise ApiError("Reference not found: " + rel, 404)
        dest_dir = os.path.join(REFS_DIR, category)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(filepath))
        if os.path.abspath(dest) == os.path.abspath(filepath):
            return self._json({"ok": True, "file": rel})
        if os.path.exists(dest):
            raise ApiError("A file with that name already exists in " + category, 409)
        os.replace(filepath, dest)
        self._json({"ok": True,
                    "file": category + "/" + os.path.basename(filepath)})


Handler.POST_ROUTES = {
    "/api/reorder": Handler.api_reorder,
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
    "/api/ref_move": Handler.api_ref_move,
}


# -- Main ------------------------------------------------------------------
def main():
    parse_args()
    _load_scene_text()
    print("Shot Dash v2")
    print("   CSV:      %s%s" % (CSV_PATH, "" if os.path.exists(CSV_PATH) else "  (missing — starting empty)"))
    print("   Frames:   %s" % FRAMES_DIR)
    print("   Refs:     %s" % REFS_DIR)
    if not get_openai_key():
        print("   WARNING:  no OpenAI key found — generate/edit will fail")
    print("   -> http://localhost:%d" % PORT)
    print("   Ctrl+C to stop\n")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
