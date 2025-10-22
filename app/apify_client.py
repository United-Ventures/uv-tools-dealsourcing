import os
from apify_client import ApifyClient

def get_apify() -> ApifyClient:
    token = os.getenv("APIFY_TOKEN", "")
    if not token:
        raise RuntimeError("APIFY_TOKEN assente")
    return ApifyClient(token)
