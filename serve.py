"""Production server (Windows-friendly):  python serve.py"""
import os

from waitress import serve

from portal.app import create_app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"Serving on http://0.0.0.0:{port}")
    serve(create_app(), host="0.0.0.0", port=port, threads=8)
