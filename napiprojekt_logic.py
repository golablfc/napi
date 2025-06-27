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
from typing import List, Dict, Optional

class NapiProjektKatalog:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.download_url = "https://napiprojekt.pl/api/api-napiprojekt3.php"
        self.search_url = "https://www.napiprojekt.pl/ajax/search_catalog.php"
        self.base_url = "https://www.napiprojekt.pl"
        self.logger.info("NapiProjektKatalog initialized")

    def log(self, message, ex=None):
        if ex:
            self.logger.error(f"{message}\n{traceback.format_exc()}")
        else:
            self.logger.info(message)

    def _decrypt(self, data):
        key = [0x5E, 0x34, 0x45, 0x43, 0x52, 0x45, 0x54, 0x5F]
        decrypted = bytearray(data)
        for i in range(len(decrypted)):
            decrypted[i] ^= key[i % 8]
            decrypted[i] = ((decrypted[i] << 4) & 0xFF) | (decrypted[i] >> 4)
        return bytes(decrypted)

    def _convert_microdvd_to_srt(self, content):
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
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        seconds_val = int(seconds % 60)
        milliseconds = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds_val:02d},{milliseconds:03d}"

    def _parse_duration(self, duration_text):
        """Ulepszone parsowanie czasu z różnych formatów"""
        if not duration_text:
            return None
            
        try:
            # Format HH:MM:SS.mmm
            if '.' in duration_text:
                h_m_s, ms = duration_text.split('.')
                ms = float(f"0.{ms}")
                parts = list(map(float, h_m_s.split(':')))
                if len(parts) == 3:
                    return parts[0] * 3600 + parts[1] * 60 + parts[2] + ms
                elif len(parts) == 2:
                    return parts[0] * 60 + parts[1] + ms
            
            # Format HH:MM:SS
            parts = list(map(float, duration_text.split(':')))
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2:
                return parts[0] * 60 + parts[1]
                
            return None
        except Exception as e:
            self.logger.warning(f"Failed to parse duration: {duration_text} - {str(e)}")
            return None

    def _calculate_match_score(self, video_duration, sub_duration, video_fps=None, sub_fps=None):
        """Nowa, bardziej odporna metoda scoringowa"""
        # Brak danych wideo = neutralny score
        if video_duration is None:
            return 100
            
        # Brak danych napisów = bardzo zły score
        if sub_duration is None:
            return float('inf')
        
        # Oblicz różnicę czasu (ważona 80%)
        duration_diff = abs(video_duration - sub_duration)
        
        # Oblicz różnicę FPS (ważona 20%, jeśli dostępne)
        fps_diff = abs((video_fps or 0) - (sub_fps or 0)) if video_fps and sub_fps else 0
        
        # Oblicz końcowy score (im mniejszy, tym lepszy)
        score = (duration_diff * 0.8) + (fps_diff * 0.2)
        
        self.logger.debug(f"Scoring: video={video_duration}s, sub={sub_duration}s → score={score:.2f}")
        return score

    def _extract_fps_from_label(self, label):
        match = re.search(r'(\d+\.\d+|\d+)\s*FPS', label)
        return float(match.group(1)) if match else None

    def _handle_old_format(self, data):
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

    def search(self, item, imdb_id, video_duration=None, video_fps=None):
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
                            subs = self._get_subtitles_from_detail(detail_url, video_duration, video_fps)
                            subtitle_list.extend(subs)
                except Exception as e:
                    self.log("Error processing block", e)
                    continue
            
            # Sortowanie po score (im mniejszy, tym lepszy)
            subtitle_list.sort(key=lambda x: x['score'])
            
            # Logowanie najlepszych wyników
            for i, sub in enumerate(subtitle_list[:15]):
                self.log(
                    f"Subtitle #{i+1}: {sub['label']} | "
                    f"Score: {sub['score']:.2f} | "
                    f"Duration: {sub['_duration']}s | "
                    f"FPS: {sub.get('_fps', 'N/A')}"
                )
            
            return subtitle_list[:15]
            
        except Exception as e:
            self.log("Search error", e)
            return []

    def _build_detail_url(self, item, href):
        match = re.search(r'napisy-(\d+)-(.*)', href)
        if match:
            napi_id, slug = match.groups()
            
            slug = re.sub(r'[-\s]*\(?\d{4}\)?$', '', slug).strip('-')
            
            base_url = f"{self.base_url}/napisy1,1,1-dla-{napi_id}-{slug}"
            
            if item.get('tvshow') and item.get('season') and item.get('episode'):
                season = str(item['season']).zfill(2)
                episode = str(item['episode']).zfill(2)
                return f"{base_url}-s{season}e{episode}"
            elif item.get('year'):
                return f"{base_url}-({item['year']})"
            else:
                return base_url
                
        return urllib.parse.urljoin(self.base_url, href)

    def _get_subtitles_from_detail(self, detail_url, video_duration=None, video_fps=None):
        subs = []
        try:
            # Sprawdzanie kolejnych stron (1, 2, 3...)
            page = 1
            while True:
                # Zamiana numeru strony w URL (np. napisy1 → napisy2)
                page_url = detail_url.replace('napisy1,', f'napisy{page},')
                
                req = urllib.request.Request(
                    page_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'}
                )
                
                with urllib.request.urlopen(req, timeout=15) as response:
                    soup = BeautifulSoup(response.read(), 'lxml')
                    
                    # Jeśli strona nie zawiera napisów, przerywamy pętlę
                    rows = soup.select('tbody > tr')
                    if not rows:
                        break
                        
                    # Przetwarzanie napisów z bieżącej strony
                    for row in rows:
                        link = row.find('a', href=re.compile(r'napiprojekt:'))
                        if not link:
                            continue
                            
                        cols = row.find_all('td')
                        if len(cols) < 5:
                            continue

                        duration_text = cols[3].get_text(strip=True)
                        sub_duration = self._parse_duration(duration_text)
                        sub_fps = self._extract_fps_from_label(cols[2].get_text(strip=True))
                        
                        score = self._calculate_match_score(
                            video_duration,
                            sub_duration,
                            video_fps,
                            sub_fps
                        )

                        subs.append({
                            'language': 'pol',
                            'label': f"{cols[1].get_text(strip=True)} | {duration_text}",
                            'link_hash': link['href'].replace('napiprojekt:', ''),
                            'score': score,
                            '_duration': sub_duration,
                            '_fps': sub_fps
                        })
                    
                    page += 1
                    time.sleep(1)  # Ograniczenie zapytań

        except Exception as e:
            self.log(f"Error processing page {page}: {str(e)}")
        
        return subs
