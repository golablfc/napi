from flask import Flask, jsonify, request, Response
from napiprojekt_logic import NapiProjektKatalog
import re
import logging
import requests
import os
import urllib.parse
import time
from waitress import serve

# Konfiguracja logowania
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('stremio_napiprojekt.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
napi_helper = NapiProjektKatalog()

OMDB_API_KEY = os.environ.get('OMDB_API_KEY', 'fdc33d1c')

def fill_item_from_name(name, item):
    """Parsuje nazwę pliku wideo i wypełnia metadane"""
    if not name:
        return
        
    try:
        # Wzorzec dla seriali: Nazwa.S01E01.xxx
        tv_match = re.search(r'(.*?)[. ](?:S|s)(\d{1,2})(?:E|e)(\d{1,2}).*', name, re.IGNORECASE)
        if tv_match:
            item['tvshow'] = tv_match.group(1).replace(".", " ").strip()
            item['season'] = str(int(tv_match.group(2)))
            item['episode'] = str(int(tv_match.group(3)))
            return

        # Wzorzec dla filmów: Nazwa (2023).xxx
        movie_match = re.search(r'(.+?)[.\s\-_\[\]()]*(\d{4})(?!\d)', name, re.IGNORECASE)
        if movie_match:
            item['title'] = movie_match.group(1).replace(".", " ").replace("_", " ").strip()
            item['year'] = movie_match.group(2)
    except Exception as e:
        logger.error(f"Error parsing filename: {str(e)}")

def parse_video_params(decoded_id):
    """Parsuje parametry wideo z URL (videoSize, videoHash)"""
    params = {}
    if '/' in decoded_id:
        params_part = decoded_id.split('/', 1)[1]
        for param in params_part.split('&'):
            if '=' in param:
                key, value = param.split('=', 1)
                params[key.lower()] = value
    return params

@app.after_request
def after_request(response):
    """Dodaje nagłówki CORS do odpowiedzi"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

@app.route('/')
def index():
    """Strona główna z informacjami o addonie"""
    return jsonify({
        "message": "NapiProjekt Stremio Addon (Python)",
        "manifest": f"{request.url_root}manifest.json",
        "version": "1.4.0"
    })

@app.route('/manifest.json')
def manifest():
    """Zwraca manifest addona w formacie JSON"""
    return jsonify({
        "id": "org.stremio.napiprojekt.python",
        "version": "1.4.0",
        "name": "NapiProjekt PL",
        "description": "Napisy PL z NapiProjekt - czas trwania, FPS, pobrania",
        "logo": "https://i.imgur.com/h5mZ4pB.png",
        "resources": ["subtitles"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt"],
        "behaviorHints": {
            "configurable": False,
            "configurationRequired": False
        }
    })

@app.route('/subtitles/<content_type>/<path:imdb_id_with_params>.json')
def get_subtitles(content_type, imdb_id_with_params):
    """Główny endpoint do wyszukiwania napisów"""
    try:
        item = {}
        decoded_id = urllib.parse.unquote(imdb_id_with_params)
        
        # Parsowanie IMDB ID (ttXXXXXXX)
        imdb_match = re.match(r'^(tt\d{7,8})', decoded_id)
        if not imdb_match:
            logger.error(f"Invalid IMDB ID format: {decoded_id}")
            return jsonify({"subtitles": []})
            
        base_imdb_id = imdb_match.group(1)
        item['imdb_id'] = base_imdb_id
        
        # Parsowanie parametrów wideo
        video_params = parse_video_params(decoded_id)
        logger.info(f"Video params: {video_params}")
        
        # Pobieranie nazwy pliku z parametrów żądania
        video_filename = request.args.get('videoFileName')
        if video_filename:
            fill_item_from_name(video_filename, item)
        
        # Pobieranie metadanych z OMDB jeśli brak tytułu
        if not item.get('title') and not item.get('tvshow'):
            try:
                omdb_response = requests.get(
                    f"https://www.omdbapi.com/?i={base_imdb_id}&apikey={OMDB_API_KEY}",
                    timeout=10
                ).json()
                
                if omdb_response.get('Response') == 'True':
                    title = omdb_response.get('Title', '').strip()
                    year = omdb_response.get('Year', '').split('–')[0].strip()
                    
                    if omdb_response.get('Type') == 'series':
                        item['tvshow'] = title
                        if 'season' not in item:
                            item['season'] = '1'
                    else:
                        item['title'] = title
                    
                    if year:
                        item['year'] = year
                        
                    logger.info(f"OMDB data: {title} ({year})")
            except Exception as e:
                logger.error(f"OMDB API error: {str(e)}")

        # Parsowanie sezonu i odcinka dla seriali
        if content_type == 'series':
            season_episode_match = re.search(r'[:/](\d+)[:/](\d+)', decoded_id)
            if season_episode_match:
                item['season'] = season_episode_match.group(1)
                item['episode'] = season_episode_match.group(2)
                if not item.get('tvshow'):
                    item['tvshow'] = item.pop('title', base_imdb_id)
        
        logger.info(f"Searching with item data: {item}")
        
        # Wyszukiwanie napisów
        found_subtitles = napi_helper.search(item, base_imdb_id)
        stremio_subtitles = []
        
        for sub in found_subtitles:
            sub_id = f"{base_imdb_id}_{sub['link_hash']}_{sub['language']}"
            
            stremio_subtitles.append({
                "id": sub_id,
                "url": f"{request.url_root}subtitles/download/{sub_id}.srt",
                "lang": sub['language'],
                "name": sub.get('label', 'NapiProjekt'),
            })
            
        logger.info(f"Found {len(stremio_subtitles)} subtitles for {base_imdb_id}")
        return jsonify({"subtitles": stremio_subtitles})
        
    except Exception as e:
        logger.error(f"Error in get_subtitles: {str(e)}", exc_info=True)
        return jsonify({"subtitles": []})

@app.route('/subtitles/download/<sub_id>.srt')
def download_subtitle_file(sub_id):
    """Endpoint do pobierania napisów"""
    try:
        parts = sub_id.split('_')
        if len(parts) < 3:
            logger.warning(f"Invalid subtitle ID format: {sub_id}")
            return "Invalid subtitle ID", 400

        napiprojekt_hash = parts[-2]
        logger.info(f"Downloading subtitle with hash: {napiprojekt_hash}")
        
        for attempt in range(3):
            try:
                start_time = time.time()
                subtitle_content = napi_helper.download(napiprojekt_hash)
                
                if subtitle_content:
                    if not subtitle_content.startswith('\ufeff'):
                        subtitle_content = '\ufeff' + subtitle_content
                    
                    subtitle_content = subtitle_content.replace('\r\n', '\n').replace('\r', '\n')
                    
                    logger.info(f"Successfully downloaded subtitle {sub_id} in {time.time()-start_time:.2f}s")
                    
                    return Response(
                        subtitle_content,
                        mimetype='text/plain; charset=utf-8',
                        headers={
                            'Content-Disposition': f'attachment; filename="{sub_id}.srt"',
                            'Cache-Control': 'max-age=86400',
                        }
                    )
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Attempt {attempt+1} failed for {sub_id}: {str(e)}")
                time.sleep(1)
        
        logger.error(f"Failed to download valid subtitle after 3 attempts: {sub_id}")
        return "Subtitle download failed", 404
        
    except Exception as e:
        logger.error(f"Critical error downloading subtitle {sub_id}: {str(e)}", exc_info=True)
        return "Internal server error", 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Server error: {str(error)}", exc_info=True)
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    logger.info("Starting optimized Stremio NapiProjekt addon")
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
    serve(app, host='0.0.0.0', port=7002)
