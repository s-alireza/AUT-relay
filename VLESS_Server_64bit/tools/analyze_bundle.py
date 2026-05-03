import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE_PATH = os.path.join(BASE_DIR, "ui", "main.bundle.js")

t = open(BUNDLE_PATH, 'r', encoding='utf-8').read()

# Search for form action or any URL-like patterns near login
print("=== Form action attributes ===")
hits = re.findall(r'action\s*[=:]\s*["\x27]([^"\x27]+)["\x27]', t)
for h in set(hits):
    print(f"  {h}")

# Search for POST/GET method calls with URLs
print("\n=== HTTP method calls ===")
hits = re.findall(r'\.(?:post|get|put|delete)\s*\(\s*["\x27]([^"\x27]+)["\x27]', t)
for h in set(hits):
    if len(h) < 100:
        print(f"  {h}")

# Search for any /api or endpoint-like paths
print("\n=== URL-like paths ===")
hits = re.findall(r'["\x27]((?:https?://|/)[^"\x27]{3,60})["\x27]', t)
api_hits = [h for h in set(hits) if any(kw in h.lower() for kw in ['api', 'login', 'logout', 'auth', 'status', 'user', 'session'])]
for h in api_hits:
    print(f"  {h}")

# Search for XMLHttpRequest or fetch
print("\n=== XHR/Fetch patterns ===")
hits = re.findall(r'(?:XMLHttpRequest|fetch)\s*\([^)]{0,100}\)', t)
for h in hits[:10]:
    print(f"  {h}")

# Find what happens on form submit - larger context
print("\n=== Form submit context (2000 chars) ===")
idx = t.find('form[name=login]')
if idx >= 0:
    # Go back further to find the module that creates the form
    chunk = t[max(0,idx-3000):idx+1000]
    # Find the start of this module
    module_starts = [m.start() for m in re.finditer(r'\d+:\s*(?:\(|function)', chunk)]
    if module_starts:
        start = module_starts[-1]
        print(chunk[start:])
