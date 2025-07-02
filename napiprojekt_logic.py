#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NapiProjekt – logika wyszukiwania i pobierania napisów (Stremio)
• Pobiera pl + en tytuł z TMDb
• Szuka przez ajax/search_catalog.php
• Obsługuje linki napisy‑<ID>‑… i napisy1,1,1‑dla‑<ID>‑…
• Filtruje wiersze bez czasu (HH:MM:SS) – teraz skanuje **dowolną** kolumnę
• Bezpieczny fallback download (Base64 / czysty tekst)
"""

from __future__ import annotations
import logging, re, time, base64, zlib, struct, unicodedata, html, requests
from typing import Dict, List
from bs4 import BeautifulSoup
from utils import convert_microdvd, convert_mpl2, convert_timecoded

TMDB_KEY  = "d5d16ca655dd74bd22bbe412502a3815"
NP_BASE   = "https://www.napiprojekt.pl"
NP_AJAX   = f"{NP_BASE}/ajax/search_catalog.php"
NP_API    = f"{NP_BASE}/api/api-napiprojekt3.php"
SESSION   = requests.Session()
MAX_PAGES = 3
DELAY     = 0.1

log = logging.getLogger("NapiProjekt")
log.setLevel(logging.DEBUG)

# ───── helpers ──────────────────────────────────────────────
def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore") \
                     .decode("ascii").lower()

def clean_search(s: str) -> str:
    s = _norm(s)
    s = re.sub(r"[&'\".:\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def clean_cmp(s: str) -> str:
    s = _norm(s)
    s = re.sub(r"\bthe\b", "", s)
    s = re.sub(r"[^\w]", "", s)
    return s

def _decrypt_np(blob: bytes) -> str:
    key = [0x5E,0x34,0x45,0x43,0x52,0x45,0x54,0x5F]
    b = bytearray(blob)
    for i in range(len(b)):
        b[i] ^= key[i % 8]
        b[i]  = ((b[i] << 4) & 0xFF) | (b[i] >> 4)
    crc,   = struct.unpack("<I", b[:4])
    inner   = b[4:]
    if (zlib.crc32(inner) & 0xFFFFFFFF) != crc:
        raise ValueError("CRC mismatch")
    return zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")

_dur_pat = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")
def _fmt(sec:int)->str: return f"{sec//3600:02}:{(sec%3600)//60:02}:{sec%60:02}"

# ───── klasa główna ─────────────────────────────────────────
class NapiProjektKatalog:
    def __init__(self):
        self.s = SESSION
        self.log = log

    # ── TMDb -----------------------------------------------------------------
    def _tmdb(self, imdb):
        url = f"https://api.themoviedb.org/3/find/{imdb}?api_key={TMDB_KEY}&language=pl&external_source=imdb_id"
        self.log.debug(f"TMDB GET: {url}")
        r = self.s.get(url, timeout=15)
        js = r.json() if r.status_code == 200 else {}
        for sec in ("tv_results", "movie_results"):
            if js.get(sec):
                o = js[sec][0]
                pl = (o.get("name") or o.get("title") or "").strip()
                en = (o.get("original_name") or o.get("original_title") or pl).strip()
                date = o.get("first_air_date") or o.get("release_date") or ""
                return pl, en, (date[:4] if date else "")
        return None, None, None

    # ── AJAX katalog ----------------------------------------------------------
    def _ajax_blocks(self, title, year, series):
        data = {
            "queryKind": "1" if series else "2",
            "queryString": clean_search(title),
            "queryYear": "" if series else year,
            "associate": ""
        }
        self.log.debug(f"AJAX POST: {NP_AJAX} {data}")
        r = self.s.post(NP_AJAX, data=data, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        return soup.select("div.movieSearchContent a.movieTitleCat")

    # ── DETAIL page -----------------------------------------------------------
    def _detail(self, url: str):
        subs, seen = [], set()
        pat = re.compile(r"napisy\d+,")
        for pg in range(1, MAX_PAGES + 1):
            pgurl = pat.sub(f"napisy{pg},", url, 1)
            self.log.debug(f"DETAIL GET: {pgurl}")
            r = self.s.get(pgurl, timeout=15)
            if r.status_code != 200:
                break
            rows = BeautifulSoup(r.text, "lxml").select("tbody tr")
            if not rows:
                break
            for row in rows:
                a = row.find("a", href=re.compile(r"napiprojekt:"))
                if not a:
                    continue
                h = a["href"].replace("napiprojekt:", "")
                if h in seen:
                    continue
                seen.add(h)

                tds = row.find_all("td")
                if len(tds) < 5:
                    continue

                # SZUKAJ CZASU W DOWOLNEJ KOLUMNIE
                sec = None
                for td in tds:
                    m = _dur_pat.search(td.text)
                    if m:
                        sec = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                        break
                if sec is None:
                    self.log.debug(f"   ✗ pomijam {h} – brak czasu")
                    continue

                try:
                    dls = int(re.sub(r"[^\d]", "", tds[4].text) or "0")
                except ValueError:
                    dls = 0

                subs.append({
                    "link_hash": h,
                    "label": tds[1].text.strip(),
                    "lang": f"{_fmt(sec)} · PL",
                    "_downloads": dls
                })
                if len(subs) >= 100:
                    return subs
            time.sleep(DELAY)
        return subs

    # ── PUBLIC search ---------------------------------------------------------
    def search(self, meta: Dict) -> List[Dict]:
        imdb = meta.get("imdb_id"); season, episode = meta.get("season"), meta.get("episode")
        if not imdb:
            return []
        pl, en, year = self._tmdb(imdb)
        if not pl:
            return []
        wanted = {clean_cmp(pl), clean_cmp(en)}
        blocks = self._ajax_blocks(pl, year, bool(season))

        detail_urls = []
        for a in blocks:
            if not any(w in clean_cmp(a.text) for w in wanted):
                continue
            href = a.get("href", "")
            m = re.search(r"(?:-dla-|napisy-)(\d+)-", href)
            if not m:
                continue
            if href.startswith("napisy-"):
                href = href.replace("napisy-", "napisy1,1,1-dla-", 1)
            url = f"{NP_BASE}/{href}"
            if season and episode:
                url += f"-s{season.zfill(2)}e{episode.zfill(2)}"
            detail_urls.append(url)

        subs = []
        for url in detail_urls:
            subs.extend(self._detail(url))
            if len(subs) >= 100:
                break
        subs.sort(key=lambda x: x["_downloads"], reverse=True)
        return subs[:100]

    # ── PUBLIC download -------------------------------------------------------
    def download(self, hid: str) -> str | None:
        payload = {
            "mode": "17", "client": "NapiProjektPython",
            "downloaded_subtitles_id": hid,
            "downloaded_subtitles_lang": "PL",
            "downloaded_subtitles_txt": "1",
        }
        r = self.s.post(NP_API, data=payload, timeout=20)
        if r.status_code != 200:
            return None
        m = re.search(r"<content>(.*?)</content>", r.text, re.S)
        if not m:
            return None
        content = m.group(1).strip()

        try:
            blob = base64.b64decode(content)
        except Exception:
            raw = html.unescape(content)
        else:
            if blob.startswith(b"NP"):
                raw = _decrypt_np(blob[4:])
            else:
                try:    raw = blob.decode("utf-8")
                except UnicodeDecodeError:
                        raw = blob.decode("cp1250", "ignore")

        if "{" in raw:
            return convert_microdvd(raw)
        if "[" in raw:
            return convert_mpl2(raw)
        if re.search(r"\d{1,2}:\d{2}:\d{2}\s*:", raw):
            return convert_timecoded(raw)
        return raw
