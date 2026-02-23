#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import base64
import logging
import zlib
import struct
import time
import os
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
        
        # Nagłówki udające nowoczesną przeglądarkę Chrome
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.napiprojekt.pl/"
        }
        self.logger.info("NapiProjektKatalog initialized")

    # ───────────────────────────────────────── helpery
    def _decrypt(self, data: bytes) -> bytes:
        key = [0x5E,0x34,0x45,0x43,0x52,0x45,0x54,0x5F]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i%8]
            dec[i] = ((dec[i] << 4) & 0xFF) | (dec[i] >> 4)
        return bytes(dec)

    def _format_time(self, sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    # ───────────────────────────────────────── konwersja do SRT
    def _convert_simple_time_to_srt(self, txt: str) -> Optional[str]:
        line_pat = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s*:\s*(.*)$")
        items = []
        for ln in txt.splitlines():
            m = line_pat.match(ln)
            if not m:
                continue
            h, mnt, s, body = m.groups()
            start = int(h) * 3600 + int(mnt) * 60 + int(s)
            text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
            items.append((start, text))

        if len(items) < 2:
            return None

        srt = []
        for idx, (start, text) in enumerate(items):
            if not text:
                continue
            end = (items[idx + 1][0] - 0.01) if idx + 1 < len(items) else start + 3
            srt.append(f"{idx + 1}\n{self._format_time(start)} --> {self._format_time(end)}\n{text}\n")
        return "\n".join(srt)

    def _convert_microdvd_to_srt(self, txt: str, fps_default: float = 23.976) -> Optional[str]:
        if not txt or "-->" in txt:
            return txt

        if "{" not in txt and "[" not in txt:
            simple = self._convert_simple_time_to_srt(txt)
            if simple:
                return simple

        line_pat = re.compile(r"([{\[])(\d+)[}\]]([{\[])(\d+)[}\]](.*)")
        items: List[tuple] = []
        fps_header: Optional[float] = None

        for ln in txt.splitlines():
            m = line_pat.match(ln.strip())
            if not m:
                continue
            br, a_raw, _, b_raw, body = m.groups()
            a, b = int(a_raw), int(b_raw)

            if a == 0 and b == 0:
                m_fps = re.search(r"(\d+(?:\.\d+)?)", body)
                if m_fps:
                    try:
                        fps_header = float(m_fps.group(1))
                    except ValueError:
                        pass
                continue

            items.append((br, a, b, body))

        if not items:
            return None

        first_br = items[0][0]
        if first_br == "{":
            mode, fps = "frames", fps_header or fps_default
        else:
            if fps_header:
                mode, fps = "frames", fps_header
            else:
                mode, fps = "mpl2", None

        srt_lines = []
        idx = 1
        for _, a, b, body in items:
            text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
            if not text:
                continue
            t1 = self._format_time(a / fps) if mode == "frames" else self._format_time(a / 10)
            t2 = self._format_time(b / fps) if mode == "frames" else self._format_time(b / 10)
            srt_lines.append(f"{idx}\n{t1} --> {t2}\n{text}\n")
            idx += 1

        return "\n".join(srt_lines) if srt_lines else None

    # ───────────────────────────────────────── parse helpery
    def _parse_duration(self, txt: str) -> Optional[float]:
        if not txt: return None
        try:
            if txt.isdigit(): return int(txt) / 1000
            if "." in txt:
                t, frac = txt.split(".", 1)
                ms = int(frac[:3].ljust(3, "0"))
            else:
                t, ms = txt, 0
            parts = list(map(int, t.split(":")))
            h, m, s = (parts if len(parts) == 3 else [0] + parts)
            return h * 3600 + m * 60 + s + ms / 1000
        except Exception:
            return None

    def _extract_fps_from_label(self, lbl: str) -> Optional[float]:
        m = re.search(r"(\d+(?:\.\d+)?)\s*FPS", lbl, re.I)
        return float(m.group(1)) if m else None

    # ───────────────────────────────────────── DOWNLOAD
    def download(self, md5hash: str) -> Optional[str]:
        try:
            payload = {
                "mode": "17",
                "client": "NapiProjektPython",
                "downloaded_subtitles_id": md5hash,
                "downloaded_subtitles_lang": "PL",
                "downloaded_subtitles_txt": "1",
            }
            resp = requests.post(self.download_url, data=payload, headers=self.headers, timeout=15)
            if resp.status_code != 200: return None
            
            xml = minidom.parseString(resp.content)
            content = xml.getElementsByTagName("content")[0].firstChild.data
            bin_data = base64.b64decode(content)

            if bin_data.startswith(b"NP"):
                dec = self._decrypt(bin_data[4:])
                crc = struct.unpack("<I", dec[:4])[0]
                inner = dec[4:]
                if zlib.crc32(inner) & 0xFFFFFFFF != crc: return None
                raw = zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")
            else:
                try:
                    raw = bin_data.decode("utf-8")
                except UnicodeDecodeError:
                    raw = bin_data.decode("cp1250", "ignore")

            if "{" in raw or "[" in raw or re.search(r"^\s*\d{1,2}:\d{2}:\d{2}\s*:", raw, re.M):
                raw = self._convert_microdvd_to_srt(raw) or raw
            return raw
        except Exception as e:
            self.logger.error(f"Download err {md5hash}: {e}")
            return None

    # ───────────────────────────────────────── SEARCH
    def _build_detail_url(self, item, href):
        m = re.search(r"napisy-(\d+)-(.*)", href)
        if not m: return f"{self.base_url}{href}"
        nid, slug = m.groups()
        slug = re.sub(r"[-\s]*\(?\d{4}\)?$", "", slug).strip("-")
        base = f"{self.base_url}/napisy1,1,1-dla-{nid}-{slug}"
        if item.get("tvshow") and item.get("season") and item.get("episode"):
            return f"{base}-s{item['season'].zfill(2)}e{item['episode'].zfill(2)}"
        if item.get("year"):
            return f"{base}-({item['year']})"
        return base

    def _get_subtitles_from_detail(self, url: str) -> List[dict]:
        subs = []
        page = 1
        while True:
            pg = url.replace("napisy1,", f"napisy{page},")
            resp = requests.get(pg, headers=self.headers, timeout=15)
            if resp.status_code != 200: break
            
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("tbody > tr")
            if not rows: break
            
            for row in rows:
                a = row.find("a", href=re.compile(r"napiprojekt:"))
                if not a: continue
                cols = row.find_all("td")
                if len(cols) < 5: continue
                
                try:
                    dls_num = int(re.sub(r"[^\d]", "", cols[4].get_text(strip=True))) or 0
                except Exception:
                    dls_num = 0
                    
                subs.append({
                    "language": "pol",
                    "label": cols[1].get_text(strip=True),
                    "link_hash": a["href"].replace("napiprojekt:", ""),
                    "_duration": self._parse_duration(cols[3].get_text(strip=True)),
                    "_fps": self._extract_fps_from_label(cols[2].get_text(strip=True)),
                    "_downloads": dls_num,
                })
            page += 1
            if page > 5: break # Zabezpieczenie przed pętlą
            time.sleep(0.5)
        return subs

    def search(self, item: Dict[str, str], imdb_id: str, *_) -> List[dict]:
        try:
            payload = {
                "queryKind": "1" if item.get("tvshow") else "2",
                "queryString": (item.get("tvshow") or item.get("title") or imdb_id).lower(),
                "queryYear": item.get("year", ""),
                "associate": imdb_id,
            }
            
            resp = requests.post(self.search_url, data=payload, headers=self.headers, timeout=15)
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
                
                detail = self._build_detail_url(item, title_a["href"])
                if detail:
                    result.extend(self._get_subtitles_from_detail(detail))
            return result
        except Exception as e:
            self.logger.error(f"Search error: {e}")
            return []
