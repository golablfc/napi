#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NapiProjekt · wyszukiwanie oparte na ajax/search_catalog.php
(przybliżona logika dodatku Kodi homik).
"""

import logging, re, time, base64, zlib, struct, unicodedata, requests
from typing import Dict, List
from bs4 import BeautifulSoup

from utils import (
    parse_subtitles,
    convert_microdvd,
    convert_mpl2,
    convert_timecoded,
)

TMDB_KEY   = "d5d16ca655dd74bd22bbe412502a3815"
NP_AJAX    = "https://www.napiprojekt.pl/ajax/search_catalog.php"
NP_API     = "https://www.napiprojekt.pl/api/api-napiprojekt3.php"
NP_BASE    = "https://www.napiprojekt.pl"
MAX_PAGES  = 3
PAGE_DELAY = 0.1
SESSION    = requests.Session()

log = logging.getLogger("NapiProjekt")
log.setLevel(logging.DEBUG)

# ───────── normalizacja ──────────────────────────────────────
def _norm(txt: str) -> str:
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii").lower()

def getsearch(title: str) -> str:
    t = _norm(title)
    t = re.sub(r"[&'\".:\-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def get_clean(title: str) -> str:
    t = _norm(title)
    t = re.sub(r"\bthe\b", "", t)
    t = re.sub(r"\(\d{4}\)", "", t)
    t = re.sub(r"s\d{1,2}e\d{1,2}", "", t)
    t = re.sub(r"[^\w]", "", t)
    return t

# ───────── decrypt helper ────────────────────────────────────
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

# ───────── klasa główna ──────────────────────────────────────
class NapiProjektKatalog:
    def __init__(self):
        self.session = SESSION
        self.logger  = log

    def _tmdb(self, imdb: str):
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

    def _ajax_blocks(self, title: str, year: str, is_series: bool):
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

    def _detail_subs(self, url: str):
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
                    "label": tds[1].text.strip(),
                    "_downloads": dls
                })
                if len(subs) >= 100:
                    return subs
            time.sleep(PAGE_DELAY)
        return subs

    # ───────── public API ────────────────────────────────────
    def search(self, meta: Dict) -> List[Dict]:
        imdb    = meta.get("imdb_id")
        season  = meta.get("season")
        episode = meta.get("episode")
        if not imdb:
            return []
        title, year = self._tmdb(imdb)
        if not title or not year:
            return []
        blocks = self._ajax_blocks(title, year, bool(season))
        if not blocks:
            return []

        canon_wanted = get_clean(title)
        candidates = []
        for blk in blocks:
            h3 = blk.select_one(".movieTitleCat")
            if not h3:
                continue
            hdr = h3.text.strip()
            canon_hdr = get_clean(hdr)
            if canon_wanted not in canon_hdr and canon_hdr not in canon_wanted:
                continue
            m = re.search(r"-dla-(\d+)-", h3["href"])
            if m:
                candidates.append((m.group(1), hdr))

        if not candidates:
            return []

        slug = title.replace(" ", "-")
        subs = []
        for nid, _ in candidates:
            base = f"{NP_BASE}/napisy1,1,1-dla-{nid}-{slug}-({year})"
            detail = f"{base}-s{season.zfill(2)}e{episode.zfill(2)}" if season and episode else base
            subs.extend(self._detail_subs(detail))
            if len(subs) >= 100:
                break
        subs.sort(key=lambda x: x.get("_downloads", 0), reverse=True)
        return subs[:100]

    def download(self, h: str) -> str | None:
        payload = {
            "mode": "17",
            "client": "NapiProjektPython",
            "downloaded_subtitles_id": h,
            "downloaded_subtitles_lang": "PL",
            "downloaded_subtitles_txt": "1",
        }
        r = self.session.post(NP_API, data=payload, timeout=20)
        if r.status_code != 200:
            return None
        m = re.search(r"<content>(.*?)</content>", r.text, re.S)
        if not m:
            return None
        blob = base64.b64decode(m.group(1))
        if blob.startswith(b"NP"):
            raw = _decrypt_np(blob[4:])
        else:
            try:
                raw = blob.decode("utf-8")
            except UnicodeDecodeError:
                raw = blob.decode("cp1250", "ignore")
        if "{" in raw:
            return convert_microdvd(raw)
        if "[" in raw:
            return convert_mpl2(raw)
        if re.search(r"\d{1,2}:\d{2}:\d{2}\s*:", raw):
            return convert_timecoded(raw)
        return raw
