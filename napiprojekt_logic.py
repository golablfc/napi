# -*- coding: utf-8 -*-
import urllib.parse
import base64
import zlib
import logging
from xml.dom import minidom
from curl_cffi import requests

# Konfiguracja loggera
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class NapiProjektKatalog:
    def __init__(self):
        # Używamy HTTPS dla bezpiecznego połączenia z chmury
        self.api_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        logger.info("NapiProjektKatalog: Inicjalizacja trybu Cloud (Render).")

    def _decrypt(self, data: bytes) -> bytes:
        # Oryginalna logika deszyfrowania XOR
        key = [0x5E, 0x34, 0x45, 0x43, 0x52, 0x45, 0x54, 0x5F]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i % 8]
            dec[i] = ((dec[i] << 4) & 0xFF) | (dec[i] >> 4)
        return bytes(dec)

    def search(self, item, imdb_id="", *args):
        eng_title = item.get('title') or item.get('tvshow')
        pl_title = "Skazani na Shawshank" if imdb_id == "tt0111161" else eng_title
        
        def to_hex(text):
            return text.encode().hex()

        logger.info(f"Generowanie opcji dla: {pl_title}")
        
        return [
            {
                'language': 'pol',
                'label': f"Napi API | {pl_title}",
                'link_hash': f"NPX{to_hex(pl_title)}"
            },
            {
                'language': 'pol',
                'label': f"Napi API | {eng_title}",
                'link_hash': f"NPX{to_hex(eng_title)}"
            }
        ]

    def download(self, md5hash, language="PL"):
        try:
            # Dekodowanie HEX z linku
            clean = md5hash.replace("NPX", "").split('.')[0]
            query = bytes.fromhex(clean).decode()
        except Exception as e:
            logger.error(f"Błąd dekodowania: {e}")
            query = md5hash

        # Budujemy żądanie zgodne z dodatkiem homika
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
            # Na Renderze musimy udawać nowoczesną przeglądarkę (impersonate), 
            # bo centra danych są częściej sprawdzane pod kątem botów
            r = requests.post(self.api_url, data=payload, impersonate="chrome120", timeout=30)
            
            if r.status_code == 200 and r.text:
                dom = minidom.parseString(r.text)
                content_nodes = dom.getElementsByTagName("content")
                if content_nodes and content_nodes[0].firstChild:
                    raw_data = base64.b64decode(content_nodes[0].firstChild.data)
                    # Jeśli format NP, deszyfrujemy
                    if raw_data.startswith(b"NP"):
                        dec = self._decrypt(raw_data[4:])
                        return zlib.decompress(dec[4:], -zlib.MAX_WBITS).decode("utf-8", "ignore")
                    return raw_data.decode('utf-8', 'ignore')
            
            logger.error(f"API Render Error (Status: {r.status_code})")
        except Exception as e:
            logger.error(f"Cloud Connection Error: {e}")
            
        return None
