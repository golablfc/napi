#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NapiProjekt – wyszukiwanie napisów (logika jak w dodatku Kodi homik)
• bierze polski + oryginalny tytuł z TMDb
• szuka przez ajax/search_catalog.php
• akceptuje href w formach:
      napisy-<ID>-Tytul-(Rok)
      napisy1,1,1-dla-<ID>-Tytul-(Rok)
• buduje DETAIL url i pobiera max 100 napisów (sort ‑downloads)
• pełne debug‑logi każdego odwiedzanego URL
"""

from __future__ import annotations
import logging, re, time, base64, zlib, struct, unicodedata, requests
from typing import Dict, List
from bs4 import BeautifulSoup
from utils import convert_microdvd, convert_mpl2, convert_timecoded

TMDB_KEY  = "d5d16ca655dd74bd22bbe412502a3815"
NP_BASE   = "https://www.napiprojekt.pl"
NP_AJAX   = f"{NP_BASE}/ajax/search_catalog.php"
NP_API    = f"{NP_BASE}/api/api-napiprojekt3.php"
MAX_PAGES = 3
DELAY     = 0.1
SESSION   = requests.Session()

log = logging.getLogger("NapiProjekt")
log.setLevel(logging.DEBUG)

# ────── helpers ──────────────────────────────────────────────
def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()

def clean_search(s: str) -> str:
    s = _norm(s)
    s = re.sub(r"[&'\".:\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def clean_cmp(s: str) -> str:
    s = _norm(s)
    s = re.sub(r"\bthe\b", "", s)
    s = re.sub(r"\(\d{4}\)", "", s)
    s = re.sub(r"s\d{1,2}e\d{1,2}", "", s)
    return re.sub(r"[^\w]", "", s)

def _decrypt_np(blob: bytes) -> str:
    key = [0x5E,0x34,0x45,0x43,0x52,0x45,0x54,0x5F]
    b   = bytearray(blob)
    for i in range(len(b)):
        b[i] ^= key[i % 8]
        b[i]  = ((b[i] << 4) & 0xFF) | (b[i] >> 4)
    crc, = struct.unpack("<I", b[:4])
    inner = b[4:]
    if (zlib.crc32(inner) & 0xFFFFFFFF) != crc:
        raise ValueError("CRC mismatch")
    return zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")

# ────── główna klasa ─────────────────────────────────────────
class NapiProjektKatalog:
    def __init__(self):
        self.s = SESSION
        self.log = log

    # TMDb → (pl, en, year)
    def _tmdb(self, imdb):
        url = f"https://api.themoviedb.org/3/find/{imdb}?api_key={TMDB_KEY}&language=pl&external_source=imdb_id"
        self.log.debug(f"TMDB GET: {url}")
        r = self.s.get(url, timeout=15)
        if r.status_code != 200:
            return None, None, None
        js = r.json()
        for sec in ("tv_results", "movie_results"):
            if js.get(sec):
                o = js[sec][0]
                pl = (o.get("name") or o.get("title") or "").strip()
                en = (o.get("original_name") or o.get("original_title") or pl).strip()
                date = o.get("first_air_date") or o.get("release_date") or ""
                return pl, en, date[:4] if date else ""
        return None, None, None

    # AJAX search_catalog.php
    def _ajax_blocks(self, title, year, is_series):
        data = {
            "queryKind": "1" if is_series else "2",
            "queryString": clean_search(title),
            "queryYear": "" if is_series else year,
            "associate": ""
        }
        self.log.debug(f"AJAX POST: {NP_AJAX} {data}")
        r = self.s.post(NP_AJAX, data=data, timeout=15)
        if r.status_code != 200:
            self.log.debug(f"AJAX HTTP {r.status_code}")
            return []
        return BeautifulSoup(r.text, "lxml").select("div.movieSearchContent a.movieTitleCat")

    # detail page
    def _detail(self, url):
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
                if not a: continue
                h = a["href"].replace("napiprojekt:", "")
                if h in seen: continue
                seen.add(h)
                tds = row.find_all("td")
                if len(tds) < 5: continue
                try:
                    dls = int(re.sub(r"[^\d]", "", tds[4].text) or "0")
                except ValueError:
                    dls = 0
                subs.append({"link_hash": h,
                             "label": tds[1].text.strip(),
                             "_downloads": dls})
                if len(subs) >= 100:
                    return subs
            time.sleep(DELAY)
        return subs

    # ────── PUBLIC search ───────────────────────────────────
    def search(self, meta: Dict) -> List[Dict]:
        imdb = meta.get("imdb_id")
        season, episode = meta.get("season"), meta.get("episode")
        if not imdb:
            return []

        t_pl, t_en, year = self._tmdb(imdb)
        if not t_pl:
            return []

        wanted = {clean_cmp(t_pl), clean_cmp(t_en)}
        blocks = self._ajax_blocks(t_pl, year, bool(season))
        self.log.debug(f"AJAX blocks: {len(blocks)}")
        cand_urls = []

        for a in blocks:
            hdr = a.text.strip()
            canon = clean_cmp(hdr)
            self.log.debug(f"⮞ {hdr} -> {canon}")
            if not any(w in canon or canon in w for w in wanted):
                self.log.debug("   ✗ no‑match")
                continue
            href = a.get("href", "")
            self.log.debug(f"   href={href}")
            m = re.search(r"(?:-dla-|napisy-)(\d+)-", href)
            if not m:
                self.log.debug("   ✗ brak ID")
                continue
            cid = m.group(1)
            # zamieniamy napisy- -> napisy1,1,1-dla-
            if href.startswith("napisy-"):
                href = href.replace("napisy-", "napisy1,1,1-dla-", 1)
            url = f"{NP_BASE}/{href}"
            if season and episode:
                url += f"-s{season.zfill(2)}e{episode.zfill(2)}"
            self.log.debug(f"   ✓ DETAIL url={url}")
            cand_urls.append(url)

        if not cand_urls:
            self.log.info("Found 0 subtitles total")
            return []

        all_subs = []
        for url in cand_urls:
            all_subs.extend(self._detail(url))
            if len(all_subs) >= 100:
                break

        all_subs.sort(key=lambda x: x.get("_downloads", 0), reverse=True)
        self.log.info(f"Found {len(all_subs)} subtitles total")
        return all_subs[:100]

    # ────── PUBLIC download ─────────────────────────────────
    def download(self, h: str) -> str | None:
        payload = {
            "mode":"17","client":"NapiProjektPython",
            "downloaded_subtitles_id":h,
            "downloaded_subtitles_lang":"PL",
            "downloaded_subtitles_txt":"1",
        }
        self.log.debug(f"DOWNLOAD POST: {NP_API} id={h}")
        r = self.s.post(NP_API, data=payload, timeout=20)
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
        if "{" in raw:         return convert_microdvd(raw)
        if "[" in raw:         return convert_mpl2(raw)
        if re.search(r"\d{1,2}:\d{2}:\d{2}\s*:", raw): return convert_timecoded(raw)
        return raw
