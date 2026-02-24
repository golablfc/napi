#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import urllib.parse
import logging
import requests
from typing import Dict
from flask import Flask, jsonify, request, Response

from napiprojekt_logic import NapiProjektKatalog

# ────────── konfiguracja logów ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
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

def get_cinemeta_info(ctype: str, imdb_id: str):
    try:
        url = f"https://v3-cinemeta.strem.io/meta/{ctype}/{imdb_id}.json"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("meta", {})
            return data.get("name", ""), str(data.get("year", ""))[:4]
    except Exception as e:
        log.error(f"Cinemeta fetch error: {e}")
    return "", ""

@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET,OPTIONS"
    return resp

@app.route("/manifest.json")
def manifest():
    return jsonify({
        "id": "org.stremio.napiprojekt.python",
        "version": "6.2.1",
        "name": "NapiProjekt PL (LOCAL)",
        "description": "Test lokalny",
        "resources": ["subtitles"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"]
    })

@app.route("/subtitles/<ctype>/<path:imdb_plus>.json")
def subtitles_list(ctype: str, imdb_plus: str):
    try:
        decoded = urllib.parse.unquote(imdb_plus)
        imdb_match = re.match(r"^(tt\d{7,8})", decoded)
        if not imdb_match:
            return jsonify({"subtitles": []})
        imdb_id = imdb_match.group(1)
        params = parse_params(decoded)
        title, year = get_cinemeta_info(ctype, imdb_id)
        
        item: Dict[str, str] = {
            "imdb_id": imdb_id,
            "season": params.get("season", ""),
            "episode": params.get("episode", ""),
            "title": title if ctype == "movie" else "",
            "tvshow": title if ctype == "series" else "",
            "year": year
        }

        log.info(f"Searching NapiProjekt with: {item}")
        raw = napi.search(item, imdb_id)
        
        subtitles = []
        for s in raw:
            subtitles.append({
                "id": f"{imdb_id}_{s['link_hash']}_pl",
                # Ważne: usuwamy .srt z linku jeśli napiprojekt_logic obsługuje czysty hash
                "url": f"{request.url_root}subtitles/download/{s['link_hash']}",
                "lang": f"{hms(s.get('_duration'))} · PL",
                "name": f"{s['label']}"
            })
        return jsonify({"subtitles": subtitles})
    except Exception:
        log.exception("subtitles_list error")
        return jsonify({"subtitles": []})

# ─── BRAKUJĄCA FUNKCJA DO POBIERANIA ───
@app.route("/subtitles/download/<path:subid>")
def download_subtitles(subid):
    try:
        log.info(f"Otrzymano prośbę o pobranie: {subid}")
        # subid to nasz link_hash (np. NPX... lub QUERY_...)
        content = napi.download(subid)
        
        if content:
            return Response(
                content,
                mimetype='text/plain',
                headers={"Content-disposition": "attachment; filename=subtitles.srt"}
            )
        return "Błąd: Napiprojekt nie zwrócił napisów", 404
    except Exception:
        log.exception("download_subtitles error")
        return "Internal Server Error", 500

# ────────── uruchomienie serwera ─────────────────────────────
if __name__ == "__main__":
    log.info("Start addon NapiProjekt (LOCAL TEST MODE)")
    app.run(host="127.0.0.1", port=7002)
