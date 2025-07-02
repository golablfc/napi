#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import urllib.request, urllib.parse, re, base64, logging, zlib, struct, time, os
from xml.dom import minidom
from bs4 import BeautifulSoup
from typing import List, Dict, Optional


class NapiProjektKatalog:
    def __init__(self):
        self.logger = logging.getLogger("NapiProjekt")
        self.download_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.search_url   = "https://www.napiprojekt.pl/ajax/search_catalog.php"
        self.base_url     = "https://www.napiprojekt.pl"
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

    # ───────────────────────────────────────── simple HH:MM:SS:TEXT → SRT
    def _convert_simple_time_to_srt(self, txt: str) -> Optional[str]:
        """
        Format linii: HH:MM:SS:Tekst   (np. 00:03:06:Szybciej...)
        • Start = podany czas.
        • Koniec = początek kolejnej linii - 0.01 s
        • Ostatnia linia = +3 s
        """
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
            return None  # nie wygląda na ten format

        srt = []
        for idx, (start, text) in enumerate(items):
            if not text:
                continue
            end = (items[idx + 1][0] - 0.01) if idx + 1 < len(items) else start + 3
            srt.append(
                f"{idx + 1}\n"
                f"{self._format_time(start)} --> {self._format_time(end)}\n"
                f"{text}\n"
            )
        return "\n".join(srt)

    # ───────────────────────────────────────── MicroDVD / MPL2 → SRT
    def _convert_microdvd_to_srt(
        self,
        txt: str,
        fps_default: float = 23.976,
    ) -> Optional[str]:
        """
        • {start}{end}  → MicroDVD  (klatki)   — FPS z nagłówka {0}{0}xx.xx lub domyślnie 23.976
        • [start][end]
            – z nagłówkiem [0][0]xx.xx         → MicroDVD‑square (klatki)
            – bez nagłówka                    → MPL2 (dziesiąte sekundy)
        • Jeśli brak { } i [ ], próbujemy HH:MM:SS:Tekst
        """

        if not txt or "-->" in txt:
            return txt  # już SRT lub puste

        # ─── 1) Simple time format?
        if "{" not in txt and "[" not in txt:
            simple = self._convert_simple_time_to_srt(txt)
            if simple:
                return simple  # skonwertowany prosty format

        # ─── 2) MicroDVD / MPL2
        line_pat = re.compile(r"([{\[])(\d+)[}\]]([{\[])(\d+)[}\]](.*)")
        items: List[tuple] = []        # (bracket, a, b, body)
        fps_header: Optional[float] = None

        for ln in txt.splitlines():
            m = line_pat.match(ln.strip())
            if not m:
                continue
            br, a_raw, _, b_raw, body = m.groups()
            a, b = int(a_raw), int(b_raw)

            # Nagłówek FPS {0}{0}xx.xx lub [0][0]xx.xx
            if a == 0 and b == 0:
                m_fps = re.search(r"(\d+(?:\.\d+)?)", body)
                if m_fps:
                    try:
                        fps_header = float(m_fps.group(1))
                        self.logger.debug(f"FPS header detected: {fps_header}")
                    except ValueError:
                        pass
                continue

            items.append((br, a, b, body))

        if not items:
            return None

        first_br = items[0][0]

        # Ustalenie trybu
        if first_br == "{":
            mode = "frames"
            fps  = fps_header or fps_default
        else:  # '['
            if fps_header:
                mode = "frames"
                fps  = fps_header
            else:
                mode = "mpl2"       # wartości w 0.1 s
                fps  = None

        self.logger.debug(f"Mode: {mode}, FPS: {fps}")

        # Konwersja
        srt_lines = []
        idx = 1
        for _, a, b, body in items:
            text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
            if not text:
                continue

            if mode == "frames":
                t1 = self._format_time(a / fps)
                t2 = self._format_time(b / fps)
            else:            # MPL2: dziesiąte sekundy
                t1 = self._format_time(a / 10)
                t2 = self._format_time(b / 10)

            srt_lines.append(f"{idx}\n{t1} --> {t2}\n{text}\n")
            idx += 1

        return "\n".join(srt_lines) if srt_lines else None

    # ───────────────────────────────────────── parse helpery
    def _parse_duration(self, txt: str) -> Optional[float]:
        if not txt:
            return None
        try:
            if txt.isdigit():
                return int(txt) / 1000
            if "." in txt:
                t, frac = txt.split(".", 1)
                ms = int(frac[:3].ljust(3, "0"))
            else:
                t, ms = txt, 0
            parts = list(map(int, t.split(":")))
            if len(parts) == 3:
                h, m, s = parts
            elif len(parts) == 2:
                h, m, s = 0, *parts
            else:
                return None
            return h * 3600 + m * 60 + s + ms / 1000
        except Exception:
            return None

    def _extract_fps_from_label(self, lbl: str) -> Optional[float]:
        m = re.search(r"(\d+(?:\.\d+)?)\s*FPS", lbl, re.I)
        return float(m.group(1)) if m else None

    # ───────────────────────────────────────── DOWNLOAD
    def download(self, md5hash: str) -> Optional[str]:
        try:
            data = urllib.parse.urlencode(
                {
                    "mode": "17",
                    "client": "NapiProjektPython",
                    "downloaded_subtitles_id": md5hash,
                    "downloaded_subtitles_lang": "PL",
                    "downloaded_subtitles_txt": "1",
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                self.download_url, data=data, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    return None
                xml = minidom.parseString(resp.read())

            content = xml.getElementsByTagName("content")[0].firstChild.data
            bin_data = base64.b64decode(content)

            if bin_data.startswith(b"NP"):
                dec = self._decrypt(bin_data[4:])
                crc = struct.unpack("<I", dec[:4])[0]
                inner = dec[4:]
                if zlib.crc32(inner) & 0xFFFFFFFF != crc:
                    return None
                raw = zlib.decompress(inner, -zlib.MAX_WBITS).decode("utf-8", "ignore")
            else:
                try:
                    raw = bin_data.decode("utf-8")
                except UnicodeDecodeError:
                    raw = bin_data.decode("cp1250", "ignore")

            # debug raw
            try:
                os.makedirs("debug_raw", exist_ok=True)
                with open(
                    f"debug_raw/debug_{md5hash}.txt",
                    "w",
                    encoding="utf-8",
                    errors="ignore",
                ) as dbg:
                    dbg.write(raw)
            except Exception:
                pass

            # konwersja
            if "{" in raw or "[" in raw or re.search(r"^\s*\d{1,2}:\d{2}:\d{2}\s*:", raw, re.M):
                raw = self._convert_microdvd_to_srt(raw) or raw
            return raw
        except Exception as e:
            self.logger.error(f"Download err {md5hash}: {e}")
            return None

    # ───────────────────────────────────────── SEARCH (bez zmian)
    def _build_detail_url(self, item, href):
        m = re.search(r"napisy-(\d+)-(.*)", href)
        if not m:
            return urllib.parse.urljoin(self.base_url, href)
        nid, slug = m.groups()
        slug = re.sub(r"[-\s]*\(?\d{4}\)?$", "", slug).strip("-")
        base = f"{self.base_url}/napisy1,1,1-dla-{nid}-{slug}"
        if item.get("tvshow") and item.get("season") and item.get("episode"):
            s = item["season"].zfill(2)
            e = item["episode"].zfill(2)
            return f"{base}-s{s}e{e}"
        if item.get("year"):
            return f"{base}-({item['year']})"
        return base

    def _get_subtitles_from_detail(self, url: str) -> List[dict]:
        subs = []
        page = 1
        while True:
            pg = url.replace("napisy1,", f"napisy{page},")
            req = urllib.request.Request(pg, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                soup = BeautifulSoup(resp.read(), "lxml")
            rows = soup.select("tbody > tr")
            if not rows:
                break
            for row in rows:
                a = row.find("a", href=re.compile(r"napiprojekt:"))
                if not a:
                    continue
                cols = row.find_all("td")
                if len(cols) < 5:
                    continue
                duration = cols[3].get_text(strip=True)
                dls_txt = cols[4].get_text(strip=True)
                try:
                    dls_num = int(re.sub(r"[^\d]", "", dls_txt)) or 0
                except Exception:
                    dls_num = 0
                subs.append(
                    {
                        "language": "pol",
                        "label": cols[1].get_text(strip=True),
                        "link_hash": a["href"].replace("napiprojekt:", ""),
                        "_duration": self._parse_duration(duration),
                        "_fps": self._extract_fps_from_label(cols[2].get_text(strip=True)),
                        "_downloads": dls_num,
                    }
                )
            page += 1
            time.sleep(1)
        return subs

    def search(self, item: Dict[str, str], imdb_id: str, *_) -> List[dict]:
        try:
            q_kind = "1" if item.get("tvshow") else "2"
            q_str = (item.get("tvshow") or item.get("title") or imdb_id).lower()
            q_year = item.get("year", "")
            post = urllib.parse.urlencode(
                {
                    "queryKind": q_kind,
                    "queryString": q_str,
                    "queryYear": q_year,
                    "associate": imdb_id,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                self.search_url, data=post, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8")
            soup = BeautifulSoup(html, "lxml")
            blocks = soup.find_all("div", class_="movieSearchContent")
            result = []
            for blk in blocks:
                a = blk.find("a", href=re.compile(r"imdb.com/title/(tt\d+)"))
                if not a or imdb_id not in a["href"]:
                    continue
                title_a = blk.find("a", class_="movieTitleCat")
                if not title_a:
                    continue
                detail = self._build_detail_url(item, title_a["href"])
                if detail:
                    result.extend(self._get_subtitles_from_detail(detail))
            return result
        except Exception as e:
            self.logger.error(f"Search error: {e}")
            return []
