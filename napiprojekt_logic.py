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
        # Używamy surowego HTTP, który bywa szybciej procesowany przez stare API
        self.api_url = "http://napiprojekt.pl/api/api-napiprojekt3.php"
        logger.info("NapiProjektKatalog: Tryb Hash-Bypass (Zgodność z Kodi).")

    def _decrypt(self, data: bytes) -> bytes:
        # Oryginalne deszyfrowanie XOR z kodu homika
        key = [0x5E, 0x34, 0x45, 0x43, 0x52, 0x45, 0x54, 0x5F]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i % 8]
            dec[i] = ((dec[i] << 4) & 0xFF) | (dec[i] >> 4)
        return bytes(dec)

    def search(self, item, imdb_id="", *args):
        # Przekazujemy IMDB ID jako link_hash, co pozwoli nam zapytać o konkretny film
        label = f"NapiProjekt (IMDB) | {item.get('title') or item.get('tvshow')}"
        return [{
            'language': 'pol',
            'label': label,
            'link_hash': imdb_id
        }]

    def download(self, md5hash, language="PL"):
        # Parametry identyczne z tymi, które wysyła dodatek Kodi od lat
        # Zmieniamy sposób zapytania: zamiast szukać po tytule, pytamy o napisy dla ID
        payload = {
            "mode": "1",
            "client": "NapiProjektPython",
            "client_ver": "0.1",
            "downloaded_subtitles_id": md5hash, # Używamy IMDB ID jako identyfikatora
            "downloaded_subtitles_txt": "1",
            "downloaded_subtitles_lang": language
        }
        
        headers = {
            "User-Agent": "NapiProjekt/1.0 (Kodi Edition)",
            "Host": "napiprojekt.pl"
        }
        
        logger.info(f"API Request: Pobieram napisy dla identyfikatora '{md5hash}'...")

        try:
            r = requests.post(self.api_url, data=payload, headers=headers, timeout=10)
            
            if r.status_code == 200:
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
                    logger.warning(f"Brak treści dla ID: {md5hash}")
            else:
                logger.error(f"API Error: {r.status_code}")
        except Exception as e:
            logger.error(f"Błąd krytyczny: {e}")
            
        return None
