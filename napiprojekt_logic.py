#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import base64
import logging
import zlib
import struct
import time
import requests
from xml.dom import minidom
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

class NapiProjektKatalog:
    def __init__(self):
        self.logger = logging.getLogger("NapiProjekt")
        self.download_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.search_url   = "https://www.napiprojekt.pl/ajax/search_catalog.php"
        self.base_url     = "https://www.napiprojekt.pl"
        
        # Udajemy przeglądarkę identycznie jak Kodi, ale z dodatkowymi polami
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.napiprojekt.pl/"
        }
        self.session = requests.Session()
        self.logger.info("NapiProjektKatalog initialized")

    def _decrypt(self, data: bytes) -> bytes:
        key = [0x5E,0x34,0x45,0x43,0x52,0x45,0x54,0x5F]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i%8]
            dec[i] = ((dec[i] << 4) & 0xFF) | (dec[i] >> 4)
        return bytes(dec)

    def _format_time(self, sec: float) -> str:
        h, r = divmod(int(sec), 3600)
        m, s = divmod(r, 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _parse_duration(self, txt: str) -> Optional[float]:
        if not txt: return None
        try:
            if txt.isdigit(): return int(txt) / 1000
            parts = list(map(int, txt.split(":")))
            h, m, s = (parts if len(parts) == 3 else [0] + parts)
            return h * 3600 + m * 60 + s
        except: return None

    def download(self, md5hash: str) -> Optional[str]:
        try:
            payload = {
                "mode": "17",
                "client": "NapiProjektPython",
                "downloaded_subtitles_id": md5hash,
                "downloaded_subtitles_lang": "PL",
                "downloaded_subtitles_txt": "1",
            }
            resp = self.session.post(self.download_url, data=payload, headers=self.headers, timeout=10)
            if resp.status_code != 200: return None
            
            xml = minidom.parseString(resp.content)
            content = xml.getElementsByTagName("content")[0].firstChild.data
            bin_data = base64.b64decode(content)

            if bin_data.startswith(b"NP"):
                dec = self._decrypt(bin_data[4:])
                inner = dec[4:]
                raw = zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")
            else:
                raw = bin_data.decode("utf-8", "ignore")
            return raw
        except Exception as e:
            self.logger.error(f"Download err: {e}")
            return None

    def _get_subtitles_from_detail(self, url: str) -> List[dict]:
        subs = []
        try:
            # Używamy sesji, aby zachować ciągłość
            resp = self.session.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("tbody > tr")
            for row in rows:
                a = row.find("a", href=re.compile(r"napiprojekt:"))
                if not a: continue
                cols = row.find_all("td")
                if len(cols) < 5: continue
                
                subs.append({
                    "language": "pol",
                    "label": cols[1].get_text(strip=True),
                    "link_hash": a["href"].replace("napiprojekt:", ""),
                    "_duration": self._parse_duration(cols[3].get_text(strip=True)),
                    "_downloads": int(re.sub(r"[^\d]", "", cols[4].get_text(strip=True)) or 0),
                })
        except: pass
        return subs

    def search(self, item: Dict[str, str], imdb_id: str, *_) -> List[dict]:
        try:
            # KROK 1: Najpierw "wchodzimy" na stronę główną, aby pobrać ciasteczka
            self.session.get(self.base_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            
            # KROK 2: Wysyłamy właściwe zapytanie
            payload = {
                "queryKind": "1" if item.get("tvshow") else "2",
                "queryString": (item.get("tvshow") or item.get("title") or imdb_id).lower(),
                "queryYear": item.get("year", ""),
                "associate": imdb_id,
            }
            
            resp = self.session.post(self.search_url, data=payload, headers=self.headers, timeout=10)
            
            if resp.status_code != 200:
                self.logger.error(f"Search failed: {resp.status_code}")
                return []
                
            soup = BeautifulSoup(resp.text, "lxml")
            blocks = soup.find_all("div", class_="movieSearchContent")
            result = []
            for blk in blocks:
                a_imdb = blk.find("a", href=re.compile(r"imdb.com/title/(tt\d+)"))
                if not a_imdb or imdb_id not in a_imdb["href"]: continue
                
                title_a = blk.find("a", class_="movieTitleCat")
                if not title_a: continue
                
                # Budujemy pełny URL do szczegółów
                m = re.search(r"napisy-(\d+)-(.*)", title_a["href"])
                if m:
                    nid, slug = m.groups()
                    slug = re.sub(r"[-\s]*\(?\d{4}\)?$", "", slug).strip("-")
                    detail_url = f"{self.base_url}/napisy1,1,1-dla-{nid}-{slug}"
                    if item.get("tvshow") and item.get("season"):
                        detail_url += f"-s{item['season'].zfill(2)}e{item['episode'].zfill(2)}"
                    
                    result.extend(self._get_subtitles_from_detail(detail_url))
            return result
        except Exception as e:
            self.logger.error(f"Search error: {e}")
            return []
