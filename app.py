import os
import logging
from flask import Flask, jsonify, Response, request
from waitress import serve
from napiprojekt_logic import NapiProjektKatalog
import utils

# Logowanie
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)
napi = NapiProjektKatalog()

MANIFEST = {
    "id": "org.napiprojekt.v3",
    "version": "1.0.0",
    "name": "NapiProjekt Cloud",
    "description": "Napisy bezpośrednio z NapiProjektu",
    "resources": ["subtitles"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt"]
}

@app.route("/manifest.json")
def manifest():
    return jsonify(MANIFEST)

@app.route("/subtitles/<mtype>/<imdb_id>.json")
def subtitles(mtype, imdb_id):
    imdb_id_clean = imdb_id.split(":")[0]
    log.info(f"Request subtitles for: {imdb_id_clean}")
    
    # Pobieramy info o tytule z Cinemeta przez utils.py
    item = utils.get_movie_info(imdb_id_clean)
    results = napi.search(item, imdb_id_clean)
    
    subtitles_list = []
    for s in results:
        sub_id = s['link_hash']
        download_url = f"{request.host_url}subtitles/download/{sub_id}.srt"
        
        subtitles_list.append({
            "id": sub_id,
            "url": download_url,
            "lang": "pol",
            "name": f"{s['label']}"
        })
        
    return jsonify({"subtitles": subtitles_list})

@app.route("/subtitles/download/<path:subid>")
def download_subtitles(subid):
    encoded_query = subid.replace(".srt", "")
    # Pobieramy i automatycznie konwertujemy do SRT
    raw_content = napi.download(encoded_query)
    
    if raw_content:
        content = utils.auto_convert_to_srt(raw_content)
        log.info("Napisy przygotowane pomyślnie.")
        return Response(content, mimetype='text/plain', headers={
            "Content-Disposition": "attachment; filename=subtitles.srt"
        })
    
    return "NapiProjekt: Napisów nie znaleziono", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7002))
    log.info(f"Uruchamiam serwer na porcie {port}...")
    serve(app, host="0.0.0.0", port=port)
