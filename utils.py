#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pomocnicze narzędzia konwersji napisów i parsowania tabel NapiProjekt.
"""

import re
import zlib
import struct
import base64
import unicodedata
from typing import List, Dict, Optional

# ───────────────────── normalizacja ─────────────────────────
def _norm(txt: str) -> str:
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode("ascii")

# ───────────────────── format czasu do SRT ───────────────────
def _fmt(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

# ───────────────────── HH:MM:SS:Text → SRT ───────────────────
def convert_timecoded(txt: str) -> str:
    pat = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s*:\s*(.*)$")
    rows = []
    for ln in txt.splitlines():
        m = pat.match(ln)
        if not m:
            continue
        h, mi, se, body = m.groups()
        start = int(h)*3600 + int(mi)*60 + int(se)
        rows.append((start, body))
    out = []
    for idx, (st, body) in enumerate(rows):
        end = rows[idx+1][0]-0.01 if idx+1 < len(rows) else st + 3
        text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
        out.append(f"{idx+1}\n{_fmt(st)} --> {_fmt(end)}\n{text}\n")
    return "".join(out)

# ───────────────────── MicroDVD {a}{b}Text → SRT ─────────────
def convert_microdvd(txt: str, fps: float = 23.976) -> str:
    pat = re.compile(r"[{](\d+)[}][{](\d+)[}](.*)")
    rows = []
    for ln in txt.splitlines():
        m = pat.match(ln.strip())
        if not m:
            continue
        a, b, body = int(m.group(1)), int(m.group(2)), m.group(3)
        rows.append((a, b, body))
    out = []
    for idx, (a, b, body) in enumerate(rows):
        t1 = _fmt(a / fps)
        t2 = _fmt(b / fps)
        text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
        out.append(f"{idx+1}\n{t1} --> {t2}\n{text}\n")
    return "".join(out)

# ───────────────────── MPL2 [a][b]Text → SRT ─────────────────
def convert_mpl2(txt: str) -> str:
    pat = re.compile(r"\[(\d+)\]\[(\d+)\](.*)")
    rows = []
    for ln in txt.splitlines():
        m = pat.match(ln.strip())
        if not m:
            continue
        a, b, body = int(m.group(1)), int(m.group(2)), m.group(3)
        rows.append((a, b, body))
    out = []
    for idx, (a, b, body) in enumerate(rows):
        t1 = _fmt(a / 10)
        t2 = _fmt(b / 10)
        text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
        out.append(f"{idx+1}\n{t1} --> {t2}\n{text}\n")
    return "".join(out)

# ───────────────────── parsowanie tabeli detail ──────────────
def parse_subtitles(rows) -> List[Dict]:
    """
    rows – lista <tr> z detail‑page.
    Zwraca listę słowników: language, label, link_hash, _downloads.
    """
    out, seen = [], set()
    for r in rows:
        a = r.find('a', href=re.compile(r'napiprojekt:'))
        if not a:
            continue
        h = a['href'].replace('napiprojekt:', '')
        if h in seen:
            continue
        seen.add(h)
        cols = r.find_all('td')
        if len(cols) < 5:
            continue
        label = cols[1].get_text(strip=True)
        try:
            dls = int(re.sub(r"[^\d]", "", cols[4].get_text(strip=True))) or 0
        except ValueError:
            dls = 0
        out.append({
            'language': 'pol',
            'label': label,
            'link_hash': h,
            '_downloads': dls
        })
    return out
