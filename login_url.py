"""Print the Breeze login URL used to mint a fresh session token.

The session token expires daily, so this is a routine manual step: open the URL,
log in, then copy the ``apisession`` value out of the redirected URL into
``BREEZE_SESSION_TOKEN`` in ``.env``.
"""

import os
import sys
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("BREEZE_API_KEY")
if not api_key:
    sys.exit("BREEZE_API_KEY is not set. Copy .env.example to .env and fill it in.")

# The api_key must be URL-encoded: Breeze keys routinely contain characters
# (+, /, =) that would otherwise break the query string.
print(f"https://api.icicidirect.com/apiuser/login?api_key={quote_plus(api_key)}")
print()
print("Open the URL, log in, then copy the 'apisession' query parameter from the")
print("redirected URL into BREEZE_SESSION_TOKEN in .env")
