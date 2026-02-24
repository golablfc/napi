#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pomocnicze narzędzia konwersji napisów i pobierania informacji o mediach.
"""

import re
import requests
import logging
import unicodedata
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ───────────────────── Pobieranie Info z Cinemeta ───────────
def get_movie_info(imdb_id: str) -> Dict:
    """
    Pobiera tytuł i rok filmu z API Cinemeta dla Stremio.
    Dzięki temu wiemy, czego szukać w NapiProjekt.
    """
    try:
        # Próbujemy jako film
        url = f"https://v3-cinemeta.strem.io/meta/movie/{imdb_id}.json"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            m = r.json().get('meta', {})
            return {'title': m.get('name'), 'year': m.get('year'), 'type': 'movie'}
        
        # Jeśli nie, próbujemy jako serial
        url = f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            m = r.json().get('meta', {})
            return {'title': m.get('name'), 'year': m.get('year'), 'type': 'series'}
            
    except Exception as e:
        logger.error(f"Cinemeta error for {imdb_id}: {e}")
    
    return {'title': '', 'year': '', 'type': 'movie'}

# ───────────────────── Automatyczna konwersja do SRT ────────
def auto_convert_to_srt(txt: str) -> str:
    """
    Wykrywa format (MicroDVD, MPL2, Timecoded) i konwertuje na SRT.
    Jeśli to już jest SRT, zwraca oryginał.
    """
    if not txt: return ""
    
    # MicroDVD: {100}{200}Tekst
    if re.search(r"^\{(\d+)\}\{(\d+)\}", txt, re.M):
        logger.info("Format MicroDVD detected - converting...")
        return convert_microdvd(txt)
    
    # MPL2: [100][200]Tekst
    if re.search(r"^\[(\d+)\]\[(\d+)\]", txt, re.M):
        logger.info("Format MPL2 detected - converting...")
        return convert_mpl2(txt)
    
    # Timecoded: 00:00:00:Tekst
    if re.search(r"^\d{1,2}:\d{2}:\d{2}\s*:", txt, re.M):
        logger.info("Format Timecoded detected - converting...")
        return convert_timecoded(txt)
        
    return txt

# ───────────────────── format czasu do SRT ───────────────────
def _fmt(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

# ───────────────────── HH:MM:SS:Text → SRT ───────────────────
def convert_timecoded(txt: str) -> str:
    pat = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s*:\s*(.*)$")
    rows = []
    for ln in txt.splitlines():
        m = pat.match(ln)
        if not m: continue
        h, mi, se, body = m.groups()
        start = int(h)*3600 + int(mi)*60 + int(se)
        rows.append((start, body))
    
    out = []
    for idx, (st, body) in enumerate(rows):
        end = rows[idx+1][0]-0.01 if idx+1 < len(rows) else st + 3
        text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
        out.append(f"{idx+1}\n{_fmt(st)} --> {_fmt(end)}\n{text}\n")
    return "".join(out)

# ───────────────────── MicroDVD {a}{b}Text → SRT ─────────────
def convert_microdvd(txt: str, fps: float = 23.976) -> str:
    pat = re.compile(r"[{](\d+)[}][{](\d+)[}](.*)")
    rows = []
    for ln in txt.splitlines():
        m = pat.match(ln.strip())
        if not m: continue
        a, b, body = int(m.group(1)), int(m.group(2)), m.group(3)
        rows.append((a, b, body))
    
    out = []
    for idx, (a, b, body) in enumerate(rows):
        t1 = _fmt(a / fps)
        t2 = _fmt(b / fps)
        text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
        out.append(f"{idx+1}\n{t1} --> {t2}\n{text}\n")
    return "".join(out)

# ───────────────────── MPL2 [a][b]Text → SRT ─────────────────
def convert_mpl2(txt: str) -> str:
    pat = re.compile(r"\[(\d+)\]\[(\d+)\](.*)")
    rows = []
    for ln in txt.splitlines():
        m = pat.match(ln.strip())
        if not m: continue
        a, b, body = int(m.group(1)), int(m.group(2)), m.group(3)
        rows.append((a, b, body))
    
    out = []
    for idx, (a, b, body) in enumerate(rows):
        t1 = _fmt(a / 10)
        t2 = _fmt(b / 10)
        text = "\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
        out.append(f"{idx+1}\n{t1} --> {t2}\n{text}\n")
    return "".join(out)
