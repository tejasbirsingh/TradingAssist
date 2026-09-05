"""Verify the Breeze credentials and session token work end to end.

Read-only: fetches customer details, funds, and a single quote. Places no orders.
"""

import os
import sys

from breeze_connect import BreezeConnect
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BREEZE_API_KEY")
SECRET_KEY = os.getenv("BREEZE_SECRET_KEY")
SESSION_TOKEN = os.getenv("BREEZE_SESSION_TOKEN")

missing = [
    name
    for name, value in (
        ("BREEZE_API_KEY", API_KEY),
        ("BREEZE_SECRET_KEY", SECRET_KEY),
        ("BREEZE_SESSION_TOKEN", SESSION_TOKEN),
    )
    if not value
]
if missing:
    sys.exit(f"Missing in .env: {', '.join(missing)}")


def show(label, response):
    """Print a Breeze response, surfacing its status and error field."""
    status = response.get("Status") if isinstance(response, dict) else None
    error = response.get("Error") if isinstance(response, dict) else None
    print(f"\n--- {label} (Status={status}) ---")
    if error:
        print(f"Error: {error}")
    else:
        print(response)


breeze = BreezeConnect(api_key=API_KEY)
breeze.generate_session(api_secret=SECRET_KEY, session_token=SESSION_TOKEN)
print("Session generated.")

show("customer details", breeze.get_customer_details(api_session=SESSION_TOKEN))
show("funds", breeze.get_funds())
show(
    "quote: RELIANCE (NSE cash)",
    breeze.get_quotes(
        stock_code="RELIND",
        exchange_code="NSE",
        product_type="cash",
        right="others",
        strike_price="0",
    ),
)
