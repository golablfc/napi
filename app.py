#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re, logging, urllib.parse, json, requests
from typing import Dict
from flask import Flask, jsonify, request, Response
from waitress import serve
from napiprojekt_logic import NapiProjektKatalog

# ───── konfiguracja logów ──────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("stremio_napi.log"), logging.StreamHandler()]
)
log = logging.getLogger("ST‑NAPI")

# ───── stałe serwisów ──────────────────────────────────────────
TMDB_KEY   = "d5d16ca655dd74bd22bbe412502a3815"
TMDB_FIND  = "https://api.themoviedb.org/3/find/{imdb}"
CINEMETA   = "https://v3-cinemeta.strem.io/meta"

app = Flask(__name__)
napi = NapiProjektKatalog()

# ───── pomocnicze parsowanie ID + parametrów ───────────────────
def parse_params(decoded_id: str) -> Dict[str, str]:
    if "/" not in decoded_id:
        return {}
    tail = decoded_id.split("/", 1)[1]
    return {k.lower(): v for k, v in (p.split("=", 1) for p in tail.split("&") if "=" in p)}

# ───── TMDB PL → tytuł + rok ───────────────────────────────────
def fill_from_tmdb(imdb_id: str, item: Dict[str, str]) -> None:
    for lang in ("pl", "en"):
        try:
            url = TMDB_FIND.format(imdb=imdb_id)
            params = {
                "api_key": TMDB_KEY,
                "language": lang,
                "external_source": "imdb_id"
            }
            r = requests.get(url, params=params, timeout=6)
            if r.status_code != 200:
                continue
            data = r.json()
            results = (data.get("movie_results") or
                       data.get("tv_results")   or [])
            if not results:
                continue
            first = results[0]
            if "title" in first:   # film
                item["title"] = first["title"]
                date = first.get("release_date") or "0000"
            else:                  # serial
                item["tvshow"] = first["name"]
                date = first.get("first_air_date") or "0000"
            item["year"] = date[:4]
            log.debug(f"TMDB ({lang}) -> title={item.get('title') or item.get('tvshow')} year={item['year']}")
            return
        except Exception as e:
            log.debug(f"TMDB fetch ({lang}) failed: {e}")

# ───── Cinemeta fallback (gdy TMDB nie dało nic) ───────────────
def fill_from_cinemeta(imdb_id: str, item: Dict[str, str]) -> None:
    try:
        ctype = "series" if item.get("season") else "movie"
        j = requests.get(f"{CINEMETA}/{ctype}/{imdb_id}.json", timeout=6).json()
        meta = j.get("meta", {})
        if meta.get("name"):
            key = "tvshow" if ctype == "series" else "title"
            item[key] = meta["name"]
        if meta.get("year"):
            item["year"] = str(meta["year"]).split("–")[0]
        log.debug(f"Cinemeta -> title={item.get('title') or item.get('tvshow')} year={item.get('year')}")
    except Exception as e:
        log.debug(f"Cinemeta fetch failed: {e}")

# ───── prosta konwersja sekund → HH:MM:SS (dla labeli) ─────────
def hms(secs):
    if not secs: return "??:??:??"
    secs=int(secs); h, r = divmod(secs,3600); m, s = divmod(r,60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ───── CORS ────────────────────────────────────────────────────
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET,OPTIONS"
    return resp

# ───── manifest Stremio ───────────────────────────────────────
@app.route("/manifest.json")
def manifest():
    return jsonify({
        "id": "org.stremio.napiprojekt.python",
        "version": "5.0.0",
        "name": "NapiProjekt PL · TMDB",
        "description": "Napisy z NapiProjekt – tytuły/rok pobierane z TMDB (PL).",
        "resources": ["subtitles"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt"]
    })

# ───── lista napisów ───────────────────────────────────────────
@app.route("/subtitles/<ctype>/<path:imdb_plus>.json")
def subtitles_list(ctype: str, imdb_plus: str):
    try:
        decoded = urllib.parse.unquote(imdb_plus)
        imdb_id = re.match(r"^(tt\d{7,8})", decoded).group(1)

        params = parse_params(decoded)
        item: Dict[str, str] = {"imdb_id": imdb_id}

        # sezon/odciek: z taila lub z param
        m_se = re.search(r":(\d{1,2})(?::(\d{1,2}))?", decoded)
        if m_se:
            item["season"] = m_se.group(1)
            if m_se.group(2):
                item["episode"] = m_se.group(2)
        if "season" in params:  item["season"]  = params["season"]
        if "episode" in params: item["episode"] = params["episode"]

        # ── 1) TMDB (PL) → title + year ─────────
        fill_from_tmdb(imdb_id, item)
        # ── 2) Cinemeta fallback, jeśli nadal brak title ──
        if not item.get("title") and not item.get("tvshow"):
            fill_from_cinemeta(imdb_id, item)

        log.info(f"Searching NapiProjekt with: {item}")
        raw = napi.search(item, imdb_id, None, None)
        log.info(f"Found {len(raw)} subtitles total")

        filtered = [s for s in raw if s["_duration"]]
        filtered.sort(key=lambda s: (-s["_downloads"], s["_duration"] or 0))

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

# ───── pobieranie pojedynczego pliku ───────────────────────────
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

# ───── uruchomienie serwera ───────────────────────────────────
if __name__ == "__main__":
    log.info("Start addon (pełna lista, TMDB)")
    serve(app, host="0.0.0.0", port=7002)
