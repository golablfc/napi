import os
import logging
import base64
from flask import Flask, jsonify, Response, request
from waitress import serve # Produkcyjny serwer
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
    
    # Pobieramy dane o filmie z Cinemeta
    item = utils.get_movie_info(imdb_id_clean)
    
    # Szukamy opcji w NapiProjekt
    results = napi.search(item, imdb_id_clean)
    
    subtitles_list = []
    for s in results:
        # Tworzymy link do pobrania
        sub_id = s['link_hash']
        download_url = f"{request.host_url}subtitles/download/{sub_id}.srt"
        
        subtitles_list.append({
            "id": sub_id,
            "url": download_url,
            "lang": "pol",
            "name": f"{s['label']} [{s.get('_duration', '??:??:??')}]"
        })
        
    return jsonify({"subtitles": subtitles_list})

@app.route("/subtitles/download/<path:subid>")
def download_subtitles(subid):
    encoded_query = subid.replace(".srt", "")
    content = napi.download(encoded_query)
    
    if content:
        log.info("Napisy pobrane pomyślnie, wysyłam do Stremio.")
        # Opcjonalnie: utils.convert_to_srt(content) jeśli format jest inny
        return Response(content, mimetype='text/plain', headers={
            "Content-Disposition": "attachment; filename=subtitles.srt"
        })
    
    return "NapiProjekt: Napisów nie znaleziono lub błąd serwera", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7002))
    log.info(f"Uruchamiam serwer na porcie {port}...")
    # Użycie Waitress dla stabilności na Renderze
    serve(app, host="0.0.0.0", port=port)
