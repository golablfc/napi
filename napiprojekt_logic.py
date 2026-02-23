#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import urllib.request, urllib.parse, re, base64, logging, zlib, struct, time
from xml.dom import minidom
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

class NapiProjektKatalog:
    def __init__(self):
        self.logger = logging.getLogger("NapiProjekt")
        self.download_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.search_url   = "https://www.napiprojekt.pl/ajax/search_catalog.php"
        self.base_url     = "https://www.napiprojekt.pl"

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
            parts = list(map(int, txt.split(":")))
            h, m, s = (parts if len(parts) == 3 else [0] + parts)
            return h * 3600 + m * 60 + s
        except: return None

    def download(self, md5hash: str) -> Optional[str]:
        try:
            post_data = urllib.parse.urlencode({
                "mode": "17",
                "client": "NapiProjektPython",
                "downloaded_subtitles_id": md5hash,
                "downloaded_subtitles_lang": "PL",
                "downloaded_subtitles_txt": "1",
            }).encode("utf-8")
            
            req = urllib.request.Request(self.download_url, data=post_data, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml = minidom.parseString(resp.read())

            content = xml.getElementsByTagName("content")[0].firstChild.data
            bin_data = base64.b64decode(content)
            if bin_data.startswith(b"NP"):
                dec = self._decrypt(bin_data[4:])
                inner = dec[4:]
                raw = zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")
            else:
                raw = bin_data.decode("utf-8", "ignore")
            return raw
        except: return None

    def _get_subtitles_from_detail(self, url: str) -> List[dict]:
        subs = []
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                soup = BeautifulSoup(resp.read(), "lxml")
            
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
            # DOKŁADNIE to samo co w service.py homika
            post_data = urllib.parse.urlencode({
                "queryKind": "1" if item.get("tvshow") else "2",
                "queryString": (item.get("tvshow") or item.get("title") or imdb_id).lower(),
                "queryYear": item.get("year", ""),
                "associate": imdb_id,
            }).encode("utf-8")
            
            req = urllib.request.Request(self.search_url, data=post_data, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8")
            
            soup = BeautifulSoup(html, "lxml")
            blocks = soup.find_all("div", class_="movieSearchContent")
            result = []
            for blk in blocks:
                a_imdb = blk.find("a", href=re.compile(r"imdb.com/title/(tt\d+)"))
                if not a_imdb or imdb_id not in a_imdb["href"]: continue
                
                title_a = blk.find("a", class_="movieTitleCat")
                if not title_a: continue
                
                # Budujemy URL detali (uproszczone)
                href = title_a["href"]
                m = re.search(r"napisy-(\d+)-(.*)", href)
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
