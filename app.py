#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, logging, urllib.parse, requests
from typing import Dict, Optional

from flask import Flask, jsonify, request, Response
from waitress import serve

from napiprojekt_logic import NapiProjektKatalog

# ── root logger ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("stremio_napi.log"), logging.StreamHandler()]
)
log = logging.getLogger("ST-NAPI")

app = Flask(__name__)
napi = NapiProjektKatalog()

CINEMETA = "https://v3-cinemeta.strem.io/meta"

# ───────────────────────── helpers ─────────────────────────────
def parse_params(decoded_id: str) -> Dict[str, str]:
    if "/" not in decoded_id:
        return {}
    tail = decoded_id.split("/", 1)[1]
    return {k.lower(): v for k, v in (p.split("=", 1) for p in tail.split("&") if "=" in p)}

def fill_from_filename(fname: str, item: Dict[str, str]) -> None:
    tv = re.search(r"(.*?)[.\s_-]S(\d{1,2})E(\d{1,2})", fname, re.I)
    if tv:
        item["tvshow"] = tv.group(1).replace(".", " ").strip()
        item["season"], item["episode"] = tv.group(2), tv.group(3)
        return
    mv = re.search(r"(.+?)[.\s_-]*(\d{4})(?!\d)", fname)
    if mv:
        item["title"] = mv.group(1).replace(".", " ").replace("_", " ").strip()
        item["year"] = mv.group(2)

def fill_from_cinemeta(imdb_id: str, item: Dict[str, str]) -> None:
    try:
        ctype = "series" if item.get("season") else "movie"
        url = f"{CINEMETA}/{ctype}/{imdb_id}.json"
        meta = requests.get(url, timeout=6).json().get("meta", {})
        if meta.get("name"):
            key = "tvshow" if ctype == "series" else "title"
            item[key] = meta["name"]
        if meta.get("year"):
            item["year"] = str(meta["year"]).split("–")[0]
    except Exception as e:
        log.debug(f"Cinemeta fetch failed: {e}")

def hms(secs: Optional[float]) -> str:
    if not secs:
        return "??:??:??"
    secs = int(secs)
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ───────────────────────── CORS ────────────────────────────────
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET,OPTIONS"
    return resp

# ───────────────────────── manifest ────────────────────────────
@app.route("/manifest.json")
def manifest():
    return jsonify({
        "id": "org.stremio.napiprojekt.python",
        "version": "4.2.0",
        "name": "NapiProjekt PL · Pełna lista",
        "description": "Wyświetla wszystkie napisy z NapiProjekt (bez OMDb, z fallbackiem Cinemeta).",
        "resources": ["subtitles"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt"]
    })

# ───────────────────────── lista napisów ───────────────────────
@app.route("/subtitles/<ctype>/<path:imdb_plus>.json")
def subtitles_list(ctype: str, imdb_plus: str):
    try:
        decoded = urllib.parse.unquote(imdb_plus)
        imdb_id = re.match(r"^(tt\d{7,8})", decoded).group(1)

        params = parse_params(decoded)
        item: Dict[str, str] = {"imdb_id": imdb_id}

        m_se = re.search(r":(\d{1,2})(?::(\d{1,2}))?", decoded)
        if m_se:
            item["season"] = m_se.group(1)
            if m_se.group(2):
                item["episode"] = m_se.group(2)

        if "season" in params:
            item["season"] = params["season"]
        if "episode" in params:
            item["episode"] = params["episode"]
        if fname := params.get("filename"):
            fill_from_filename(fname, item)

        # ── Cinemeta fallback (gdy brak tytułu) ─────────────────
        if not item.get("tvshow") and not item.get("title"):
            fill_from_cinemeta(imdb_id, item)

        log.info(f"Searching NapiProjekt with: {item}")
        raw_results = napi.search(item, imdb_id, None, None)
        log.info(f"Found {len(raw_results)} subtitles total")

        filtered = [s for s in raw_results if s["_duration"] is not None]
        filtered.sort(key=lambda s: (-s["_downloads"], s["_duration"]))

        subtitles = [{
            "id": f"{imdb_id}_{s['link_hash']}_pl",
            "url": f"{request.url_root}subtitles/download/{s['link_hash']}.srt",
            "lang": f"{hms(s['_duration'])} · PL",
            "name": f"NapiProjekt · {s['_downloads']}× · {s.get('_fps') or '?'} FPS"
        } for s in filtered]

        return jsonify({"subtitles": subtitles})

    except Exception:
        log.exception("subtitles_list error")
        return jsonify({"subtitles": []})

# ───────────────────────── pojedynczy plik ─────────────────────
@app.route("/subtitles/download/<hash>.srt")
def download_subtitle(hash: str):
    try:
        log.info(f"Pobieranie napisów: {hash}")
        txt = napi.download(hash)
        if not txt:
            log.warning(f"{hash}: brak treści")
            return "404", 404
        if "-->" not in txt and "{" in txt:
            txt = napi._convert_microdvd_to_srt(txt) or txt
        if "-->" not in txt:
            log.warning(f"{hash}: brak timestampów – odrzucam")
            return "404", 404
        return Response(
            txt.encode("utf-8"),
            mimetype="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename=\"{hash}.srt\"'}
        )
    except Exception:
        log.exception(f"download_subtitle {hash}")
        return "500", 500

# ────────────────────────── start serwera ──────────────────────
if __name__ == "__main__":
    log.info("Start addon (pełna lista, bez deduplikacji)")
    serve(app, host="0.0.0.0", port=7002)
