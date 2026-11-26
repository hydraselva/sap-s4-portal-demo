"""Start the portal:  python run.py   (then open http://127.0.0.1:5000)"""
import os

from portal.app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n  SAP portal running on http://{host}:{port}  (SAP mode: {app.config['SETTINGS'].sap_mode})\n")
    app.run(host=host, port=port, debug=os.environ.get("FLASK_DEBUG") == "1", threaded=True)
