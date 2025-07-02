import re
import requests
import logging
from bs4 import BeautifulSoup
from utils import convert_subtitles, guess_fps_and_duration, get_duration_string

log = logging.getLogger(__name__)

BASE_URL = "https://www.napiprojekt.pl"

def _normalize_title(title):
    return re.sub(r"[^\w\s]", "", title.lower()).strip()

def _generate_candidate_urls(id_title, title, year, season=None, episode=None):
    urls = []

    # Podstawowy adres
    base = f"{BASE_URL}/napisy1,1,1-dla-{id_title}-{title}-({year})"
    if season and episode:
        urls.append(f"{base}-s{int(season):02d}e{int(episode):02d}")
    urls.append(base)
    return urls

def _extract_blocks_from_detail(detail_html):
    soup = BeautifulSoup(detail_html, "html.parser")
    rows = soup.select("table#tblNapisy tr[class^='bg']")
    return rows

def _parse_block(row):
    tds = row.find_all("td")
    if len(tds) < 6:
        return None

    link_tag = tds[0].find("a", href=True)
    if not link_tag:
        return None

    href = link_tag["href"]
    subtitle_id_match = re.search(r"([a-f0-9]{32})", href)
    if not subtitle_id_match:
        return None

    subtitle_id = subtitle_id_match.group(1)
    download_link = f"{BASE_URL}/bin/napisy/{subtitle_id}.zip"

    fps = tds[2].text.strip()
    downloads = tds[3].text.strip()
    lang = tds[4].text.strip()
    filename = tds[5].text.strip()

    return {
        "id": subtitle_id,
        "link": download_link,
        "fps": fps,
        "downloads": int(downloads.replace(" ", "")) if downloads else 0,
        "lang": lang,
        "filename": filename
    }

def _fetch_subtitles_from_detail_page(url):
    log.debug(f"DETAIL try: {url}")
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        log.debug(f"DETAIL {url}: HTTP {resp.status_code}")
        return []

    blocks = _extract_blocks_from_detail(resp.text)
    if not blocks:
        log.debug(f"DETAIL {url}: brak wierszy")
        return []

    parsed = list(filter(None, (_parse_block(row) for row in blocks)))
    return parsed

def search_subtitles(meta):
    log.info(f"Searching NapiProjekt with: {meta}")
    results = []

    title = meta.get("tvshow") or meta.get("title")
    year = meta.get("year")
    season = meta.get("season")
    episode = meta.get("episode")

    if not title or not year:
        log.warning("Brak tytułu lub roku – pomijam wyszukiwanie")
        return []

    id_map = {
        "Gra o tron": "26704",
        "Nasza planeta": "55875",
        "Infiltracja": "4576",
        "Ong-Bak": "2634"
    }

    id_title = id_map.get(title)
    if not id_title:
        log.warning(f"Brak ID w mapie dla: {title}")
        return []

    candidate_urls = _generate_candidate_urls(id_title, title.replace(" ", "-"), year, season, episode)

    all_subs = []
    for url in candidate_urls:
        subs = _fetch_subtitles_from_detail_page(url)
        all_subs.extend(subs)

    if not all_subs:
        log.debug("Brak napisów na wszystkich sprawdzonych stronach.")
        return []

    log.info(f"Found {len(all_subs)} subtitles total")

    converted = []
    for sub in all_subs:
        try:
            content = convert_subtitles(sub["link"])
            if "-->" not in content:
                log.debug(f"Pomijam brak timestampów: {sub['filename']}")
                continue
            fps, duration = guess_fps_and_duration(content)
            duration_str = get_duration_string(duration)
            converted.append({
                "id": sub["id"],
                "lang": f"{duration_str} · PL",
                "url": f"http://localhost:7002/subtitles/{sub['id']}.vtt",
                "name": f"{sub['filename']} ({sub['fps']} FPS, {sub['downloads']} pobrań)"
            })
        except Exception as e:
            log.debug(f"Błąd przy konwersji: {e}")
            continue

    # Sortuj po liczbie pobrań
    converted.sort(key=lambda x: int(re.search(r"(\d+) pobrań", x["name"]).group(1)), reverse=True)

    return converted[:100]

def download_subtitle(sub_id):
    url = f"{BASE_URL}/bin/napisy/{sub_id}.zip"
    log.info(f"Pobieranie napisów: {sub_id}")
    resp = requests.get(url, timeout=10)
    if resp.status_code != 200:
        log.error(f"Nie udało się pobrać napisów: {sub_id}")
        return None
    return resp.content
