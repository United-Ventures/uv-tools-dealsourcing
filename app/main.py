import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .schemas import SerpRequest, SerpResponse, PeopleRow
from .apify_client import get_apify
from .transform import items_to_people

app = FastAPI(title="UV Deal Sourcing API", version="0.1.0")

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS","").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],  # in prod restringi
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/healthz")
def health():
    return {"ok": True}

@app.post("/v1/serp", response_model=SerpResponse)
def serp(req: SerpRequest):
    if not req.keywords:
        raise HTTPException(400, "keywords vuoto")

    keywords_query = " OR ".join([f'"{kw}"' for kw in req.keywords])
    query_text = f'site:{req.site_filter}/in ({keywords_query}) {req.country_code.upper()}'

    try:
        apify = get_apify()
        run = apify.actor("apify/google-search-scraper").call(run_input={
            "queries": query_text,
            "countryCode": req.country_code,
            "maxPagesPerQuery": int(req.max_pages),
            "site": req.site_filter,
        })
        dataset_id = run["defaultDatasetId"]
        items = list(apify.dataset(dataset_id).iterate_items())
    except Exception as e:
        raise HTTPException(502, f"Errore Apify: {e}")

    people_rows = items_to_people(items)
    return SerpResponse(
        query=query_text,
        count_pages=req.max_pages,
        people=[PeopleRow(**r) for r in people_rows],
        raw_items_count=len(items),
    )
