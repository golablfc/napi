# -*- coding: utf-8 -*-
import base64
import zlib
import logging
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
        # Prawidłowy klucz XOR: 'NAPI_'
        key = [0x4e, 0x41, 0x50, 0x49, 0x5f]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i % 5]
        return bytes(dec)

    def search(self, item, imdb_id=""):
        title = item.get('title') or item.get('tvshow')
        year = item.get('year', '')
        query = f"{title} {year}".strip()
        
        # Kodujemy zapytanie do Base64, aby bezpiecznie przesyłać je w URL
        encoded_query = base64.b64encode(query.encode()).decode()
        
        return [{
            'language': 'pol',
            'label': f"NapiProjekt | {query}",
            'link_hash': encoded_query,
            '_duration': "??:??:??"
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
            "search_title": query,
            "downloaded_subtitles_lang": "PL",
            "downloaded_subtitles_txt": "1"
        }

        logger.info(f"Napi Download: Próba pobrania dla '{query}'...")

        try:
            # TLS Impersonate chrome120 dla ominięcia Cloudflare
            r = requests.post(self.api_url, data=payload, headers=self.headers, 
                              impersonate="chrome120", timeout=15)
            
            # DIAGNOSTYKA: Logujemy odpowiedź serwera
            logger.info(f"DEBUG API Response: {r.text[:300]}")

            if r.status_code == 200 and r.text:
                if "nie znaleziono" in r.text.lower():
                    logger.warning(f"API: Brak wyników dla '{query}'")
                    return None
                
                dom = minidom.parseString(r.text)
                content_nodes = dom.getElementsByTagName("content")
                
                if content_nodes and content_nodes[0].firstChild:
                    raw_data = base64.b64decode(content_nodes[0].firstChild.data)
                    if raw_data.startswith(b"NP"):
                        # Deszyfrowanie i dekompresja
                        dec = self._decrypt(raw_data[4:])
                        return zlib.decompress(dec[4:], -zlib.MAX_WBITS).decode("utf-8", "ignore")
                    return raw_data.decode('utf-8', 'ignore')
            
            logger.error(f"Napi API Error: Status {r.status_code}")
        except Exception as e:
            logger.error(f"Napi Connection Error: {e}")
            
        return None
