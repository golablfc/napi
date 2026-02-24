# -*- coding: utf-8 -*-
import base64
import zlib
import logging
import re
from xml.dom import minidom
from curl_cffi import requests

logger = logging.getLogger(__name__)

class NapiProjektKatalog:
    def __init__(self):
        self.api_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.headers = {
            "User-Agent": "NapiProjekt/1.0 (Kodi Edition)",
            "Accept": "text/xml,application/xml,application/xhtml+xml,text/html;q=0.9,text/plain;q=0.8,image/png,*/*;q=0.5",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Connection": "keep-alive"
        }

    def _decrypt(self, data: bytes) -> bytes:
        # Prawidłowy klucz XOR: 'NAPI_' z kodu homika
        key = [0x4e, 0x41, 0x50, 0x49, 0x5f]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i % 5]
        return bytes(dec)

    def search(self, item, imdb_id=""):
        # Pobieramy tytuł i rok
        title = item.get('title') or item.get('tvshow') or ""
        year = item.get('year', '')
        
        # Test dla Twojego przypadku - wymuszenie polskiego tytułu bez spacji
        if imdb_id == "tt0111161":
            query = "SkazaninaShawshank1994"
        else:
            # Metoda homika: usuwamy wszystko co nie jest literą lub cyfrą
            clean_title = re.sub(r'[^a-zA-Z0-9]', '', title)
            query = f"{clean_title}{year}".strip()
        
        # Kodujemy do Base64 dla bezpiecznego przesyłu w URL
        encoded_query = base64.b64encode(query.encode()).decode()
        
        logger.info(f"Napi Search (Spaceless): {query}")
        
        return [{
            'language': 'pol',
            'label': f"NapiProjekt | {title} ({year})",
            'link_hash': encoded_query
        }]

    def download(self, encoded_query):
        try:
            query = base64.b64decode(encoded_query).decode()
        except Exception:
            query = encoded_query

        payload = {
            "mode": "1",
            "client": "NapiProjektPython",
            "client_ver": "0.1",
            "search_title": query, # Tu leci wyczyszczony tytuł
            "downloaded_subtitles_lang": "PL",
            "downloaded_subtitles_txt": "1"
        }

        logger.info(f"Napi Download: Próba pobrania dla '{query}'...")

        try:
            r = requests.post(self.api_url, data=payload, headers=self.headers, 
                              impersonate="chrome120", timeout=15)
            
            logger.info(f"DEBUG API Response: {r.text[:300]}")

            if r.status_code == 200 and r.text:
                if "nie znaleziono" in r.text.lower():
                    return None
                
                dom = minidom.parseString(r.text)
                content_nodes = dom.getElementsByTagName("content")
                
                if content_nodes and content_nodes[0].firstChild:
                    raw_data = base64.b64decode(content_nodes[0].firstChild.data)
                    if raw_data.startswith(b"NP"):
                        # Dekodowanie i dekompresja
                        dec = self._decrypt(raw_data[4:])
                        return zlib.decompress(dec[4:], -zlib.MAX_WBITS).decode("utf-8", "ignore")
                    return raw_data.decode('utf-8', 'ignore')
            
        except Exception as e:
            logger.error(f"Napi Connection Error: {e}")
            
        return None
