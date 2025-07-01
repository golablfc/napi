#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import urllib.request, urllib.parse, re, base64, logging, zlib, struct, time, unicodedata
from xml.dom import minidom
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

MAX_PAGES  = 4
PAGE_DELAY = 0.1

class NapiProjektKatalog:
    def __init__(self):
        self.logger = logging.getLogger("NapiProjekt")
        self.logger.setLevel(logging.DEBUG)
        if not self.logger.handlers:
            h = logging.StreamHandler()
            h.setLevel(logging.DEBUG)
            h.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(h)
        self.download_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.search_url = "https://www.napiprojekt.pl/ajax/search_catalog.php"
        self.base_url = "https://www.napiprojekt.pl"
        self.logger.info("NapiProjektKatalog initialized")

    @staticmethod
    def _norm(txt: str) -> str:
        return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii").lower()

    def _canon(self, txt: str) -> str:
        return re.sub(r"[^a-z0-9]", "", self._norm(txt))

    def _title_eq(self, hdr: str, wanted: str) -> bool:
        return self._canon(hdr).startswith(self._canon(wanted))

    @staticmethod
    def _decrypt(b: bytes) -> bytes:
        key = [0x5E,0x34,0x45,0x43,0x52,0x45,0x54,0x5F]
        d = bytearray(b)
        for i in range(len(d)):
            d[i] ^= key[i % 8]
            d[i] = ((d[i] << 4) & 0xFF) | (d[i] >> 4)
        return bytes(d)

    @staticmethod
    def _format_time(sec: float) -> str:
        h = int(sec // 3600)
        m = int(sec % 3600 // 60)
        s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def _convert_simple_time_to_srt(self, txt: str) -> Optional[str]:
        pat = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s*:\s*(.*)$")
        items = []
        for ln in txt.splitlines():
            m = pat.match(ln)
            if not m: continue
            h, mi, se, body = m.groups()
            start = int(h)*3600 + int(mi)*60 + int(se)
            text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
            items.append((start, text))
        if len(items) < 2: return None
        out = []
        for idx, (st, tx) in enumerate(items):
            if not tx: continue
            end = (items[idx+1][0] - 0.01) if idx + 1 < len(items) else st + 3
            out.append(f"{idx+1}\n{self._format_time(st)} --> {self._format_time(end)}\n{tx}\n")
        return "".join(out)

    def _convert_microdvd_to_srt(self, txt: str, fps_default: float = 23.976) -> Optional[str]:
        if not txt or "-->" in txt: return txt
        if "{" not in txt and "[" not in txt:
            return self._convert_simple_time_to_srt(txt)
        pat = re.compile(r"([{\[])(\d+)[}\]]([{\[])(\d+)[}\]](.*)")
        items = []
        fps_hdr = None
        for ln in txt.splitlines():
            m = pat.match(ln.strip())
            if not m: continue
            br, a_raw, _, b_raw, body = m.groups()
            a, b = int(a_raw), int(b_raw)
            if a == 0 and b == 0:
                mf = re.search(r"(\d+(?:\.\d+)?)", body)
                if mf:
                    try: fps_hdr = float(mf.group(1))
                    except: pass
                continue
            items.append((br, a, b, body))
        if not items: return None
        first = items[0][0]
        if first == "{": mode, fps = "frames", fps_hdr or fps_default
        else: mode, fps = ("frames", fps_hdr) if fps_hdr else ("mpl2", None)
        out = []; idx = 1
        for _, a, b, body in items:
            text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
            if not text: continue
            if mode == "frames":
                t1 = self._format_time(a / fps)
                t2 = self._format_time(b / fps)
            else:
                t1 = self._format_time(a / 10)
                t2 = self._format_time(b / 10)
            out.append(f"{idx}\n{t1} --> {t2}\n{text}\n")
            idx += 1
        return "".join(out) if out else None

    @staticmethod
    def _parse_duration(txt: str) -> Optional[float]:
        if not txt: return None
        try:
            if txt.isdigit(): return int(txt) / 1000
            if "." in txt:
                t, frac = txt.split(".", 1)
                ms = int(frac[:3].ljust(3, "0"))
            else:
                t, ms = txt, 0
            parts = list(map(int, t.split(":")))
            if len(parts) == 3: h, m, s = parts
            elif len(parts) == 2: h, m, s = 0, *parts
            else: return None
            return h*3600 + m*60 + s + ms/1000
        except: return None

    @staticmethod
    def _extract_fps_from_label(lbl: str) -> Optional[float]:
        m = re.search(r"(\d+(?:\.\d+)?)\s*FPS", lbl, re.I)
        return float(m.group(1)) if m else None

    def download(self, md5hash: str) -> Optional[str]:
        try:
            data = urllib.parse.urlencode({
                "mode": "17", "client": "NapiProjektPython",
                "downloaded_subtitles_id": md5hash,
                "downloaded_subtitles_lang": "PL",
                "downloaded_subtitles_txt": "1"
            }).encode("utf-8")
            req = urllib.request.Request(self.download_url, data=data,
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                xml = minidom.parseString(resp.read())
            content = xml.getElementsByTagName("content")[0].firstChild.data
            bin_data = base64.b64decode(content)
            if bin_data.startswith(b"NP"):
                dec = self._decrypt(bin_data[4:])
                crc = struct.unpack("<I", dec[:4])[0]
                inner = dec[4:]
                if zlib.crc32(inner) & 0xFFFFFFFF != crc: return None
                raw = zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")
            else:
                try: raw = bin_data.decode("utf-8")
                except UnicodeDecodeError: raw = bin_data.decode("cp1250", "ignore")
            if "{" in raw or "[" in raw or re.search(r"^\s*\d{1,2}:\d{2}:\d{2}\s*:", raw, re.M):
                raw = self._convert_microdvd_to_srt(raw) or raw
            return raw
        except Exception as e:
            self.logger.error(f"Download err {md5hash}: {e}")
            return None

    def _is_episode_match(self, blk, season: str, episode: str) -> bool:
        if not season or not episode: return False
        s, e = season.zfill(2), episode.zfill(2)
        return bool(re.search(fr"s{s}e{e}|{s}x{e}", self._norm(blk.get_text(" ", strip=True)), re.I))

    def _build_detail_url(self, item: Dict[str, str], href: str) -> str:
        m = re.search(r"napisy-(\d+)-(.*)", href)
        if not m:
            return urllib.parse.urljoin(self.base_url, href)
        nid, slug = m.groups()
        base = f"{self.base_url}/napisy1,1,1-dla-{nid}-{slug}"
        if item.get("tvshow") and item.get("season") and item.get("episode"):
            s = item["season"].zfill(2)
            e = item["episode"].zfill(2)
            if item.get("year") and not re.search(r"\(\d{4}\)", base):
                base = f"{base}-({item['year']})"
            if not re.search(rf"-s{s}e{e}$", base, re.I):
                base = f"{base}-s{s}e{e}"
        elif item.get("year") and not re.search(r"\(\d{4}\)", base):
            base = f"{base}-({item['year']})"
        return base

    def _get_subtitles_from_detail(self, url: str) -> List[dict]:
        subs = []
        seen = set()
        pattern = re.compile(r"napisy\d+,")
        for pg in range(1, MAX_PAGES+1):
            pgurl = pattern.sub(f"napisy{pg},", url, 1)
            try:
                req = urllib.request.Request(pgurl, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    html = resp.read()
            except: break
            rows = BeautifulSoup(html, 'lxml').select("tbody > tr")
            if not rows: break
            for r in rows:
                a = r.find('a', href=re.compile(r'napiprojekt:'))
                if not a: continue
                h = a['href'].replace('napiprojekt:', '')
                if h in seen: continue
                seen.add(h)
                cols = r.find_all('td')
                if len(cols) < 5: continue
                try:
                    dls = int(re.sub(r"[^\d]", "", cols[4].get_text(strip=True))) or 0
                except:
                    dls = 0
                subs.append({
                    'language': 'pol',
                    'label': cols[1].get_text(strip=True),
                    'link_hash': h,
                    '_duration': self._parse_duration(cols[3].get_text(strip=True)),
                    '_fps': self._extract_fps_from_label(cols[2].get_text(strip=True)),
                    '_downloads': dls
                })
                if len(subs) >= 100: return subs
            time.sleep(PAGE_DELAY)
        return subs

    def _fetch_search_html(self, data: bytes) -> str:
        try:
            req = urllib.request.Request(self.search_url, data=data,
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as e:
            self.logger.debug(f"search_catalog request error: {e}")
            return ""

    def _block_ok(self, blk, title_pl: str, season: str, episode: str) -> bool:
        hdr = blk.find("a", class_="movieTitleCat")
        if not hdr: return False
        hdr_txt = hdr.get_text(" ", strip=True)
        if not self._title_eq(hdr_txt, title_pl):
            self.logger.debug(f"  ✗ tytuł nie pasuje: «{hdr_txt}» ≠ «{title_pl}»")
            return False
        if season and episode and not self._is_episode_match(blk, season, episode):
            self.logger.debug(f"  ✗ sezon/ep odrzucony")
            return False
        self.logger.debug(f"  ✓ blok przyjęty «{hdr_txt}»")
        return True

    def search(self, item: Dict[str, str], imdb_id: str, *_):
        try:
            q = (item.get("tvshow") or item.get("title") or imdb_id).lower()
            htmls = [
                self._fetch_search_html(urllib.parse.urlencode({
                    "queryKind": "1", "queryString": q, "queryYear": item.get("year", ""), "associate": imdb_id}).encode()),
                self._fetch_search_html(urllib.parse.urlencode({
                    "queryKind": "1", "queryString": q, "queryYear": item.get("year", ""), "associate": ""}).encode()),
                self._fetch_search_html(urllib.parse.urlencode({
                    "queryKind": "2", "queryString": q, "queryYear": item.get("year", "")}).encode())
            ]
            blocks = []
            for h in htmls:
                blocks = BeautifulSoup(h, 'lxml').find_all("div", class_="movieSearchContent")
                self.logger.debug(f"SEARCH blocks: {len(blocks)}")
                if blocks: break
            if not blocks: return []
            title_pl = item.get("title") or item.get("tvshow") or ""
            season = item.get("season")
            episode = item.get("episode")
            filtered = [b for b in blocks if self._block_ok(b, title_pl, season, episode)]
            if not filtered:
                self.logger.debug("No blocks matched title/episode – 0 results")
                return []
            out = []
            for blk in filtered:
                detail = self._build_detail_url(item, blk.find("a", class_="movieTitleCat")["href"])
                self.logger.debug(f"DETAIL try: {detail}")
                subs = self._get_subtitles_from_detail(detail)
                if not subs and season:
                    subs = self._get_subtitles_from_detail(re.sub(r"-s\d{2}e\d{2}$", f"-s{season.zfill(2)}", detail, flags=re.I))
                if not subs:
                    subs = self._get_subtitles_from_detail(re.sub(r"-s\d{2}(e\d{2})?$", "", detail, flags=re.I))
                out.extend(subs)
            out.sort(key=lambda s: s.get('_downloads', 0), reverse=True)
            return out[:100]
        except Exception as e:
            self.logger.error(f"Search error: {e}")
            return []
