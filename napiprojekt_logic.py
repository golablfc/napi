#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NapiProjekt · wyszukiwanie napisów oparte na tym samym mechanizmie,
którego używa dodatek Kodi 'service.subtitles.napiprojektkatalog'.

Kroki:
1. TMDB → polski tytuł + rok.
2. POST do /ajax/search_catalog.php (queryKind 1/2).
3. Dla każdego <div class="movieSearchContent">:
   • porównaj tytuły po get_clean().
   • wyciągnij ID z href (`napisy-<ID>-...`).
4. Zbuduj URL detail:
   napisy1,1,1-dla-<ID>-<slug>-({rok})[-sXXeYY]
5. Pobierz i przetwórz listę napisów (max 100).
"""

from __future__ import annotations
import logging, re, time, base64, zlib, struct, unicodedata, requests
from typing import Dict, List
from bs4 import BeautifulSoup

from utils import (
    parse_subtitles,
    convert_microdvd,
    convert_mpl2,
    convert_timecoded,
)

# ───────── konfiguracja ───────────────────────────────────────
TMDB_KEY   = "d5d16ca655dd74bd22bbe412502a3815"
NP_AJAX    = "https://www.napiprojekt.pl/ajax/search_catalog.php"
NP_API     = "https://www.napiprojekt.pl/api/api-napiprojekt3.php"
NP_BASE    = "https://www.napiprojekt.pl"
MAX_PAGES  = 3         # detail paging
PAGE_DELAY = 0.1
SESSION    = requests.Session()

log = logging.getLogger("NapiProjekt")
log.setLevel(logging.DEBUG)

# ───────── pomocnicze ─────────────────────────────────────────
def _norm(txt: str) -> str:
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii").lower()

def getsearch(title: str) -> str:
    """kopia funkcji getsearch z dodatku Kodi – zastępuje znaki diakrytyczne, usuwa kropki, myślniki itd."""
    t = _norm(title)
    t = re.sub(r"[&'\".:\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def get_clean(title: str) -> str:
    t = _norm(title)
    t = re.sub(r"\bthe\b", "", t)             # usuń 'the'
    t = re.sub(r"\(\d{4}\)", "", t)           # usuń rok w nawiasie
    t = re.sub(r"s\d{1,2}e\d{1,2}", "", t)    # usuń SxxEyy
    t = re.sub(r"[^\w]", "", t)               # tylko [a-z0-9]
    return t

def _decrypt_np(blob: bytes) -> str:
    key = [0x5E,0x34,0x45,0x43,0x52,0x45,0x54,0x5F]
    b   = bytearray(blob)
    for i in range(len(b)):
        b[i] ^= key[i % 8]
        b[i] = ((b[i] << 4) & 0xFF) | (b[i] >> 4)
    crc = struct.unpack("<I", b[:4])[0]
    inner = b[4:]
    if (zlib.crc32(inner) & 0xFFFFFFFF) != crc:
        raise ValueError("CRC mismatch")
    return zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")

# ───────── klasa główna ───────────────────────────────────────
class NapiProjektKatalog:
    def __init__(self):
        self.session = SESSION
        self.logger  = log

    # TMDB → (titlePL, year)
    def _tmdb(self, imdb: str) -> tuple[str|None, str|None]:
        url = f"https://api.themoviedb.org/3/find/{imdb}?api_key={TMDB_KEY}&language=pl&external_source=imdb_id"
        self.logger.debug(f"TMDB GET: {url}")
        r = self.session.get(url, timeout=15)
        if r.status_code != 200:
            return None, None
        js = r.json()
        for sec in ("tv_results", "movie_results"):
            if js.get(sec):
                obj   = js[sec][0]
                title = (obj.get("name") or obj.get("title") or "").strip()
                date  = obj.get("first_air_date") or obj.get("release_date") or ""
                year  = date[:4] if date else ""
                self.logger.debug(f"TMDB (pl) -> title={title} year={year}")
                return title, year
        return None, None

    # POST search_catalog.php
    def _ajax_blocks(self, title: str, year: str, is_series: bool) -> list[BeautifulSoup]:
        data = {
            "queryKind": "1" if is_series else "2",
            "queryString": getsearch(title),
            "queryYear": "" if is_series else year,
            "associate": ""
        }
        self.logger.debug(f"AJAX POST: {NP_AJAX} data={data}")
        r = self.session.post(NP_AJAX, data=data, timeout=15)
        if r.status_code != 200:
            self.logger.debug(f"AJAX HTTP {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "lxml")
        blocks = soup.select("div.movieSearchContent")
        self.logger.debug(f"AJAX blocks: {len(blocks)}")
        return blocks

    # detail → list of subtitles (≤100)
    def _detail_subs(self, url: str) -> list[dict]:
        subs, seen = [], set()
        pattern = re.compile(r"napisy\d+,")
        for pg in range(1, MAX_PAGES + 1):
            pgurl = pattern.sub(f"napisy{pg},", url, 1)
            self.logger.debug(f"DETAIL GET: {pgurl}")
            r = self.session.get(pgurl, timeout=15)
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
                try:
                    dls = int(re.sub(r"[^\d]", "", tds[4].text.strip()) or "0")
                except ValueError:
                    dls = 0
                subs.append({
                    "link_hash": h,
                    "label":     tds[1].text.strip(),
                    "_downloads": dls
                })
                if len(subs) >= 100:
                    return subs
            time.sleep(PAGE_DELAY)
        return subs

    # public method
    def search(self, meta: Dict) -> List[Dict]:
        imdb    = meta.get("imdb_id")
        season  = meta.get("season")
        episode = meta.get("episode")
        is_series = bool(season)

        if not imdb:
            self.logger.info("Brak imdb_id – przerywam")
            return []

        title, year = self._tmdb(imdb)
        if not title:
            self.logger.info("TMDB nie zwrócił tytułu/roku")
            return []

        blocks = self._ajax_blocks(title, year, is_series)
        if not blocks:
            self.logger.debug("Brak bloków z wyszukiwarki.")
            return []

        canon_wanted = get_clean(title)
        candidates = []
        for blk in blocks:
            h3 = blk.select_one(".movieTitleCat")
            if not h3:
                continue
            hdr = h3.text.strip()
            if get_clean(hdr) != canon_wanted:
                continue
            href = h3["href"]
            m = re.search(r"-dla-(\d+)-", href)
            if not m:
                continue
            nid = m.group(1)
            candidates.append((nid, hdr))
        if not candidates:
            self.logger.debug("Brak kandydatów po get_clean.")
            return []

        subs_all = []
        slug = title.replace(" ", "-")
        for nid, hdr in candidates:
            base = f"{NP_BASE}/napisy1,1,1-dla-{nid}-{slug}-({year})"
            detail = f"{base}-s{season.zfill(2)}e{episode.zfill(2)}" if season and episode else base
            subs_all.extend(self._detail_subs(detail))
            if len(subs_all) >= 100:
                break

        subs_all.sort(key=lambda x: x.get("_downloads", 0), reverse=True)
        self.logger.info(f"Found {len(subs_all)} subtitles total")
        return subs_all[:100]

    # download by hash
    def download(self, hash_: str) -> str | None:
        data = {
            "mode": "17",
            "client": "NapiProjektPython",
            "downloaded_subtitles_id": hash_,
            "downloaded_subtitles_lang": "PL",
            "downloaded_subtitles_txt": "1",
        }
        self.logger.debug(f"DOWNLOAD POST: {NP_API} id={hash_}")
        r = self.session.post(NP_API, data=data, timeout=20)
        if r.status_code != 200:
            self.logger.error(f"Download HTTP {r.status_code}")
            return None
        m = re.search(r"<content>(.*?)</content>", r.text, re.S)
        if not m:
            self.logger.error("Brak <content>")
            return None
        payload = base64.b64decode(m.group(1))
        if payload.startswith(b"NP"):
            try:
                raw = _decrypt_np(payload[4:])
            except Exception as e:
                self.logger.error(f"Decrypt err: {e}")
                return None
        else:
            try:
                raw = payload.decode("utf-8")
            except UnicodeDecodeError:
                raw = payload.decode("cp1250", "ignore")

        if "{" in raw:
            return convert_microdvd(raw)
        if "[" in raw:
            return convert_mpl2(raw)
        if re.search(r"\d{1,2}:\d{2}:\d{2}\s*:", raw):
            return convert_timecoded(raw)
        return raw
