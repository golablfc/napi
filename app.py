#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import urllib.parse
import logging
import requests
from typing import Dict
from flask import Flask, jsonify, request, Response
from waitress import serve

from napiprojekt_logic import NapiProjektKatalog

# ────────── konfiguracja logów ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()] # Logujemy bezpośrednio do konsoli Render
)
log = logging.getLogger("ST-NAPI")

app = Flask(__name__)
napi = NapiProjektKatalog()

# ────────── pomocnicze parsowanie ID i parametrów ─────────────
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

# ────────── TŁUMACZ ID NA TYTUŁ (NOWOŚĆ) ───────────────────────
def get_cinemeta_info(ctype: str, imdb_id: str):
    """Pobiera prawdziwy tytuł i rok z bazy Stremio na podstawie IMDB ID."""
    try:
        url = f"https://v3-cinemeta.strem.io/meta/{ctype}/{imdb_id}.json"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("meta", {})
            return data.get("name", ""), str(data.get("year", ""))[:4]
    except Exception as e:
        log.error(f"Cinemeta fetch error: {e}")
    return "", ""

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
        "version": "6.2.0",
        "name": "NapiProjekt PL",
        "description": "Napisy z NapiProjekt z obsługą inteligentnego dopasowania po czasie trwania (Nuvio).",
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
        
        # 1. Pobieramy tytuł (Cinemeta) żeby NapiProjekt wiedział czego szukać
        title, year = get_cinemeta_info(ctype, imdb_id)
        
        item: Dict[str, str] = {
            "imdb_id": imdb_id,
            "season": params.get("season", ""),
            "episode": params.get("episode", ""),
            "title": title if ctype == "movie" else "",
            "tvshow": title if ctype == "series" else "",
            "year": year
        }

        # 2. Nasłuchujemy na czas z Nuvio
        target_duration_sec = None
        if "durationms" in params:
            target_duration_sec = float(params["durationms"]) / 1000.0
            log.info(f"Nuvio mode active! Target duration: {target_duration_sec}s")

        se_match = re.search(r":(\d{1,2})(?::(\d{1,2}))?", decoded)
        if se_match and not item["season"]:
            item["season"] = se_match.group(1)
            item["episode"] = se_match.group(2) or ""

        log.info(f"Searching NapiProjekt with: {item}")
        
        raw = napi.search(item, imdb_id)
        log.info(f"Found {len(raw)} subtitles total")

        for s in raw:
            s_score = s.get('_downloads', 0)
            s_dur = s.get('_duration')

            if target_duration_sec and s_dur:
                diff = abs(target_duration_sec - s_dur)
                if diff <= 0.1:
                    s_score += 10000 
                    s['perfect_match'] = True
                elif diff <= 1.0:
                    s_score += 5000

            s['_score'] = s_score

        raw.sort(key=lambda s: s.get('_score', 0), reverse=True)

        subtitles = []
        for s in raw:
            prefix = "⭐ IDEALNE " if s.get('perfect_match') else ""
            subtitles.append({
                "id": f"{imdb_id}_{s['link_hash']}_pl",
                "url": f"{request.url_root}subtitles/download/{s['link_hash']}.srt",
                "lang": f"{prefix}{hms(s.get('_duration'))} · PL",
                "name": f"NapiProjekt · {s.get('_downloads', 0)} pobrań · {s.get('_fps') or '?'} FPS"
            })

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
    log.info("Start addon NapiProjekt")
    port = int(os.environ.get("PORT", 7002))
    serve(app, host="0.0.0.0", port=port)
