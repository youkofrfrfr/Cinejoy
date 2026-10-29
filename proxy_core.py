from flask import Flask, request, Response, send_from_directory
from proxy_core import fetch_upstream
import os

app = Flask(__name__, static_folder='.')

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'OPTIONS'])
def proxy(path):
    # Serve static files directly if they exist locally (like index.html)
    if path in ['', 'index.html', 'sec.html', 'favicon.ico'] or path.startswith('static/'):
        if os.path.exists(path) and path != '':
            return send_from_directory('.', path)
        return send_from_directory('.', 'index.html')

    # Handle CORS preflight
    if request.method == 'OPTIONS':
        return Response('', status=204, headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': '*',
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'
        })

    # Proxy all other requests (including /cinejoy and Svelte assets)
    status, headers, payload = fetch_upstream(
        path=path,
        query=request.query_string.decode('utf-8'),
        method=request.method,
        request_headers=dict(request.headers),
        body=request.get_data() if request.method == 'POST' else None
    )

    return Response(payload, status=status, headers=headers)

if __name__ == '__main__':
    print("Proxy running on http://localhost:4173")
    app.run(port=4173, debug=True)
