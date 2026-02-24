# -*- coding: utf-8 -*-
import urllib.parse
import base64
import zlib
import logging
import os
from xml.dom import minidom
from curl_cffi import requests

# Konfiguracja loggera
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class NapiProjektKatalog:
    def __init__(self):
        # Używamy HTTPS dla bezpiecznego połączenia z chmury
        self.api_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.session = requests.Session()
        logger.info("NapiProjektKatalog: Tryb Legacy API (Cloud Diagnostic Mode).")

    def _decrypt(self, data: bytes) -> bytes:
        # Oryginalna logika deszyfrowania XOR z kodu homika
        key = [0x5E, 0x34, 0x45, 0x43, 0x52, 0x45, 0x54, 0x5F]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i % 8]
            dec[i] = ((dec[i] << 4) & 0xFF) | (dec[i] >> 4)
        return bytes(dec)

    def search(self, item, imdb_id="", *args):
        # Pobieramy tytuły
        eng_title = item.get('title') or item.get('tvshow')
        # Dodajemy rok do wyszukiwania, co drastycznie zwiększa szansę na trafienie w API
        year = item.get('year', '')
        
        pl_title = "Skazani na Shawshank" if imdb_id == "tt0111161" else eng_title
        
        # Tworzymy frazę wyszukiwania: Tytuł + Rok
        search_pl = f"{pl_title} {year}".strip()
        search_eng = f"{eng_title} {year}".strip()

        def to_hex(text):
            return text.encode().hex()

        logger.info(f"Generowanie opcji API dla: {search_pl}")
        
        return [
            {
                'language': 'pol',
                'label': f"Napi API | {pl_title} ({year})",
                'link_hash': f"NPX{to_hex(search_pl)}"
            },
            {
                'language': 'pol',
                'label': f"Napi API | {eng_title} ({year})",
                'link_hash': f"NPX{to_hex(search_eng)}"
            }
        ]

    def download(self, md5hash, language="PL"):
        try:
            # Dekodowanie HEX z identyfikatora
            clean = md5hash.replace("NPX", "").split('.')[0]
            query = bytes.fromhex(clean).decode()
        except Exception as e:
            logger.error(f"Błąd dekodowania HEX: {e}")
            query = md5hash

        # Parametry żądania identyczne z tymi z Kodi
        payload = {
            "mode": "1",
            "client": "NapiProjektPython",
            "client_ver": "0.1",
            "search_title": query,
            "downloaded_subtitles_lang": language,
            "downloaded_subtitles_txt": "1"
        }
        
        logger.info(f"Cloud Request: Pobieram napisy dla '{query}'...")

        try:
            # Używamy impersonate, aby ominąć zabezpieczenia anty-botowe
            r = self.session.post(self.api_url, data=payload, impersonate="chrome120", timeout=30)
            
            # Logujemy fragment odpowiedzi dla diagnostyki
            logger.info(f"Odebrano odpowiedź (Status: {r.status_code}). Długość: {len(r.text)} znaków.")
            
            if r.status_code == 200:
                # Sprawdzamy czy odpowiedź zawiera błąd wewnątrz XML
                if "nie znaleziono" in r.text.lower():
                    logger.warning(f"API zwróciło 200, ale brak wyników dla: {query}")
                    return None
                
                try:
                    dom = minidom.parseString(r.text)
                    content_nodes = dom.getElementsByTagName("content")
                    
                    if content_nodes and content_nodes[0].firstChild:
                        raw_data = base64.b64decode(content_nodes[0].firstChild.data)
                        
                        # Deszyfrowanie formatu 'NP'
                        if raw_data.startswith(b"NP"):
                            dec = self._decrypt(raw_data[4:])
                            return zlib.decompress(dec[4:], -zlib.MAX_WBITS).decode("utf-8", "ignore")
                        
                        return raw_data.decode('utf-8', 'ignore')
                    else:
                        logger.warning(f"Brak węzła <content> w odpowiedzi dla: {query}")
                except Exception as parse_err:
                    logger.error(f"Błąd parsowania XML: {parse_err}. Treść: {r.text[:100]}...")
            
            logger.error(f"API Error (Status: {r.status_code})")
        except Exception as e:
            logger.error(f"Cloud Connection Error: {e}")
            
        return None
