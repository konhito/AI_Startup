"""Start the local gateway and website using .env (no shell evaluation)."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
config = ROOT / '.env'
if config.exists():
    for raw in config.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app.main:app', host='127.0.0.1', port=int(os.getenv('PORT', '8080')), workers=1)
