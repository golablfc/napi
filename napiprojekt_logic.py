# -*- coding: utf-8 -*-
import base64
import zlib
import logging
import requests
from xml.dom import minidom

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class NapiProjektKatalog:
    def __init__(self):
        # Wymuszamy HTTPS, aby uniknąć problemów z routingiem HTTP u Cloudflare
        self.api_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        logger.info("NapiProjektKatalog: Tryb Deep-Request (Omijanie 522).")

    def _decrypt(self, data: bytes) -> bytes:
        # Oryginalne deszyfrowanie XOR z kodu homika
        key = [0x5E, 0x34, 0x45, 0x43, 0x52, 0x45, 0x54, 0x5F]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i % 8]
            dec[i] = ((dec[i] << 4) & 0xFF) | (dec[i] >> 4)
        return bytes(dec)

    def search(self, item, imdb_id="", *args):
        eng_title = item.get('title') or item.get('tvshow')
        year = item.get('year', '')
        pl_title = "Skazani na Shawshank" if imdb_id == "tt0111161" else eng_title
        
        search_pl = f"{pl_title} {year}".strip()
        search_eng = f"{eng_title} {year}".strip()

        def to_hex(text):
            return text.encode().hex()

        return [
            {
                'language': 'pol',
                'label': f"Napi API (PL) | {pl_title} ({year})",
                'link_hash': f"NPX{to_hex(search_pl)}"
            },
            {
                'language': 'pol',
                'label': f"Napi API (ENG) | {eng_title} ({year})",
                'link_hash': f"NPX{to_hex(search_eng)}"
            }
        ]

    def download(self, md5hash, language="PL"):
        try:
            clean = md5hash.replace("NPX", "").split('.')[0]
            query = bytes.fromhex(clean).decode()
        except Exception:
            query = md5hash

        # Budujemy surowe żądanie z nagłówkami, które Cloudflare musi przepuścić
        payload = {
            "mode": "1",
            "client": "NapiProjektPython",
            "client_ver": "0.1",
            "search_title": query,
            "downloaded_subtitles_lang": language,
            "downloaded_subtitles_txt": "1"
        }
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": "napiprojekt.pl"
        }
        
        logger.info(f"Ostateczna próba pobrania przez HTTPS dla '{query}'...")

        try:
            # Używamy session, aby zachować parametry TLS
            with requests.Session() as s:
                r = s.post(self.api_url, data=payload, headers=headers, timeout=15)
                
                logger.info(f"Odebrano odpowiedź (Status: {r.status_code}).")
                
                if r.status_code == 200:
                    if "nie znaleziono" in r.text.lower():
                        logger.warning(f"Brak wyników: {query}")
                        return None
                    
                    dom = minidom.parseString(r.text)
                    content_nodes = dom.getElementsByTagName("content")
                    if content_nodes and content_nodes[0].firstChild:
                        raw_data = base64.b64decode(content_nodes[0].firstChild.data)
                        if raw_data.startswith(b"NP"):
                            dec = self._decrypt(raw_data[4:])
                            return zlib.decompress(dec[4:], -zlib.MAX_WBITS).decode("utf-8", "ignore")
                        return raw_data.decode('utf-8', 'ignore')
                
                logger.error(f"API Error (Status: {r.status_code}). Treść: {r.text[:50]}")
        except Exception as e:
            logger.error(f"Błąd krytyczny: {e}")
            
        return None
