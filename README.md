# Cinejoy Wrapper

## Deploy to Vercel

1. Import this folder as a project in Vercel.
2. Select **Other** as the framework preset. No build command or output directory is required.
3. Deploy. Vercel serves `index.html` and runs `api/proxy.py` as a Python Function.

The preview server is for local use: run `python3 server.py` and open `http://localhost:4173`.

The proxy serves the Cinejoy catalog and removes its known ad-loader bootstrap. Third-party playback sources remain controlled by their providers and may require their own valid media URL and CORS configuration.