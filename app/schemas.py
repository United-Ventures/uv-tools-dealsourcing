# app/schemas.py
from typing import List, Optional
from pydantic import BaseModel, AnyHttpUrl, field_validator
import re

# -----------------------
# Helpers
# -----------------------
def _ensure_http_scheme(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if not re.match(r"^https?://", s, flags=re.I):
        s = "https://" + s.lstrip("/")
    return s

def _to_int_safe(val) -> Optional[int]:
    """Converte stringhe tipo 'Oltre 6.630 follower' o '12k' in int, altrimenti None."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    s = str(val).strip()
    mk = re.search(r"(\d+(?:\.\d+)?)\s*[kK]\b", s)
    if mk:
        try:
            return int(float(mk.group(1)) * 1000)
        except Exception:
            pass
    m = re.search(r"\d[\d\.\,\s]*", s)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(0))
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None

# -----------------------
# SERP
# -----------------------
class SerpRequest(BaseModel):
    keywords: List[str]
    country_code: str = "it"
    site_filter: str = "it.linkedin.com"
    max_pages: int = 1

class PeopleRow(BaseModel):
    Nome: str
    Cognome: str
    Title: Optional[str] = None
    Snippet: Optional[str] = None
    Location: Optional[str] = None
    Followers: Optional[int] = None
    LinkedIn: AnyHttpUrl  # normalizzato lato transform

    # normalizza Followers se Apify ritorna stringa
    @field_validator("Followers", mode="before")
    @classmethod
    def _fix_followers(cls, v):
        return _to_int_safe(v)

class SerpResponse(BaseModel):
    query: str
    count_pages: int
    people: List[PeopleRow]
    raw_items_count: int

# -----------------------
# ENRICH
# -----------------------
class EnrichRequest(BaseModel):
    linkedin_urls: List[AnyHttpUrl]

    @field_validator("linkedin_urls", mode="before")
    @classmethod
    def _fix_urls_list(cls, v):
        if not isinstance(v, list):
            return v
        return [_ensure_http_scheme(item) for item in v]

class MasterRow(BaseModel):
    fullName: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    current_role: Optional[str] = None
    current_company: Optional[str] = None
    current_start: Optional[str] = None
    current_location: Optional[str] = None
    current_duration: Optional[str] = None
    experiences_full: Optional[str] = None
    education_top: Optional[str] = None
    education_text: Optional[str] = None
    skills: Optional[str] = None
    connections: Optional[int] = None
    followers: Optional[int] = None
    email: Optional[str] = None
    mobileNumber: Optional[str] = None

    linkedinUrl: Optional[AnyHttpUrl] = None
    companyLinkedin: Optional[AnyHttpUrl] = None
    profilePicHighQuality: Optional[AnyHttpUrl] = None

    @field_validator("linkedinUrl", "companyLinkedin", "profilePicHighQuality", mode="before")
    @classmethod
    def _fix_single_url(cls, v):
        return _ensure_http_scheme(v)

    @field_validator("followers", "connections", mode="before")
    @classmethod
    def _fix_intish(cls, v):
        return _to_int_safe(v)

class EnrichResponse(BaseModel):
    count: int
    people_master: List[MasterRow]

# -----------------------
# OPENAI Scoring
# -----------------------
class ScoreRequest(BaseModel):
    people_master: List[MasterRow]
    max_rows: int = 400
    model: Optional[str] = None  # opzionale, override dell'ENV

class ScoredRow(MasterRow):
    score: Optional[int] = None
    reasons: Optional[str] = None
    contact: Optional[bool] = None

class ScoreResponse(BaseModel):
    count_in: int
    count_scored: int
    model_used: str
    people_scored: List[ScoredRow]
