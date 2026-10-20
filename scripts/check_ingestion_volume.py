"""Regression check for the worker/API shared SQLite volume (no external source)."""
import json
import time
import urllib.request


def main():
    for attempt in range(10):
        try:
            with urllib.request.urlopen('http://localhost:8000/api/ingestion', timeout=5) as response:
                state = json.load(response)
            assert state['state'] == 'running', state
            assert state['pending'] == 1, state
            assert state['indexed'] == 0, state
            print('PASS: worker SQLite WAL state is readable by API through its shared volume')
            return
        except (OSError, AssertionError):
            if attempt == 9:
                raise
            time.sleep(1)


if __name__ == '__main__':
    main()
