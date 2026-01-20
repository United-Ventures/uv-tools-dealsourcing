import os
import json
import re
import sys
import time
import uuid
import logging
from urllib.parse import quote_plus

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

from .schemas import (
    SerpRequest,
    SerpResponse,
    PeopleRow,
    EnrichRequest,
    EnrichResponse,
    MasterRow,
    ScoreRequest,
    ScoreResponse,
    ScoredRow,
)
from .apify_client import get_apify
from .transform import items_to_people, items_to_master


# -------------------------------------------------
# Logging
# -------------------------------------------------
def _get_uvds_logger():
    """
    Usa il logger 'uvds' se esiste/configurato.
    Altrimenti fallback al root logger.
    """
    lg = logging.getLogger("uvds")
    if not lg.handlers and not logging.getLogger().handlers:
        h = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        h.setFormatter(fmt)
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
    return lg


logger = _get_uvds_logger()
level = os.getenv("LOG_LEVEL", "INFO").upper()
logger.setLevel(getattr(logging, level, logging.INFO))


# -------------------------------------------------
# Helpers (FIX: URL cleanup + scoring guard)
# -------------------------------------------------
def _normalize_domain_or_path(s: str | None) -> str:
    """
    Accepts:
      - 'linkedin.com'
      - 'linkedin.com/in'
      - 'https://www.linkedin.com/in'
    Returns:
      - 'linkedin.com'
      - 'linkedin.com/in'
    """
    if not s:
        return "linkedin.com/in"
    x = str(s).strip()
    x = re.sub(r"^https?://", "", x, flags=re.I)
    x = re.sub(r"^www\.", "", x, flags=re.I)
    x = x.strip().strip("/")
    return x or "linkedin.com/in"


def _canonical_linkedin_in(url: str | None) -> str | None:
    """
    Keeps ONLY canonical linkedin profile urls:
      https://www.linkedin.com/in/<slug>
    Returns None if not valid.
    """
    if not url:
        return None

    u = str(url).strip()
    if not u:
        return None

    # allow inputs like 'linkedin.com/in/slug'
    if u.startswith("linkedin.com/"):
        u = "https://www." + u

    # normalize scheme
    u = u.replace("http://", "https://")

    # strict match for /in/ slug
    m = re.match(r"^https?://(www\.)?linkedin\.com/in/([^/?#]+)", u, flags=re.I)
    if not m:
        return None

    slug = m.group(2)
    return f"https://www.linkedin.com/in/{slug}"


def _safe_json_extract(s):
    try:
        if isinstance(s, (dict, list)):
            return s
        if not isinstance(s, str):
            s = "" if s is None else str(s)
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s or "", re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {"error": "bad_json", "raw": (s or "")[:1000]}
        return {"error": "no_json", "raw": (s or "")[:500]}


def _months_from_duration(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d+)\s*(?:mos?|mesi|month|months)", text, flags=re.I)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    y = re.search(r"(\d+)\s*(?:yrs?|anni|year|years)", text, flags=re.I)
    if y:
        try:
            return int(y.group(1)) * 12
        except Exception:
            return None
    return None


def _has_enough_signal(r) -> bool:
    """
    Decide if it's worth calling OpenAI.
    Prevents 0/10 on empty/incomplete enrich rows.
    """
    full_name = getattr(r, "fullName", None)
    headline = getattr(r, "headline", None)

    # if both missing -> definitely skip
    if not (full_name or headline):
        return False

    exp = getattr(r, "experiences_full", None)
    edu = getattr(r, "education_text", None)
    role = getattr(r, "current_role", None)
    comp = getattr(r, "current_company", None)

    if exp and str(exp).strip():
        return True
    if edu and str(edu).strip():
        return True
    if role and str(role).strip():
        return True
    if comp and str(comp).strip():
        return True

    return False


# -------------------------------------------------
# App
# -------------------------------------------------
app = FastAPI(title="UV Deal Sourcing API", version="0.2.0")

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def health():
    return {"ok": True}


