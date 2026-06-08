"""Entry point for the NLPL Status backend.

Run directly (``python backend/server.py``) or import ``app`` for testing
(``from server import app``). The app itself is built in ``app.py`` via the
application factory; this file only owns the run loop.
"""
import settings
from app import app

if __name__ == "__main__":
    print(f" * NLPL Status backend on http://{settings.HOST}:{settings.PORT}")
    print(f" * Engine: {settings.UNIFIED_COLLECTION_DIR}")
    print(f" * Data:   {settings.DATA_DIR}")
    app.run(host=settings.HOST, port=settings.PORT, debug=False, threaded=True)
