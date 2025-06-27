import urllib.request
import urllib.parse
import re
import base64
import traceback
from xml.dom import minidom
from bs4 import BeautifulSoup
import logging
import zlib
import struct
import time
import binascii


class NapiProjektKatalog:

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.download_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.search_url = "https://www.napiprojekt.pl/ajax/search_catalog.php"
        self.base_url = "https://www.napiprojekt.pl"
        self.logger.info("NapiProjektKatalog initialized")

    def log(self, message, ex=None):
        """Uniwersalna metoda logowania"""
        if ex:
            self.logger.error(f"{message}\n{traceback.format_exc()}")
        else:
            self.logger.info(message)

    def _decrypt(self, data):
        """Deszyfrowanie danych NP"""
        key = [0x5E, 0x34, 0x45, 0x43, 0x52, 0x45, 0x54, 0x5F]
        decrypted = bytearray(data)
        for i in range(len(decrypted)):
            decrypted[i] ^= key[i % 8]
            decrypted[i] = ((decrypted[i] << 4) & 0xFF) | (decrypted[i] >> 4)
        return bytes(decrypted)

    def _convert_microdvd_to_srt(self, content):
        """Konwersja formatu MicroDVD do SRT"""
        try:
            if not content:
                return None
                
            if '-->' in content and '\n\n' in content:
                return content
                
            lines = content.splitlines()
            srt_lines = []
            counter = 1
            fps = 23.976

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                match = re.match(r'\{(\d+)\}\{(\d+)\}(.*)', line)
                if match:
                    start_frame, end_frame, text = match.groups()
                    
                    start_time = int(start_frame) / fps
                    end_time = int(end_frame) / fps
                    
                    start_str = self._format_time(start_time)
                    end_str = self._format_time(end_time)
                    
                    srt_lines.append(f"{counter}\n{start_str} --> {end_str}\n{text.replace('|', '\n')}\n\n")
                    counter += 1
            
            return ''.join(srt_lines) if srt_lines else None
            
        except Exception as e:
            self.log("Error converting MicroDVD to SRT", e)
            return None

    def _format_time(self, seconds):
        """Formatowanie czasu do formatu SRT"""
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        seconds_val = int(seconds % 60)
        milliseconds = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds_val:02d},{milliseconds:03d}"

    def _handle_old_format(self, data):
        """Obsługa starych formatów napisów"""
        try:
            try:
                text = data.decode('utf-8-sig')
                if "{" in text:
                    converted = self._convert_microdvd_to_srt(text)
                    if converted:
                        return converted
                return text
            except UnicodeDecodeError:
                pass

            if len(data) > 10 and data[:3] == b'\xf6\x93\xf4':
                try:
                    decrypted = bytes(b ^ 0x66 for b in data[10:])
                    text = decrypted.decode('utf-8')
                    converted = self._convert_microdvd_to_srt(text)
                    if converted:
                        return converted
                    return text
                except Exception as e:
                    self.log("Special f693f4 format decoding failed", e)

            for encoding in ['utf-8', 'iso-8859-2', 'windows-1250', 'cp1250']:
                try:
                    text = data.decode(encoding)
                    if "{" in text:
                        converted = self._convert_microdvd_to_srt(text)
                        if converted:
                            return converted
                    return text
                except UnicodeDecodeError:
                    continue

        except Exception as e:
            self.log("Critical error in _handle_old_format", e)
            try:
                filename = f"failed_sub_{int(time.time())}.bin"
                with open(filename, "wb") as f:
                    f.write(data)
                self.log(f"Saved failed subtitle to {filename}")
            except:
                pass
        
        return None

    def download(self, md5hash):
        """Pobieranie napisów"""
        for attempt in range(3):
            try:
                params = {
                    'mode': '17',
                    'client': 'NapiProjektPython',
                    'downloaded_subtitles_id': md5hash,
                    'downloaded_subtitles_lang': 'PL',
                    'downloaded_subtitles_txt': '1'
                }
                data = urllib.parse.urlencode(params).encode('utf-8')
                req = urllib.request.Request(
                    self.download_url, 
                    data=data,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
                    }
                )
                
                with urllib.request.urlopen(req, timeout=15) as response:
                    if response.status != 200:
                        continue
                        
                    xml = minidom.parseString(response.read())
                    content = xml.getElementsByTagName('content')[0].firstChild.data
                    binary_data = base64.b64decode(content)

                    if binary_data.startswith(b'NP'):
                        decrypted = self._decrypt(binary_data[4:])
                        if len(decrypted) < 8:
                            continue
                            
                        crc = struct.unpack('<I', decrypted[:4])[0]
                        actual_data = decrypted[4:]
                        
                        if zlib.crc32(actual_data) & 0xFFFFFFFF != crc:
                            continue
                            
                        decompressed = zlib.decompress(actual_data, -zlib.MAX_WBITS)
                        text = decompressed.decode('utf-8')
                        result = self._convert_microdvd_to_srt(text) if "{" in text else text
                        if result:
                            return result
                    
                    else:
                        result = self._handle_old_format(binary_data)
                        if result:
                            return result

            except Exception as e:
                self.log(f"Download attempt {attempt+1} failed for hash {md5hash}", e)
                time.sleep(1)
        
        return None

    def search(self, item, imdb_id):
        """Wyszukiwanie napisów w katalogu"""
        subtitle_list = []
        try:
            title_to_find = item.get('tvshow') or item.get('title') or imdb_id
            
            query_kind = 1 if item.get('tvshow') else 2
            query_year = item.get('year', '').split('–')[0] if item.get('tvshow') else item.get('year', '')
            
            post = {
                'queryKind': str(query_kind),
                'queryString': title_to_find.lower(),
                'queryYear': str(query_year),
                'associate': imdb_id if imdb_id.startswith('tt') else ''
            }
            
            self.log(f"Searching NapiProjekt with params: {post}")
            
            post_data = urllib.parse.urlencode(post).encode('utf-8')
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest'
            }
            
            req = urllib.request.Request(
                self.search_url, 
                data=post_data, 
                headers=headers
            )
            
            with urllib.request.urlopen(req, timeout=15) as response:
                search_results_html = response.read().decode('utf-8')
                
            soup = BeautifulSoup(search_results_html, 'lxml')
            results_blocks = soup.find_all('div', class_='movieSearchContent')
            
            if not results_blocks:
                self.log(f"No results for: {title_to_find}")
                return subtitle_list
                
            for block in results_blocks:
                try:
                    imdb_link = block.find('a', href=re.compile(r'imdb.com/title/(tt\d+)'))
                    if imdb_link and imdb_id in imdb_link['href']:
                        title_link = block.find('a', class_='movieTitleCat')
                        if title_link and title_link.get('href'):
                            detail_url = self._build_detail_url(item, title_link.get('href'))
                            if not detail_url: 
                                continue
                            
                            self.log(f"Found detail page: {detail_url}")
                            subs = self._get_subtitles_from_detail(detail_url)
                            subtitle_list.extend(subs)
                except Exception as e:
                    self.log("Error processing block", e)
                    continue
            
            self.log(f"Found {len(subtitle_list)} subtitles")
            return subtitle_list
            
        except Exception as e:
            self.log("Search error", e)
            return []

    def _build_detail_url(self, item, href):
        """Budowanie URL do szczegółów napisów"""
        match = re.search(r'napisy-(\d+)-(.*)', href)
        if match:
            napi_id, slug = match.groups()
            
            # Usuwamy istniejący rok z slug jeśli jest
            slug = re.sub(r'-\d{4}\)?$', '', slug)
            
            # Dla seriali
            if item.get('tvshow') and item.get('season') and item.get('episode'):
                season = str(item['season']).zfill(2)
                episode = str(item['episode']).zfill(2)
                return f"{self.base_url}/napisy1,1,1-dla-{napi_id}-{slug}-s{season}e{episode}"
            # Dla filmów
            elif item.get('year'):
                return f"{self.base_url}/napisy1,1,1-dla-{napi_id}-{slug}-({item['year']})"
            # Dla przypadków bez roku
            else:
                return f"{self.base_url}/napisy1,1,1-dla-{napi_id}-{slug}"
        return urllib.parse.urljoin(self.base_url, href)

    def _get_subtitles_from_detail(self, detail_url):
        """Pobieranie listy napisów ze strony szczegółów"""
        subs = []
        try:
            req = urllib.request.Request(
                detail_url, 
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
                }
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                detail_page = response.read().decode('utf-8')
                
            soup = BeautifulSoup(detail_page, 'lxml')
            for row in soup.select('tbody > tr'):
                link = row.find('a', href=re.compile(r'napiprojekt:'))
                if link:
                    cols = row.find_all('td')
                    if len(cols) > 4:
                        sub_data = {
                            'language': 'pol',
                            'label': f"{cols[1].get_text(strip=True)} | {cols[3].get_text(strip=True)} | {cols[4].get_text(strip=True)}",
                            'link_hash': link['href'].replace('napiprojekt:', ''),
                            'duration_text': cols[3].get_text(strip=True),
                            'fps': cols[4].get_text(strip=True),
                            'downloads': cols[5].get_text(strip=True) if len(cols) > 5 else 'N/A'
                        }
                        subs.append(sub_data)
                        self.log(f"Found subtitle: {sub_data}")

        except Exception as e:
            self.log(f"Detail page error: {detail_url}", e)
        return subs
