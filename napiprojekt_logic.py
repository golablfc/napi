#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import urllib.request, urllib.parse, re, base64, logging, zlib, struct, time, unicodedata
from xml.dom import minidom
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

MAX_PAGES  = 4      # maks. liczba stron tabeli
PAGE_DELAY = 0.1    # pauza między stronami (sek.)

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
        self.search_url   = "https://www.napiprojekt.pl/ajax/search_catalog.php"
        self.base_url     = "https://www.napiprojekt.pl"
        self.logger.info("NapiProjektKatalog initialized")

    # ───────── (helpery decrypt / konwersje / download – bez zmian) ──────────
    def _decrypt(self, b):                # … pozostałe funkcje niezmienione …
        key=[0x5E,0x34,0x45,0x43,0x52,0x45,0x54,0x5F]; d=bytearray(b)
        for i in range(len(d)):
            d[i]^=key[i%8]; d[i]=((d[i]<<4)&0xFF)|(d[i]>>4)
        return bytes(d)

    def _format_time(self, s):            # …
        h=int(s//3600); m=int(s%3600//60); ss=int(s%60); ms=int(round((s-int(s))*1000))
        return f"{h:02d}:{m:02d}:{ss:02d},{ms:03d}"

    def _convert_simple_time_to_srt(self, txt):   # …
        pat=re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})\s*:\s*(.*)$"); items=[]
        for ln in txt.splitlines():
            m=pat.match(ln); 
            if not m: continue
            h,mi,se,body=m.groups()
            start=int(h)*3600+int(mi)*60+int(se)
            text="\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
            items.append((start,text))
        if len(items)<2: return None
        out=[]
        for i,(st,tx) in enumerate(items):
            if not tx: continue
            end=(items[i+1][0]-0.01) if i+1<len(items) else st+3
            out.append(f"{i+1}\n{self._format_time(st)} --> {self._format_time(end)}\n{tx}\n")
        return "".join(out)

    def _convert_microdvd_to_srt(self, txt, fps_default=23.976):  # …
        if not txt or "-->" in txt: return txt
        if "{" not in txt and "[" not in txt:
            return self._convert_simple_time_to_srt(txt)
        pat=re.compile(r"([{\[])(\d+)[}\]]([{\[])(\d+)[}\]](.*)")
        items=[]; fps_hdr=None
        for ln in txt.splitlines():
            m=pat.match(ln.strip())
            if not m: continue
            br,a,_,b,body=m.groups(); a=int(a); b=int(b)
            if a==0 and b==0:
                mf=re.search(r"(\d+(?:\.\d+)?)",body)
                if mf:
                    try: fps_hdr=float(mf.group(1))
                    except: pass
                continue
            items.append((br,a,b,body))
        if not items: return None
        first=items[0][0]
        if first=="{": mode="frames"; fps=fps_hdr or fps_default
        else:
            if fps_hdr: mode="frames"; fps=fps_hdr
            else: mode="mpl2"; fps=None
        out=[]; idx=1
        for _,a,b,body in items:
            text="\n".join(seg.lstrip('/').strip() for seg in body.split('|') if seg.strip())
            if not text: continue
            if mode=="frames":
                t1=self._format_time(a/fps); t2=self._format_time(b/fps)
            else:
                t1=self._format_time(a/10);  t2=self._format_time(b/10)
            out.append(f"{idx}\n{t1} --> {t2}\n{text}\n"); idx+=1
        return "".join(out) if out else None

    def _parse_duration(self, t):         # …
        if not t: return None
        try:
            if t.isdigit(): return int(t)/1000
            if "." in t: hms,frac=t.split(".",1); ms=int(frac[:3].ljust(3,"0"))
            else: hms,ms=t,0
            parts=list(map(int,hms.split(":")))
            if len(parts)==3: h,m,s=parts
            elif len(parts)==2: h,m,s=0,*parts
            else: return None
            return h*3600+m*60+s+ms/1000
        except: return None

    def _extract_fps_from_label(self,lbl):
        m=re.search(r"(\d+(?:\.\d+)?)\s*FPS",lbl,re.I)
        return float(m.group(1)) if m else None

    def download(self, md5):
        try:
            data=urllib.parse.urlencode({
                "mode":"17","client":"NapiProjektPython",
                "downloaded_subtitles_id":md5,
                "downloaded_subtitles_lang":"PL",
                "downloaded_subtitles_txt":"1"}).encode()
            req=urllib.request.Request(self.download_url,data=data,
                                       headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=15) as r:
                if r.status!=200: return None
                xml=minidom.parseString(r.read())
            content=xml.getElementsByTagName("content")[0].firstChild.data
            bin=base64.b64decode(content)
            if bin.startswith(b"NP"):
                dec=self._decrypt(bin[4:]); crc=struct.unpack("<I",dec[:4])[0]; inner=dec[4:]
                if zlib.crc32(inner)&0xFFFFFFFF!=crc: return None
                raw=zlib.decompress(inner,-zlib.MAX_WBITS).decode("utf-8","ignore")
            else:
                try: raw=bin.decode("utf-8")
                except UnicodeDecodeError: raw=bin.decode("cp1250","ignore")
            if "{" in raw or "[" in raw or re.search(r"^\s*\d{1,2}:\d{2}:\d{2}\s*:",raw,re.M):
                raw=self._convert_microdvd_to_srt(raw) or raw
            return raw
        except Exception as e:
            self.logger.error(f"Download err {md5}: {e}")
            return None

    # ───────────────────────── wyszukiwanie i filtry ────────────
    def _normalize(self,t): return unicodedata.normalize("NFKD",t).encode("ascii","ignore").decode("ascii").lower()
    def _is_episode_match(self,blk,s,e):
        if not s or not e: return False
        s,e=s.zfill(2),e.zfill(2)
        return bool(re.search(fr"s{s}e{e}|{s}x{e}",self._normalize(blk.get_text(" ",strip=True)),re.I))

    def _build_detail_url(self,item,href):
        m=re.search(r"napisy-(\d+)-(.*)",href)
        if not m: return urllib.parse.urljoin(self.base_url,href)
        nid,slug=m.groups(); base=f"{self.base_url}/napisy1,1,1-dla-{nid}-{slug}"
        if item.get("tvshow") and item.get("season") and item.get("episode"):
            s=item["season"].zfill(2); e=item["episode"].zfill(2)
            if item.get("year") and not re.search(r"\(\d{4}\)",base): base=f"{base}-({item['year']})"
            if not re.search(rf"-s{s}e{e}$",base,re.I): base=f"{base}-s{s}e{e}"
        elif item.get("year") and not re.search(r"\(\d{4}\)",base):
            base=f"{base}-({item['year']})"
        return base

    def _get_subtitles_from_detail(self,url):
        subs=[]; seen=set(); pat=re.compile(r"napisy\d+,")
        for page in range(1,MAX_PAGES+1):
            pg=pat.sub(f"napisy{page},",url,1)
            try:
                req=urllib.request.Request(pg,headers={'User-Agent':'Mozilla/5.0'})
                with urllib.request.urlopen(req,timeout=15) as r: html=r.read()
            except: break
            trs=BeautifulSoup(html,'lxml').select("tbody > tr")
            if not trs: break
            for tr in trs:
                a=tr.find('a',href=re.compile(r'napiprojekt:')); 
                if not a: continue
                h=a['href'].replace('napiprojekt:','')
                if h in seen: continue
                seen.add(h)
                tds=tr.find_all('td')
                if len(tds)<5: continue
                try: dls=int(re.sub(r"[^\d]","",tds[4].get_text(strip=True))) or 0
                except: dls=0
                subs.append({
                    'language':'pol',
                    'label':tds[1].get_text(strip=True),
                    'link_hash':h,
                    '_duration':self._parse_duration(tds[3].get_text(strip=True)),
                    '_fps':self._extract_fps_from_label(tds[2].get_text(strip=True)),
                    '_downloads':dls})
                if len(subs)>=100: return subs
            time.sleep(PAGE_DELAY)
        return subs

    def _fetch_search_html(self, data:bytes)->str:
        try:
            req=urllib.request.Request(self.search_url,data=data,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=15) as r:
                return r.read().decode("utf-8","ignore")
        except Exception as e:
            self.logger.debug(f"search_catalog request error: {e}")
            return ""

    # ───────────────────────── MAIN SEARCH ──────────────────────
    def search(self,item:Dict[str,str],imdb_id:str,*_):
        try:
            q=(item.get("tvshow") or item.get("title") or imdb_id).lower()
            htmls=[]
            if imdb_id: htmls.append(self._fetch_search_html(
                urllib.parse.urlencode({"queryKind":"1","queryString":q,
                "queryYear":item.get("year",""),"associate":imdb_id}).encode()))
            htmls.append(self._fetch_search_html(
                urllib.parse.urlencode({"queryKind":"1","queryString":q,
                "queryYear":item.get("year",""),"associate":""}).encode()))
            htmls.append(self._fetch_search_html(
                urllib.parse.urlencode({"queryKind":"2","queryString":q,
                "queryYear":item.get("year","")}).encode()))

            blocks=[]
            for h in htmls:
                blocks=BeautifulSoup(h,'lxml').find_all("div",class_="movieSearchContent")
                self.logger.debug(f"SEARCH blocks: {len(blocks)}")
                if blocks: break
            if not blocks: return []

            eps=[b for b in blocks if self._is_episode_match(
                b,item.get("season"),item.get("episode"))]
            sel=eps if eps else blocks

            out=[]
            for blk in sel:
                a=blk.find("a",href=re.compile(r"imdb.com/title/(tt\d+)"))
                if imdb_id:
                    if not a: continue        # ← NOWE: wymagamy linku IMDb
                    if imdb_id not in a["href"]: continue
                detail=self._build_detail_url(item,blk.find("a",class_="movieTitleCat")["href"])
                self.logger.debug(f"DETAIL try: {detail}")
                subs=self._get_subtitles_from_detail(detail)
                if not subs and item.get("season"):
                    s_url=re.sub(r"-s\d{2}e\d{2}$",
                                 f"-s{item['season'].zfill(2)}",detail,flags=re.I)
                    subs=self._get_subtitles_from_detail(s_url)
                if not subs:
                    base=re.sub(r"-s\d{2}(e\d{2})?$","",detail,flags=re.I)
                    subs=self._get_subtitles_from_detail(base)
                out.extend(subs)

            out.sort(key=lambda s:s.get('_downloads',0),reverse=True)
            return out[:100]
        except Exception as e:
            self.logger.error(f"Search error: {e}")
            return []
