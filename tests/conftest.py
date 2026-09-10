"""Keep the test suite hermetic.

bot.py loads the project .env on import, so a live WHOS_TV_ACCOUNTS_FILE (or
any other integration) would leak into every test and make it hit the network.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Never inherit live integrations unless a test sets them explicitly.
os.environ.setdefault('WHOS_TV_ACCOUNTS_FILE', '')
