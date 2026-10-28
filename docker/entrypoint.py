"""Start the local web app with private data-file permissions."""
import os
import sys
from pathlib import Path

os.umask(0o077)
root = Path(os.environ.get('XHS_LOCAL_DATA', '/data'))
root.mkdir(parents=True, exist_ok=True)
if not os.access(root, os.W_OK):
    raise SystemExit('The data directory must be writable by container UID 10001.')
os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'webapp.app:create_app', '--factory',
                        '--host', '0.0.0.0', '--port', '8777', '--no-access-log', '--no-proxy-headers'])
