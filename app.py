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
    if not name:
        return
        
    try:
        # First try to clean up the filename by removing extensions and common patterns
        clean_name = re.sub(r'\.(mp4|avi|mkv|mov|wmv)$', '', name, flags=re.IGNORECASE)
        clean_name = re.sub(r'[._\-]', ' ', clean_name)
        clean_name = clean_name.strip()
        
        # Try TV show pattern (S01E02 format)
        tv_match = re.search(r'(.*?)[. ](?:S|s)(\d{1,2})(?:E|e)(\d{1,2}).*', clean_name, re.IGNORECASE)
        if tv_match:
            item['tvshow'] = tv_match.group(1).strip()
            item['season'] = str(int(tv_match.group(2)))
            item['episode'] = str(int(tv_match.group(3)))
            return

        # Try movie pattern (Title Year format)
        movie_match = re.search(r'(.+?)\s*\(?(\d{4})\)?', clean_name)
        if movie_match:
            item['title'] = movie_match.group(1).strip()
            item['year'] = movie_match.group(2)
            return
            
        # Fallback - try to extract just the title (everything before the first dot or special char)
        fallback_match = re.search(r'^([^._\-]+)', clean_name)
        if fallback_match:
            item['title'] = fallback_match.group(1).strip()
            
    except Exception as e:
        logger.error(f"Error parsing filename: {str(e)}")

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

@app.route('/')
def index():
    return jsonify({
        "message": "NapiProjekt Stremio Addon (Python)",
        "manifest": f"{request.url_root}manifest.json",
        "version": "1.3.0"
    })

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "id": "org.stremio.napiprojekt.python",
        "version": "1.3.0",
        "name": "NapiProjekt PL",
        "description": "Napisy PL z NapiProjekt - czas trwania, FPS, pobrania",
        "logo": "https://d3npyywa6qnolf.cloudfront.net/prod/user/337361/eyJ1cmwiOiJodHRwczpcL1wvcGF0cm9uaXRlLnBsXC91cGxvYWRcL3VzZXJcLzMzNzM2MVwvYXZhdGFyX29yaWcuanBnPzE1ODk5NjY3NjMiLCJlZGl0cyI6eyJyZXNpemUiOnsid2lkdGgiOjI5MH0sInRvRm9ybWF0Ijoid2VicCJ9fQ%3D%3D/wIDQ1%2FJ8QvTzsI29KHkeWnO5G0BuCBY00wpksnlpJfs%3D",
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
    try:
        item = {}
        decoded_id = urllib.parse.unquote(imdb_id_with_params)
        
        # Extract base IMDB ID (ttXXXXXXX) even if there are additional parameters
        base_imdb_id = re.search(r'(tt\d+)', decoded_id).group(1) if re.search(r'(tt\d+)', decoded_id) else decoded_id.split(':')[0]
        
        item['imdb_id'] = base_imdb_id
        video_filename = request.args.get('filename') or request.args.get('videoFileName')
        
        if video_filename:
            fill_item_from_name(video_filename, item)
        
        if not item.get('title') and not item.get('tvshow') and base_imdb_id.startswith('tt'):
            try:
                omdb_response = requests.get(
                    f"https://www.omdbapi.com/?i={base_imdb_id}&apikey={OMDB_API_KEY}",
                    timeout=10
                ).json()
                
                if omdb_response.get('Response') == 'True':
                    item['title'] = omdb_response.get('Title', '').strip()
                    item['year'] = omdb_response.get('Year', '').strip()
                    if omdb_response.get('Type') == 'series':
                        item['tvshow'] = item.pop('title', None)
            except Exception as e:
                logger.error(f"OMDB API error: {str(e)}")

        if content_type == 'series' and len(decoded_id.split(':')) > 2:
            parts = decoded_id.split(':')
            item['season'] = parts[1]
            item['episode'] = parts[2].split('/')[0]
            if not item.get('tvshow'):
                item['tvshow'] = item.pop('title', base_imdb_id)
        
        found_subtitles = napi_helper.search(item, base_imdb_id)
        stremio_subtitles = []
        
        for sub in found_subtitles:
            sub_id = f"{base_imdb_id}_{sub['link_hash']}_{sub['language']}"
            
            # Build compact metadata string
            meta_parts = []
            if sub.get('duration_text') and sub['duration_text'].lower() != 'b.d.':
                meta_parts.append(f"{sub['duration_text']}")
            if sub.get('fps'):
                meta_parts.append(f"{sub['fps']}fps")
            if sub.get('downloads'):
                meta_parts.append(f"⬇{sub['downloads']}")
            
            metadata = " ".join(meta_parts) if meta_parts else "NapiProjekt"
            
            stremio_subtitles.append({
                "id": sub_id,
                "url": f"{request.url_root}subtitles/download/{sub_id}.srt",
                "lang": sub['language'],
                "name": metadata,
            })
            
        logger.info(f"Found {len(stremio_subtitles)} subtitles for {base_imdb_id}")
        return jsonify({"subtitles": stremio_subtitles})
        
    except Exception as e:
        logger.error(f"Error in get_subtitles: {str(e)}", exc_info=True)
        return jsonify({"subtitles": []})

@app.route('/subtitles/download/<sub_id>.srt')
def download_subtitle_file(sub_id):
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
