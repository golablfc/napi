#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import logging
import urllib.parse
import base64

from typing import Dict
from flask import Flask, jsonify, request, Response
from waitress import serve

from napiprojekt_logic import NapiProjektKatalog
from utils import _fmt  # z utils.py – do labeli czasu jeśli zechcesz

# ────────── konfiguracja logów ─────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("stremio_napi.log"), logging.StreamHandler()]
)
log = logging.getLogger("ST‑NAPI")

app = Flask(__name__)
napi = NapiProjektKatalog()

# ────────── pomocnicze parsowanie ID i parametrów ─────────────
def parse_params(decoded_id: str) -> Dict[str, str]:
    if "/" not in decoded_id:
        return {}
    tail = decoded_id.split("/", 1)[1]
    return {k.lower(): v for k, v in (p.split("=", 1) for p in tail.split("&") if "=" in p)}

def hms(secs):
    if not secs:
        return "??:??:??"
    secs = int(secs)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ────────── CORS ──────────────────────────────────────────────
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET,OPTIONS"
    return resp

# ────────── manifest Stremio ─────────────────────────────────
@app.route("/manifest.json")
def manifest():
    return jsonify({
        "id": "org.stremio.napiprojekt.python",
        "version": "6.0.0",
        "name": "NapiProjekt PL · TMDB",
        "description": "Napisy z NapiProjekt (ID wyciągane z katalogu NP, dane PL z TMDB).",
        "resources": ["subtitles"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt"]
    })

# ────────── lista napisów ────────────────────────────────────
@app.route("/subtitles/<ctype>/<path:imdb_plus>.json")
def subtitles_list(ctype: str, imdb_plus: str):
    try:
        decoded = urllib.parse.unquote(imdb_plus)
        imdb_match = re.match(r"^(tt\d{7,8})", decoded)
        if not imdb_match:
            return jsonify({"subtitles": []})
        imdb_id = imdb_match.group(1)

        params = parse_params(decoded)
        item: Dict[str, str] = {
            "imdb_id": imdb_id,
            "season": params.get("season", ""),
            "episode": params.get("episode", "")
        }

        # sezon/odcinek w formacie :Sxx:Eyy może być w path
        se_match = re.search(r":(\d{1,2})(?::(\d{1,2}))?", decoded)
        if se_match and not item["season"]:
            item["season"] = se_match.group(1)
            item["episode"] = se_match.group(2) or ""

        log.info(f"Searching NapiProjekt with: {item}")
        raw = napi.search(item)
        log.info(f"Found {len(raw)} subtitles total")

        # sortowanie i listowanie
        raw.sort(key=lambda s: (-s.get('_downloads', 0), s.get('_duration') or 0))
        subtitles = [{
            "id": f"{imdb_id}_{s['link_hash']}_pl",
            "url": f"{request.url_root}subtitles/download/{s['link_hash']}.srt",
            "lang": f"{hms(s.get('_duration'))} · PL",
            "name": f"NapiProjekt · {s.get('_downloads', 0)}· pobrań · {s.get('_fps') or '?'} FPS"
        } for s in raw]

        return jsonify({"subtitles": subtitles})

    except Exception:
        log.exception("subtitles_list error")
        return jsonify({"subtitles": []})

# ────────── pobieranie pojedynczego pliku SRT ────────────────
@app.route("/subtitles/download/<hash>.srt")
def download_subtitle(hash: str):
    try:
        log.info(f"Pobieranie napisów: {hash}")
        txt = napi.download(hash)
        if not txt or "-->" not in txt:
            return "404", 404
        return Response(
            txt.encode("utf-8"),
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename=\"{hash}.srt\"'}
        )
    except Exception:
        log.exception(f"download_subtitle {hash}")
        return "500", 500

# ────────── uruchomienie serwera ─────────────────────────────
if __name__ == "__main__":
    log.info("Start addon (pełna lista, TMDB)")
    serve(app, host="0.0.0.0", port=7002)
