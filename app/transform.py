# app/transform.py
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ----------------------------
# Utilities
# ----------------------------
def normalize_linkedin_url(u: str) -> str:
    try:
        p = urlparse(u or "")
        scheme = "https"
        host = (p.hostname or "").lower()
        host = (
            host.replace("www.linkedin.", "linkedin.")
                .replace("it.linkedin.", "linkedin.")
                .replace("es.linkedin.", "linkedin.")
                .replace("uk.linkedin.", "linkedin.")
        )
        path = (p.path or "").rstrip("/")
        return f"{scheme}://{host}{path}"
    except Exception:
        return (u or "").strip()

def split_name_from_title(title: str) -> Tuple[str, str]:
    if not title:
        return "", ""
    first_part = re.split(r"\s[-–—|·•]\s", title, maxsplit=1)[0]
    first_part = re.sub(r"\s+\([^)]*\)$", "", first_part).strip()
    first_part = re.sub(r"^\s*LinkedIn\s*›\s*", "", first_part, flags=re.IGNORECASE).strip()
    tokens = [t for t in re.split(r"\s+", first_part) if t]
    if len(tokens) >= 2:
        return tokens[0], " ".join(tokens[1:])
    return first_part, ""

def _safe_int_from_text(s: Any) -> Optional[int]:
    """
    Converte stringhe tipo:
      - "Oltre 6.630 follower"
      - "Più di 500 collegamenti"
      - "6,360 followers"
      - 1780 (già int)
    in un int pulito. Se non c’è cifra, torna None.
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        try:
            return int(s)
        except Exception:
            return None
    t = str(s)
    m = re.search(r"\d[\d\.,]*", t)
    if not m:
        return None
    raw = m.group(0)
    # Rende gestibili i separatori migliaia/decimali nel contesto EU/US
    # Esempi: "6.630" -> "6630"; "6,360" -> "6360"
    raw = raw.replace(".", "").replace(",", "")
    try:
        return int(raw)
    except Exception:
        return None

# ----------------------------
# SERP -> People (per /v1/serp)
# ----------------------------
def items_to_people(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Converte l'array 'items' del dataset Apify google-search-scraper
    in una lista di righe People compatibili con PeopleRow:
      { Nome, Cognome, Title, Snippet, Location, Followers, LinkedIn }
    """
    out: List[Dict[str, Any]] = []
    for page in items or []:
        organic = page.get("organicResults") or []
        for r in organic:
            url = r.get("url") or r.get("link") or r.get("sourceUrl")
            if not url or "linkedin.com" not in (url or ""):
                continue
            title = r.get("title") or ""
            snippet = r.get("description") or r.get("snippet") or ""
            location = (r.get("personalInfo") or {}).get("location")
            followers_raw = r.get("followersAmount")
            followers = _safe_int_from_text(followers_raw)
            nome, cognome = split_name_from_title(title)
            url_norm = normalize_linkedin_url(url)

            out.append(
                {
                    "Nome": nome,
                    "Cognome": cognome,
                    "Title": title,
                    "Snippet": snippet,
                    "Location": location,
                    "Followers": followers,
                    "LinkedIn": url_norm,
                }
            )

    # dedup per LinkedIn
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for row in out:
        li = row.get("LinkedIn")
        if not li or li in seen:
            continue
        seen.add(li)
        deduped.append(row)
    return deduped

# ----------------------------
# Enrichment -> People Master (per /v1/enrich)
# ----------------------------
def _fmt_ymd(ymd: Optional[str]) -> Optional[str]:
    if not ymd:
        return None
    m = re.match(r"(\d{4})-(\d{2})-\d{2}", ymd)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ymd

def _summarize_skills(item: Dict[str, Any], top_n: int = 8) -> Optional[str]:
    titles = [s.get("title") for s in (item.get("skills") or []) if s.get("title")]
    if titles:
        return ", ".join(titles[:top_n])
    return item.get("topSkillsByEndorsements")

