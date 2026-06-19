import json
import re

# Paste the full cURL command between the triple quotes below
curl = """
"""

headers = {}

for m in re.finditer(r"-H '([^']+?)'", curl):
    line = m.group(1)
    if ': ' in line and not line.startswith(':'):
        key, value = line.split(': ', 1)
        headers[key] = value

# Chrome puts cookies in -b instead of -H
cookie = re.search(r"-b '([^']+)'", curl)
if cookie:
    headers['cookie'] = cookie.group(1)

if 'cookie' not in headers:
    raise ValueError("Cookie not found in cURL. Make sure you copied the full command.")
if 'x-goog-authuser' not in headers:
    raise ValueError("x-goog-authuser not found. Try a different browse request.")

with open('browser.json', 'w') as f:
    json.dump(headers, f, indent=2)

print(f"browser.json created with {len(headers)} headers.")
