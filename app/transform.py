import re
from urllib.parse import urlparse
from typing import List, Dict, Any, Tuple

def normalize_linkedin_url(u: str) -> str:
    try:
        p = urlparse(u)
        host = (p.hostname or "").lower()
        host = (host
            .replace("www.linkedin.", "linkedin.")
            .replace("it.linkedin.", "linkedin.")
            .replace("es.linkedin.", "linkedin.")
            .replace("uk.linkedin.", "linkedin."))
        path = (p.path or "").rstrip("/")
        return f"https://{host}{path}"
    except Exception:
        return (u or "").strip()

def split_name_from_title(title: str) -> Tuple[str, str]:
    if not title: return "", ""
    first = re.split(r"\s[-–—|·•]\s", title, maxsplit=1)[0]
    first = re.sub(r"\s+\([^)]*\)$", "", first).strip()
    first = re.sub(r"^\s*LinkedIn\s*›\s*", "", first, flags=re.I).strip()
    toks = [t for t in re.split(r"\s+", first) if t]
    if len(toks) >= 2: return toks[0], " ".join(toks[1:])
    return first, ""

def items_to_people(items: List[Dict[str, Any]]):
    rows = []
    seen = set()
    for it in items:
        for r in it.get("organicResults", []):
            url = r.get("url") or r.get("link") or r.get("sourceUrl")
            if not url or "linkedin.com" not in url:
                continue
            url_norm = normalize_linkedin_url(url)
            if url_norm in seen:
                continue
            seen.add(url_norm)
            title = r.get("title") or ""
            nome, cognome = split_name_from_title(title)
            rows.append({
                "Nome": nome,
                "Cognome": cognome,
                "Title": title,
                "Snippet": r.get("description") or r.get("snippet"),
                "Location": (r.get("personalInfo") or {}).get("location"),
                "Followers": r.get("followersAmount"),
                "LinkedIn": url_norm,
            })
    return rows
