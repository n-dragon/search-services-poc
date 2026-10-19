"""Check the private listener locally; never expose it through the public proxy."""
import json
import time
import urllib.request

for attempt in range(15):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8001/api/summary', timeout=3) as response:
            data = json.load(response)
        assert 'view' in data['totals']
        print('PASS: private analytics listener and persistent database')
        break
    except (OSError, AssertionError):
        if attempt == 14: raise
        time.sleep(1)
