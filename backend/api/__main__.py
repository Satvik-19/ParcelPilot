"""Run the application locally: ``python -m backend.api [port]``.

Serves the chat UI, the insights view and the JSON API from one process.
The Groq key comes from the environment / gitignored .env (backend.agent.
config); nothing about it is printed.
"""

import sys

from .app import ParcelPilotApp
from .server import serve


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    app = ParcelPilotApp()
    server = serve(app, port=port)
    print(f"ParcelPilot running at http://127.0.0.1:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()


if __name__ == "__main__":
    main()
