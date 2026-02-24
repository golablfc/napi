# -*- coding: utf-8 -*-
import base64
import zlib
import logging
from xml.dom import minidom
from curl_cffi import requests # Omijanie TLS Fingerprinting

logger = logging.getLogger(__name__)

class NapiProjektKatalog:
    def __init__(self):
        self.api_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        # Pełne nagłówki dla uwiarygodnienia ruchu
        self.headers = {
            "User-Agent": "NapiProjekt/1.0 (Kodi Edition)",
            "Accept": "text/xml,application/xml,application/xhtml+xml,text/html;q=0.9,text/plain;q=0.8,image/png,*/*;q=0.5",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }

    def _decrypt(self, data: bytes) -> bytes:
        # Prawidłowy klucz i algorytm XOR z kodu homika
        key = [0x4e, 0x41, 0x50, 0x49, 0x5f] # "NAPI_"
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i % 5]
        return bytes(dec)

    def search(self, item, imdb_id=""):
        # Przygotowanie zapytania tekstowego
        title = item.get('title') or item.get('tvshow')
        year = item.get('year', '')
        query = f"{title} {year}".strip()
        
        # Generujemy link_hash jako Base64 tytułu dla bezpieczeństwa URL
        encoded_query = base64.b64encode(query.encode()).decode()
        
        logger.info(f"Napi Search: Przygotowano opcję dla '{query}'")
        
        return [{
            'language': 'pol',
            'label': f"NapiProjekt | {query}",
            'link_hash': encoded_query,
            '_duration': "??:??:??" # API tekstowe rzadko zwraca czas trwania w 1. kroku
        }]

    def download(self, encoded_query):
        try:
            query = base64.b64decode(encoded_query).decode()
        except Exception:
            query = encoded_query

        # Parametry zgodne z NapiProjekt.py dla wyszukiwania tekstowego
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
            # Używamy impersonate="chrome120" aby przejść przez Cloudflare
            r = requests.post(self.api_url, data=payload, headers=self.headers, 
                              impersonate="chrome120", timeout=15)
            
            if r.status_code == 200 and r.text:
                dom = minidom.parseString(r.text)
                content_nodes = dom.getElementsByTagName("content")
                
                if content_nodes and content_nodes[0].firstChild:
                    raw_data = base64.b64decode(content_nodes[0].firstChild.data)
                    # Deszyfrowanie formatu NP
                    if raw_data.startswith(b"NP"):
                        dec = self._decrypt(raw_data[4:])
                        # Dekompresja zlib
                        return zlib.decompress(dec[4:], -zlib.MAX_WBITS).decode("utf-8", "ignore")
                    return raw_data.decode('utf-8', 'ignore')
            
            logger.error(f"Napi API Error: Status {r.status_code}")
        except Exception as e:
            logger.error(f"Napi Connection Error: {e}")
            
        return None
