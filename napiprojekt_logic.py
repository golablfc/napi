#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NapiProjekt ‑ logika wyszukiwania i pobierania napisów do Stremio.

Najważniejsze cechy:
• Pobiera polski i oryginalny tytuł z TMDb (API v3).
• Szuka w katalogu NapiProjekt (ajax/search_catalog.php).
• Akceptuje linki 'napisy‑<ID>‑...' i zamienia je na
  'napisy1,1,1-dla-<ID>-...'.
• Buduje URL z ID + sezon/odcinek, skanuje maks. 3 strony
  (napisy1, napisy2, napisy3).
• W _detail() odrzuca wiersze bez kolumny „Długość” (HH:MM:SS),
  dzięki czemu w Stremio nie pojawia się '??:??:??'.
• Zwraca maks. 100 wyników posortowanych malejąco po liczbie pobrań.
• W download() radzi sobie z przypadkami, kiedy <content> jest
  czystym tekstem (nie‑Base64).
"""

from __future__ import annotations
import logging, re, time, base64, zlib, struct, unicodedata, html, requests
from typing import Dict, List
from bs4 import BeautifulSoup
from utils import (
    convert_microdvd,
    convert_mpl2,
    convert_timecoded,
)

# ───── konfiguracja ─────────────────────────────────────────
TMDB_KEY  = "d5d16ca655dd74bd22bbe412502a3815"
NP_BASE   = "https://www.napiprojekt.pl"
NP_AJAX   = f"{NP_BASE}/ajax/search_catalog.php"
NP_API    = f"{NP_BASE}/api/api-napiprojekt3.php"
SESSION   = requests.Session()
MAX_PAGES = 3          # napisy1,2,3
DELAY     = 0.1        # mała pauza by nie spamować

log = logging.getLogger("NapiProjekt")
log.setLevel(logging.DEBUG)

# ───── helpers ──────────────────────────────────────────────
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
    key = [0x5E, 0x34, 0x45, 0x43, 0x52, 0x45, 0x54, 0x5F]
    b   = bytearray(blob)
    for i in range(len(b)):
        b[i] ^= key[i % 8]
        b[i]  = ((b[i] << 4) & 0xFF) | (b[i] >> 4)
    crc,   = struct.unpack("<I", b[:4])
    inner   = b[4:]
    if (zlib.crc32(inner) & 0xFFFFFFFF) != crc:
        raise ValueError("CRC mismatch")
    return zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")

_dur_pat = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")
def _fmt(sec: int) -> str:
    return f"{sec//3600:02}:{(sec%3600)//60:02}:{sec%60:02}"

# ───── klasa główna ─────────────────────────────────────────
class NapiProjektKatalog:
    def __init__(self):
        self.s   = SESSION
        self.log = log

    # ── TMDb -----------------------------------------------------------------
    def _tmdb(self, imdb_id: str):
        url = (f"https://api.themoviedb.org/3/find/{imdb_id}"
               f"?api_key={TMDB_KEY}&language=pl&external_source=imdb_id")
        self.log.debug(f"TMDB GET: {url}")
        r = self.s.get(url, timeout=15)
        data = r.json() if r.status_code == 200 else {}
        for sec in ("tv_results", "movie_results"):
            if data.get(sec):
                o = data[sec][0]
                pl = (o.get("name") or o.get("title") or "").strip()
                en = (o.get("original_name") or o.get("original_title") or pl).strip()
                date = o.get("first_air_date") or o.get("release_date") or ""
                year = date[:4] if date else ""
                return pl, en, year
        return None, None, None

    # ── AJAX katalog ----------------------------------------------------------
    def _ajax_blocks(self, title: str, year: str, is_series: bool):
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
        soup = BeautifulSoup(r.text, "lxml")
        return soup.select("div.movieSearchContent a.movieTitleCat")

    # ── DETAIL page -----------------------------------------------------------
    def _detail(self, url: str):
        subs, seen = [], set()
        pat = re.compile(r"napisy\d+,")
        for page in range(1, MAX_PAGES + 1):
            page_url = pat.sub(f"napisy{page},", url, 1)
            self.log.debug(f"DETAIL GET: {page_url}")
            r = self.s.get(page_url, timeout=15)
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

                # kolumna "Długość" – czas może być w td[2] (filmy) lub td[3] (seriale)
                dur_txt = (tds[2].text.strip() or tds[3].text.strip())
                m = _dur_pat.search(dur_txt)
                if not m:
                    self.log.debug(f"   ✗ pomijam {h} – brak czasu")
                    continue
                sec = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
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
        imdb_id = meta.get("imdb_id")
        season  = meta.get("season")
        episode = meta.get("episode")
        if not imdb_id:
            return []

        pl, en, year = self._tmdb(imdb_id)
        if not pl:
            return []
        wanted = {clean_cmp(pl), clean_cmp(en)}

        blocks = self._ajax_blocks(pl, year, bool(season))
        self.log.debug(f"AJAX blocks: {len(blocks)}")

        detail_urls = []
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

            if href.startswith("napisy-"):
                href = href.replace("napisy-", "napisy1,1,1-dla-", 1)

            url = f"{NP_BASE}/{href}"
            if season and episode:
                url += f"-s{season.zfill(2)}e{episode.zfill(2)}"

            self.log.debug(f"   ✓ DETAIL url={url}")
            detail_urls.append(url)

        subs = []
        for url in detail_urls:
            subs.extend(self._detail(url))
            if len(subs) >= 100:
                break

        subs.sort(key=lambda x: x["_downloads"], reverse=True)
        self.log.info(f"Found {len(subs)} subtitles total")
        return subs[:100]

    # ── PUBLIC download -------------------------------------------------------
    def download(self, hash_id: str) -> str | None:
        self.log.debug(f"DOWNLOAD POST: {NP_API} id={hash_id}")
        payload = {
            "mode": "17",
            "client": "NapiProjektPython",
            "downloaded_subtitles_id": hash_id,
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

        # ── próbujemy base64
        try:
            blob = base64.b64decode(content)
        except Exception:
            # nie‑base64 → czysty tekst (w CDATA) – odkoduj encje
            raw = html.unescape(content)
        else:
            if blob.startswith(b"NP"):
                raw = _decrypt_np(blob[4:])
            else:
                try:
                    raw = blob.decode("utf-8")
                except UnicodeDecodeError:
                    raw = blob.decode("cp1250", "ignore")

        # ── konwersja do SRT
        if "{" in raw:
            return convert_microdvd(raw)
        if "[" in raw:
            return convert_mpl2(raw)
        if re.search(r"\d{1,2}:\d{2}:\d{2}\s*:", raw):
            return convert_timecoded(raw)
        return raw  # fallback