def _experiences_full_string(item: Dict[str, Any]) -> Optional[str]:
    exps = item.get("experiences") or []
    if not exps:
        return None

    def _desc_from(sc_list):
        texts = []
        for sc in sc_list or []:
            for d in sc.get("description") or []:
                t = d.get("text")
                if t:
                    texts.append(t.strip())
        if not texts:
            return None
        first = texts[0].split("\n")[0]
        return first[:300]

    lines = []
    for exp in exps:
        breakdown = exp.get("breakdown")
        if breakdown:
            company = exp.get("title")
            exp_caption = exp.get("caption") or ""
            for sc in exp.get("subComponents") or []:
                role = sc.get("title")
                caption = sc.get("caption") or exp_caption
                loc = sc.get("metadata") or exp.get("metadata") or ""
                # qui non ricalcolo date in dettaglio: stampo caption grezza
                part = f"{caption}: {role or ''} @ {company or ''}".strip()
                if loc:
                    part += f" — {loc}"
                desc = _desc_from([sc]) or ""
                if desc:
                    part += f" | {desc}"
                lines.append(part)
        else:
            role = exp.get("title")
            company = exp.get("subtitle") or exp.get("title")
            caption = exp.get("caption") or ""
            loc = exp.get("metadata") or ""
            desc = _desc_from(exp.get("subComponents")) or ""
            part = f"{caption}: {role or ''} @ {company or ''}".strip()
            if loc:
                part += f" — {loc}"
            if desc:
                part += f" | {desc}"
            lines.append(part)

    return " • ".join([l for l in lines if l])

def _extract_experience_blocks(item: Dict[str, Any]):
    exps = item.get("experiences") or []
    if not exps:
        return None, None, None, None, None, None

    current_role = current_company = current_start = current_loc = current_dur = None
    lines = []
    for idx, exp in enumerate(exps):
        breakdown = exp.get("breakdown")
        if breakdown:
            company = exp.get("title")
            sub = (exp.get("subComponents") or [])
            if sub:
                sc0 = sub[0]
                role = sc0.get("title")
                caption = sc0.get("caption") or (exp.get("caption") or "")
                loc = sc0.get("metadata") or exp.get("metadata") or ""
                line = f"{caption}: {role} @ {company}" + (f" — {loc}" if loc else "")
                lines.append(line)
                if idx == 0:
                    current_role = role
                    current_company = company
                    current_start = None  # opzionale
                    current_loc = loc or None
                    current_dur = caption or exp.get("caption") or None
        else:
            role = exp.get("title")
            company = exp.get("subtitle") or exp.get("title")
            caption = exp.get("caption") or ""
            loc = exp.get("metadata") or ""
            line = f"{caption}: {role} @ {company}" + (f" — {loc}" if loc else "")
            lines.append(line)
            if idx == 0:
                current_role = role
                current_company = company
                current_start = None
                current_loc = loc or None
                current_dur = caption or None

    timeline_text = " • ".join([l for l in lines if l])
    return current_role, current_company, current_start, current_loc, current_dur, timeline_text

def _extract_education_blocks(item: Dict[str, Any]):
    edus = item.get("educations") or []
    if not edus:
        return None, None
    lines = []
    top = None
    for i, ed in enumerate(edus):
        school = ed.get("title")
        subtitle = ed.get("subtitle") or ""
        degree = subtitle.split(",")[0].strip() if subtitle else None
        field = subtitle.split(",")[1].strip() if subtitle and "," in subtitle else None
        caption = ed.get("caption") or ""
        line = f"{caption}: {degree or ''}{(' in ' + field) if field else ''} @ {school}".strip()
        lines.append(line)
        if i == 0:
            short = f"{degree or ''}{(' in ' + field) if field else ''}".strip()
            top = f"{school} — {short}" if short else school
    return top, " • ".join([l for l in lines if l])

def _person_master_row(item: Dict[str, Any]) -> Dict[str, Any]:
    current_role, current_company, current_start, current_loc, current_dur, _ = _extract_experience_blocks(item)
    edu_top, edu_text = _extract_education_blocks(item)
    return {
        "fullName": item.get("fullName"),
        "headline": item.get("headline"),
        "location": item.get("addressWithCountry")
            or item.get("addressWithoutCountry")
            or item.get("addressCountryOnly"),
        "current_role": current_role,
        "current_company": current_company,
        "current_start": current_start,
        "current_location": current_loc,
        "current_duration": current_dur,
        "experiences_full": _experiences_full_string(item),
        "education_top": edu_top,
        "education_text": edu_text,
        "skills": _summarize_skills(item, top_n=8),
        "connections": item.get("connections"),
        "followers": item.get("followers"),
        "email": item.get("email"),
        "mobileNumber": item.get("mobileNumber"),
        "linkedinUrl": item.get("linkedinUrl"),
        "companyLinkedin": item.get("companyLinkedin"),
        "profilePicHighQuality": item.get("profilePicHighQuality"),
    }

def items_to_master(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Converte gli items dell’attore di enrichment (Apify) in righe "People Master".
    Ritorna una lista di dict: una riga per persona.
    """
    rows = []
    for it in items or []:
        rows.append(_person_master_row(it))
    return rows
