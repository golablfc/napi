#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Logika wyszukiwania napisów na NapiProjekt z danymi z TMDB.
Dodano obszerne logi DEBUG, aby śledzić każdy odwiedzany URL.
"""

import logging, re, time, base64, zlib, struct, requests
from typing import List, Dict
from bs4 import BeautifulSoup

from utils import (
    parse_subtitles,
    convert_microdvd,
    convert_mpl2,
    convert_timecoded,
)

TMDB_API_KEY = "d5d16ca655dd74bd22bbe412502a3815"
MAX_PAGES = 50
PAGE_DELAY = 0.15
SESSION = requests.Session()


class NapiProjektKatalog:
    def __init__(self):
        self.session = SESSION
        self.logger = logging.getLogger("NapiProjekt")
        self.logger.setLevel(logging.DEBUG)

    # ───────────────── TMDB ──────────────────
    def _fetch_tmdb_info(self, imdb_id):
        url = (
            f"https://api.themoviedb.org/3/find/{imdb_id}"
            f"?api_key={TMDB_API_KEY}&language=pl&external_source=imdb_id"
        )
        self.logger.debug(f"TMDB request: {url}")
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        for sec in ("tv_results", "movie_results"):
            if data.get(sec):
                obj = data[sec][0]
                title = (obj.get("name") or obj.get("title") or "").strip()
                date = obj.get("first_air_date") or obj.get("release_date") or ""
                year = date[:4] if date else ""
                self.logger.debug(f"TMDB (pl) -> title={title} year={year}")
                return title, year
        return None, None

    # ───────────────── katalog NP ────────────
    def _fetch_catalog_blocks(self, title: str, year: str):
        canon_title = re.sub(r"[^a-z0-9]", "", title.lower())
        results = []
        for page in range(1, MAX_PAGES + 1):
            url = (
                f"https://www.napiprojekt.pl/napisy-katalog-"
                f"{page}-wszystkie-{year}-0-0.html"
            )
            self.logger.debug(f"SEARCH katalog: {url}")
            r = self.session.get(url, timeout=15)
            if r.status_code != 200:
                self.logger.debug(f"Katalog HTTP {r.status_code}")
                break
            soup = BeautifulSoup(r.text, "lxml")
            blocks = soup.select("div.kategoria > div")
            self.logger.debug(f"SEARCH blocks: {len(blocks)} page={page}")
            for blk in blocks:
                a = blk.select_one(".movieTitleCat")
                if not a:
                    continue
                hdr = a.text.strip()
                canon_hdr = re.sub(r"[^a-z0-9]", "", hdr.lower())
                if canon_title not in canon_hdr and canon_hdr not in canon_title:
                    continue
                href = a["href"]
                m = re.search(r"-dla-(\d+)-", href)
                if m:
                    nid = m.group(1)
                    self.logger.debug(f"  ✓ dopasowano blok: «{hdr}» id={nid}")
                    results.append((nid, hdr, href))
            if results:
                break
            time.sleep(PAGE_DELAY)
        return results

    # ───────────────── decrypt helper ────────
    @staticmethod
    def _decrypt_np(payload: bytes) -> str:
        key = [0x5E, 0x34, 0x45, 0x43, 0x52, 0x45, 0x54, 0x5F]
        blk = bytearray(payload)
        for i in range(len(blk)):
            blk[i] ^= key[i % 8]
            blk[i] = ((blk[i] << 4) & 0xFF) | (blk[i] >> 4)
        crc = struct.unpack("<I", blk[:4])[0]
        data = blk[4:]
        if zlib.crc32(data) & 0xFFFFFFFF != crc:
            raise ValueError("CRC mismatch")
        return zlib.decompress(data, -zlib.MAX_WBITS).decode("utf-8", "ignore")

    # ───────────────── detail page ───────────
    def _get_subtitles_from_detail(self, url: str):
        self.logger.debug(f"DETAIL GET: {url}")
        r = self.session.get(url, timeout=15)
        if r.status_code != 200:
            self.logger.debug(f"{url} HTTP {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "lxml")
        rows = soup.select("tbody tr")
        return parse_subtitles(rows)

    # ───────────────── download hash ─────────
    def download(self, napisy_hash: str) -> str | None:
        url = "https://www.napiprojekt.pl/api/api-napiprojekt3.php"
        self.logger.debug(f"DOWNLOAD POST: {url} id={napisy_hash}")
        data = {
            "mode": "1",
            "client": "NapiProjektPython",
            "downloaded_subtitles_id": napisy_hash,
            "downloaded_subtitles_lang": "PL",
            "downloaded_subtitles_txt": "1",
        }
        r = self.session.post(url, data=data, timeout=20)
        if r.status_code != 200:
            self.logger.error(f"Download HTTP {r.status_code}")
            return None
        xml_content = r.text
        m = re.search(r"<content>(.*?)</content>", xml_content, re.S)
        if not m:
            self.logger.error("Download: no <content>")
            return None
        payload = base64.b64decode(m.group(1))
        if payload.startswith(b"NP"):
            try:
                raw = self._decrypt_np(payload[4:])
            except Exception as e:
                self.logger.error(f"NP decrypt error: {e}")
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

    # ───────────────── public search ─────────
    def search(self, args: Dict) -> List[Dict]:
        imdb_id = args.get("imdb_id")
        season  = args.get("season")
        episode = args.get("episode")

        if not imdb_id:
            self.logger.info("Brak imdb_id – przerywam.")
            return []

        title, year = self._fetch_tmdb_info(imdb_id)
        if not title or not year:
            self.logger.info("TMDB nie zwrócił tytułu/roku.")
            return []

        self.logger.info(
            f"Searching NapiProjekt with: "
            f"{{'imdb_id': '{imdb_id}', 'tvshow': '{title}', 'year': '{year}', "
            f"'season': '{season}', 'episode': '{episode}'}}"
        )

        blocks = self._fetch_catalog_blocks(title, year)
        if not blocks:
            self.logger.debug("Brak bloków z pasującym tytułem.")
            return []

        subs: List[Dict] = []
        slug_title = title.replace(" ", "-")
        for nid, hdr, _ in blocks:
            base = f"https://www.napiprojekt.pl/napisy1,1,1-dla-{nid}-{slug_title}-({year})"
            detail = f"{base}-s{season.zfill(2)}e{episode.zfill(2)}" if season and episode else base
            subs.extend(self._get_subtitles_from_detail(detail))

            if not subs and season:
                # fallback: tylko sezon
                s_url = f"{base}-s{season.zfill(2)}"
                self.logger.debug(f"DETAIL S-fallback GET: {s_url}")
                subs.extend(self._get_subtitles_from_detail(s_url))

            if not subs:
                # fallback: bez sezonu i odcinka
                p_url = base
                self.logger.debug(f"DETAIL plain GET: {p_url}")
                subs.extend(self._get_subtitles_from_detail(p_url))

            if len(subs) >= 100:
                break

        subs.sort(key=lambda x: x.get("_downloads", 0), reverse=True)
        return subs[:100]