# -------------------------------------------------
# SERP (FIX: uses site_filter)
# -------------------------------------------------
@app.post("/v1/serp", response_model=SerpResponse)
def serp(req: SerpRequest):
    if not req.keywords:
        raise HTTPException(400, "keywords vuoto")

    # --- 1. Costruzione keyword ---
    keywords_query = " OR ".join([f'"{kw}"' for kw in req.keywords])

    # --- 2. Geografia ---
    country = (req.country_code or "").strip().lower()
    if country == "uk":
        country = "gb"

    GEO_CONFIG = {
        "it": {"google_domain": "google.it", "gate": '("Italy" OR "Italia")', "language": "it"},
        "fr": {"google_domain": "google.fr", "gate": '("France" OR "Français")', "language": "fr"},
        "gb": {"google_domain": "google.co.uk", "gate": '("United Kingdom" OR "UK" OR "Britain")', "language": "en"},
        "us": {"google_domain": "google.com", "gate": '("United States" OR "USA")', "language": "en"},
        "eu": {"google_domain": "google.com", "gate": '("Europe" OR "European Union")', "language": "en"},
    }

    geo = GEO_CONFIG.get(country)

    # --- 3. Query finale (FIX) ---
    # FE invia site_filter, ma prima lo ignoravi: ora lo usiamo.
    site_filter = _normalize_domain_or_path(getattr(req, "site_filter", None))

    if geo:
        query_text = f'site:{site_filter} ({keywords_query}) {geo["gate"]}'
        google_url = f'https://www.{geo["google_domain"]}/search?q={quote_plus(query_text)}'
        country_code = country
        language_code = geo["language"]
    else:
        query_text = f"site:{site_filter} ({keywords_query})"
        google_url = query_text  # actor accetta anche query raw
        country_code = ""
        language_code = None

    # --- 4. Apify call ---
    try:
        apify = get_apify()

        apify_input = {
            "queries": google_url,
            "countryCode": country_code,
            "maxPagesPerQuery": int(req.max_pages),
            "mobileResults": False,
        }

        if language_code:
            apify_input["languageCode"] = language_code

        run = apify.actor("apify/google-search-scraper").call(run_input=apify_input)
        dataset_id = run["defaultDatasetId"]
        items = list(apify.dataset(dataset_id).iterate_items())

    except Exception as e:
        import traceback

        print("APIFY ERROR:", e, file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(502, detail=f"Errore Apify: {str(e)}")

    # --- 5. Transform ---
    people_rows = items_to_people(items)

    return SerpResponse(
        query=query_text,
        count_pages=req.max_pages,
        people=[PeopleRow(**r) for r in people_rows],
        raw_items_count=len(items),
    )


# -------------------------------------------------
# ENRICH (FIX: canonicalize + filter linkedin.com/in)
# -------------------------------------------------
@app.post("/v1/enrich", response_model=EnrichResponse)
def enrich(req: EnrichRequest):
    if not req.linkedin_urls:
        raise HTTPException(400, "linkedin_urls vuoto")

    # FIX: canonicalize + filter
    cleaned = []
    for u in req.linkedin_urls:
        cu = _canonical_linkedin_in(str(u))
        if cu:
            cleaned.append(cu)

    # dedup preserving order
    cleaned = list(dict.fromkeys(cleaned))

    if not cleaned:
        raise HTTPException(400, "No valid linkedin.com/in URLs after cleaning")

    try:
        apify = get_apify()
        run = apify.actor("2SyF0bVxmgGr8IVCZ").call(
            run_input={"profileUrls": cleaned}
        )
        dataset_id = run["defaultDatasetId"]
        items = list(apify.dataset(dataset_id).iterate_items())
    except Exception as e:
        import traceback

        print("APIFY ENRICH ERROR:", e, file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(502, detail=f"Errore Apify: {str(e)}")

    master_rows = items_to_master(items)

    # optional debug metric: how many are basically empty
    empty_like = 0
    for r in master_rows:
        if not (
            getattr(r, "fullName", None)
            or getattr(r, "headline", None)
            or getattr(r, "experiences_full", None)
            or getattr(r, "education_text", None)
        ):
            empty_like += 1
    logger.info("Enrich summary", extra={"count": len(master_rows), "empty_like": empty_like})

    return EnrichResponse(
        count=len(master_rows),
        people_master=[MasterRow(**r) for r in master_rows],
    )


# -------------------------------------------------
# SCORING
# -------------------------------------------------
SCORING_RULES_TEXT = (
    "Evaluate whether the person should be contacted for a potential investment (score 0–10). "
    "If the person is a CEO, technical background carries less weight. "
    "If the person is a CTO, business background carries less weight. "
    "Assign 1 point for each criterion satisfied: "
    "1) Founder or co-founder experience; "
    "2) Current or past C-level roles (CEO/CTO/COO/etc.); "
    "3) Traction or validation signals: product launched, customers/pilots/LOIs, revenue, user growth, "
    "relevant partnerships, fundraising or grants (including pre-seed/seed), or clear performance metrics; "
    "4) Top-tier universities (Ivy League, Oxbridge, Stanford, MIT, ETH, EPFL, Bocconi, "
    "Politecnico di Milano, Politecnico di Torino, Sapienza University of Rome, "
    "Scuola Superiore Sant'Anna, Scuola Normale Superiore, etc.); "
    "5) Technical background (CS/AI/engineering/deep tech) OR business background from top business schools "
    "or prior experience managing large teams; "
    "6) Serial entrepreneur (≥2 startup founder experiences) or second-time founder; "
    "7) Experience in big tech / unicorns / high-growth scaleups (e.g., FAANG, unicorns, ScaleUps); "
    "8) Strong network (high number of followers or connections > 5,000); "
    "9) Current role with high responsibility, especially if the company has raised investment rounds larger than €2M; "
    "10) Recent momentum: current role or company/product launch started within the last 24 months. "
    'Return ONLY valid JSON with the following structure: '
    '{"score": int 0-10, "reasons": short string in English, "contact": boolean (true if score >= 7)}.'
)

TOP_UNI_CANONICAL = [
    "Harvard University",
    "Stanford University",
    "Massachusetts Institute of Technology",
    "University of Oxford",
    "University of Cambridge",
    "ETH Zurich",
    "EPFL",
    "University of Bologna",
    "Università Bocconi",
    "Politecnico di Milano",
    "Politecnico di Torino",
    "Sapienza University of Rome",
    "Scuola Superiore Sant'Anna",
    "Scuola Normale Superiore",
    "ESCP Business School",
    "London Business School",
    "INSEAD",
    "HEC Paris",
    "other top-tier universities worldwide",
]


@app.post("/v1/score", response_model=ScoreResponse)
def score(req: ScoreRequest):
    request_id = str(uuid.uuid4())
    start_ts = time.time()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENAI_API_KEY non configurata")

    model = req.model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)

    rows = req.people_master or []
    limit = int(req.max_rows) if req.max_rows else len(rows)
    rows = rows[:limit]

    logger.info("Scoring started", extra={"request_id": request_id, "rows": len(rows), "model": model})

    people_scored = []

    system_msg = (
        SCORING_RULES_TEXT
        + " Also consider the following list of top-tier universities as a reference: "
        + ", ".join(TOP_UNI_CANONICAL)
        + "."
    )

    for i, r in enumerate(rows):
        logger.info(
            "Scoring person",
            extra={
                "request_id": request_id,
                "idx": i,
                "fullName": getattr(r, "fullName", None),
                "linkedinUrl": str(getattr(r, "linkedinUrl", None)) if getattr(r, "linkedinUrl", None) else None,
                "company": getattr(r, "current_company", None),
                "role": getattr(r, "current_role", None),
            },
        )

        # FIX: skip rows with insufficient enrich to avoid fake 0/10
        if not _has_enough_signal(r):
            base = r.model_dump()
            base.update(
                {
                    "score": None,  # IMPORTANT: keep None -> UI shows "-" not "0/10"
                    "reasons": "Insufficient profile data (enrich incomplete).",
                    "contact": False,
                }
            )
            people_scored.append(ScoredRow(**base))
            logger.info(
                "Scoring skipped (insufficient data)",
                extra={"request_id": request_id, "idx": i, "fullName": getattr(r, "fullName", None)},
            )
            continue

        payload = {
            "fullName": getattr(r, "fullName", None),
            "headline": getattr(r, "headline", None),
            "current_role": getattr(r, "current_role", None),
            "current_company": getattr(r, "current_company", None),
            "months_in_current": _months_from_duration(getattr(r, "current_duration", None)),
            "education": getattr(r, "education_text", None),
            "experience": getattr(r, "experiences_full", None),
            "skills": getattr(r, "skills", None),
            "followers": getattr(r, "followers", None),
            "connections": getattr(r, "connections", None),
        }

        score_val = None
        reasons = None
        contact = None

        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            )
            latency_ms = int((time.time() - t0) * 1000)

            content = resp.choices[0].message.content if resp.choices else ""
            data = _safe_json_extract(content)

            if isinstance(data, dict):
                s = data.get("score")
                if str(s).isdigit():
                    score_val = int(s)
                reasons = data.get("reasons")
                contact = bool(data.get("contact")) if "contact" in data else (
                    score_val is not None and score_val >= 7
                )

            logger.info(
                "Scoring result",
                extra={
                    "request_id": request_id,
                    "idx": i,
                    "fullName": getattr(r, "fullName", None),
                    "score": score_val,
                    "contact": contact,
                    "reason": (reasons or "")[:120],
                    "openai_ms": latency_ms,
                },
            )

        except Exception as e:
            logger.error(
                "Scoring failed",
                extra={"request_id": request_id, "idx": i, "fullName": getattr(r, "fullName", None), "error": str(e)},
                exc_info=True,
            )

        base = r.model_dump()
        base.update({"score": score_val, "reasons": reasons, "contact": contact})
        people_scored.append(ScoredRow(**base))

    top5 = sorted(
        people_scored,
        key=lambda p: (p.score is not None, p.score),
        reverse=True,
    )[:5]

    logger.info(
        "Scoring finished",
        extra={
            "request_id": request_id,
            "duration_ms": int((time.time() - start_ts) * 1000),
            "top5": [{"name": p.fullName, "score": p.score} for p in top5],
        },
    )

    return ScoreResponse(
        count_in=len(req.people_master or []),
        count_scored=len(people_scored),
        model_used=model,
        people_scored=people_scored,
    )
