# -*- coding: utf-8 -*-
import base64
import zlib
import logging
import re
import urllib.parse
from xml.dom import minidom
from curl_cffi import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class NapiProjektKatalog:
    def __init__(self):
        self.api_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.headers = {
            "User-Agent": "NapiProjekt/1.0 (Kodi Edition)",
            "Accept": "text/xml,application/xml,application/xhtml+xml,text/html;q=0.9,text/plain;q=0.8,*/*;q=0.5",
            "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
            "Connection": "keep-alive"
        }

    def _decrypt(self, data: bytes) -> bytes:
        # Prawidłowy klucz z kodu homika
        key = [0x4e, 0x41, 0x50, 0x49, 0x5f]
        dec = bytearray(data)
        for i in range(len(dec)):
            dec[i] ^= key[i % 5]
        return bytes(dec)

    def search(self, item, imdb_id=""):
        """ FAZA 1: Skrapowanie strony WWW katalogu """
        title = item.get('title') or item.get('tvshow') or ""
        
        # Test na Skazanych
        if imdb_id == "tt0111161":
            search_query = "Skazani na Shawshank"
        else:
            search_query = title

        logger.info(f"Napi Search (WWW): Szukam '{search_query}' na stronie katalogu...")
        
        try:
            # KROK 1: Szukamy filmu w wyszukiwarce
            url = f"https://www.napiprojekt.pl/katalog-napisow?tytul={urllib.parse.quote(search_query)}"
            r = requests.get(url, impersonate="chrome120", timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            # DIAGNOSTYKA: Tytuł strony (czy Cloudflare nas przepuścił?)
            page_title = soup.title.string.strip() if soup.title else 'Brak tytułu'
            logger.info(f"Napi WWW Title: '{page_title}'")
            
            # KROK 2: Znajdujemy link do profilu filmu
            movie_link = None
            for a in soup.find_all('a', href=True):
                href = a['href']
                # Szukamy słów kluczowych niezależnie od ścieżki i ukośników
                if 'napisy' in href and '-do-' in href:
                    if href.startswith('http'):
                        movie_link = href
                    else:
                        movie_link = "https://www.napiprojekt.pl/" + href.lstrip('/')
                    break
                    
            if not movie_link:
                logger.warning("Napi Search: Nie znaleziono podstrony. Pokazuję pierwsze 5 linków:")
                links = [a['href'] for a in soup.find_all('a', href=True)][:5]
                logger.warning(f"Linki: {links}")
                return []
                
            logger.info(f"Napi Search: Znaleziono profil filmu -> {movie_link}")
            
            # KROK 3: Wchodzimy w profil i wyciągamy tabelę z prawdziwymi hashami
            r2 = requests.get(movie_link, impersonate="chrome120", timeout=15)
            soup2 = BeautifulSoup(r2.text, 'html.parser')
            
            results = []
            seen = set()
            
            for a in soup2.find_all('a', href=re.compile(r'^napiprojekt:')):
                h = a['href'].replace('napiprojekt:', '')
                if h in seen: continue
                seen.add(h)
                
                # Próbujemy wyciągnąć informacje o wersji z tabeli
                tr = a.find_parent('tr')
                label = "Polska wersja"
                if tr:
                    cols = tr.find_all('td')
                    if len(cols) >= 2:
                        label = cols[1].get_text(strip=True)
                
                results.append({
                    'language': 'pol',
                    'label': f"Napi | {label}",
                    'link_hash': h,
                    '_duration': "??:??:??"
                })
                
            logger.info(f"Napi Search: Znaleziono {len(results)} wersji napisów (Hashy).")
            return results

        except Exception as e:
            logger.error(f"Napi WWW Scrape Error: {e}")
            return []

    def download(self, md5hash):
        """ FAZA 2: Pobieranie z API za pomocą czystego Hasha MD5 """
        # Odbezpieczamy hash przysłany ze Stremio
        md5hash = md5hash.replace(".srt", "")
        
        payload = {
            "mode": "1",
            "client": "NapiProjektPython",
            "client_ver": "0.1",
            "downloaded_subtitles_id": md5hash, # Używamy prawdziwego hasha wyciągniętego z WWW
            "downloaded_subtitles_lang": "PL",
            "downloaded_subtitles_txt": "1"
        }

        logger.info(f"Napi Download: Uderzam do API po hash '{md5hash}'...")

        try:
            r = requests.post(self.api_url, data=payload, headers=self.headers, 
                              impersonate="chrome120", timeout=15)

            if r.status_code == 200 and r.text:
                dom = minidom.parseString(r.text)
                content_nodes = dom.getElementsByTagName("content")
                
                if content_nodes and content_nodes[0].firstChild:
                    raw_data = base64.b64decode(content_nodes[0].firstChild.data)
                    if raw_data.startswith(b"NP"):
                        dec = self._decrypt(raw_data[4:])
                        return zlib.decompress(dec[4:], -zlib.MAX_WBITS).decode("utf-8", "ignore")
                    return raw_data.decode('utf-8', 'ignore')
            
            logger.warning(f"Napi API (Hash): Odmowa lub brak treści dla hasha {md5hash}")
        except Exception as e:
            logger.error(f"Napi Connection Error: {e}")
            
        return None
