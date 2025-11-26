import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .schemas import SerpRequest, SerpResponse, PeopleRow, EnrichRequest, EnrichResponse, MasterRow
from .apify_client import get_apify
from .transform import items_to_people, items_to_master
import os, json, re
from openai import OpenAI
from .schemas import ScoreRequest, ScoreResponse, ScoredRow, MasterRow

app = FastAPI(title="UV Deal Sourcing API", version="0.2.0")

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS","").split(",") if o.strip()]
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

@app.post("/v1/serp", response_model=SerpResponse)
@app.post("/v1/serp", response_model=SerpResponse)
def serp(req: SerpRequest):
    if not req.keywords:
        raise HTTPException(400, "keywords vuoto")

    keywords_query = " OR ".join([f'"{kw}"' for kw in req.keywords])
    query_text = f'site:{req.site_filter}/in ({keywords_query}) {req.country_code.upper()}'

    # --- FIX countryCode: lowercase + mappa UK ---
    cc = (req.country_code or "").strip().lower()
    if cc == "uk":
        cc = "gb"
    # opzionale: se non è nella whitelist, fallback vuoto per lasciare default globale
    allowed = {
        "", "af","al","dz","as","ad","ao","ai","aq","ag","ar","am","aw","au","at","az","bs","bh","bd","bb","by","be","bz","bj",
        "bm","bt","bo","ba","bw","bv","br","io","bn","bg","bf","bi","kh","cm","ca","cv","ky","cf","td","cl","cn","cx","cc","co",
        "km","cg","cd","ck","cr","ci","hr","cu","cy","cz","dk","dj","dm","do","ec","eg","sv","gq","er","ee","et","fk","fo","fj",
        "fi","fr","gf","pf","tf","ga","gm","ge","de","gh","gi","gr","gl","gd","gp","gu","gt","gn","gw","gy","ht","hm","va","hn",
        "hk","hu","is","in","id","ir","iq","ie","il","it","jm","jp","jo","kz","ke","ki","kp","kr","kw","kg","la","lv","lb","ls",
        "lr","ly","li","lt","lu","mo","mk","mg","mw","my","mv","ml","mt","mh","mq","mr","mu","yt","mx","fm","md","mc","mn","ms",
        "ma","mz","mm","na","nr","np","nl","an","nc","nz","ni","ne","ng","nu","nf","mp","no","om","pk","pw","ps","pa","pg","py",
        "pe","ph","pn","pl","pt","pr","qa","re","ro","ru","rw","sh","kn","lc","pm","vc","ws","sm","st","sa","sn","cs","sc","sl",
        "sg","sk","si","sb","so","za","gs","es","lk","sd","sr","sj","sz","se","ch","sy","tw","tj","tz","th","tl","tg","tk","to",
        "tt","tn","tr","tm","tc","tv","ug","ua","ae","gb","us","um","uy","uz","vu","ve","vn","vg","vi","wf","eh","ye","zm","zw"
    }
    if cc not in allowed:
        cc = ""

    try:
        apify = get_apify()
        run = apify.actor("apify/google-search-scraper").call(run_input={
            "queries": query_text,
            "countryCode": cc,                    # <— USARE cc fissato
            "maxPagesPerQuery": int(req.max_pages),
            "site": req.site_filter,
        })
        dataset_id = run["defaultDatasetId"]
        items = list(apify.dataset(dataset_id).iterate_items())
    except Exception as e:
        import traceback, sys
        print("APIFY ERROR:", e, file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(502, detail=f"Errore Apify: {str(e)}")

    people_rows = items_to_people(items)
    return SerpResponse(
        query=query_text,
        count_pages=req.max_pages,
        people=[PeopleRow(**r) for r in people_rows],
        raw_items_count=len(items),
    )



@app.post("/v1/enrich", response_model=EnrichResponse)
def enrich(req: EnrichRequest):
    if not req.linkedin_urls:
        raise HTTPException(400, "linkedin_urls vuoto")

    try:
        apify = get_apify()
        # batch unico — se vuoi chunking, lo aggiungiamo
        run = apify.actor("2SyF0bVxmgGr8IVCZ").call(run_input={
            "profileUrls": list(dict.fromkeys([str(u) for u in req.linkedin_urls]))  # dedup
        })
        dataset_id = run["defaultDatasetId"]
        items = list(apify.dataset(dataset_id).iterate_items())
    except Exception as e:
        import traceback, sys
        print("APIFY ENRICH ERROR:", e, file=sys.stderr)
        traceback.print_exc()
        raise HTTPException(502, detail=f"Errore Apify: {str(e)}")

    master_rows = items_to_master(items)
    return EnrichResponse(
        count=len(master_rows),
        people_master=[MasterRow(**r) for r in master_rows],
    )

SCORING_RULES_TEXT = (
    "Valuta se contattare la persona per possibile investimento (0-10). Assegna 1 punto per ciascun criterio soddisfatto: "
    "1) Esperienza da founder o co-founder; 2) Ruoli C-level (CEO/CTO/...) passati o attuali; "
    "3) Tempo in stealth < 18 mesi; 4) Università top-tier (Ivy, Oxbridge, Stanford, MIT, ETH, EPFL, Bocconi, PoliMi, PoliTo, Sapienza, Sant'Anna, Normale, etc.); "
    "5) Background tecnico (CS/AI/ingegneria/deep tech); 6) Serial entrepreneur (>=2 esperienze da founder di startup) o second time Founder di una startup; "
    "7) Esperienza in big tech/scaleup/StartUp che ha avuto una crescità di employees nell'ultimo periodo (FAANG/unicorn/ScaleUp); 8) Network forte (followers o connections elevati > 5000); "
    "9) Ruolo attuale con alta responsabilità (team >10, guida divisione); 10) Momentum: ruolo attuale iniziato < 24 mesi. "
    'Restituisci SOLO JSON valido con: {"score": int 0-10, "reasons": string breve in italiano, "contact": boolean (true se score>=7)}.'
)
TOP_UNI_CANONICAL = [
    "Harvard University","Stanford University","Massachusetts Institute of Technology","University of Oxford","University of Cambridge",
    "ETH Zurich","EPFL","University of Bologna","Università Bocconi","Politecnico di Milano","Politecnico di Torino","Sapienza University of Rome",
    "Scuola Superiore Sant'Anna","Scuola Normale Superiore"  # Università Ca' Foscari venezia -Unitversità degli studi triste - Universtià di trento - Universtià degli studi di Bari - politecnico di bari - Alma mater studiuorum bologna - Uni Pisa 
]

def _safe_json_extract(s):
    try:
        if isinstance(s, (dict, list)): return s
        if not isinstance(s, str): s = "" if s is None else str(s)
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s or "", re.DOTALL)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return {"error":"bad_json","raw":(s or "")[:1000]}
        return {"error":"no_json","raw":(s or "")[:500]}

def _months_from_duration(text: str | None) -> int | None:
    if not text: return None
    m = re.search(r"(\d+)\s*(?:mos?|mesi|month|months)", text, flags=re.I)
    if m: 
        try: return int(m.group(1))
        except: return None
    y = re.search(r"(\d+)\s*(?:yrs?|anni|year|years)", text, flags=re.I)
    if y:
        try: return int(y.group(1)) * 12
        except: return None
    return None

@app.post("/v1/score", response_model=ScoreResponse)
def score(req: ScoreRequest):
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise HTTPException(500, detail="OPENAI_API_KEY non configurata")
    model = req.model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(api_key=api_key)

    rows_in = req.people_master or []
    wanted = int(req.max_rows) if req.max_rows is not None else len(rows_in)
    n = max(1, min(len(rows_in), wanted))

    people_scored: list[ScoredRow] = []

    # scoriamo batch piccolo alla volta (iterativo semplice)
    for i, r in enumerate(rows_in[:n]):
        payload = {
            "fullName": r.fullName,
            "headline": r.headline,
            "current_role": r.current_role,
            "current_company": r.current_company,
            "current_duration": r.current_duration,
            "months_in_current": _months_from_duration(r.current_duration),
            "education_top": r.education_top,
            "education_text": r.education_text,
            "experiences_full": r.experiences_full,
            "skills": r.skills,
            "followers": r.followers,
            "connections": r.connections,
            "linkedinUrl": str(r.linkedinUrl) if r.linkedinUrl else None,
        }
        
        system_msg = SCORING_RULES_TEXT + " Considera anche questa lista di università top-tier come riferimento: " + ", ".join(TOP_UNI_CANONICAL) + "."
        user_msg = json.dumps(payload, ensure_ascii=False)

        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
            content = resp.choices[0].message.content if resp and resp.choices else ""
            data = _safe_json_extract(content)
            score_val = None
            if isinstance(data, dict) and str(data.get("score")).isdigit():
                score_val = int(data["score"])
            reasons = data.get("reasons") if isinstance(data, dict) else None
            contact = bool(data.get("contact")) if isinstance(data, dict) and "contact" in data else (score_val is not None and score_val >= 7)
        except Exception as e:
            score_val, reasons, contact = None, f"Errore modello: {e}", None

        # merge nei campi dell’input
        base = r.model_dump()
        base.update({"score": score_val, "reasons": reasons, "contact": contact})
        people_scored.append(ScoredRow(**base))

    return ScoreResponse(
        count_in=len(rows_in),
        count_scored=len(people_scored),
        model_used=model,
        people_scored=people_scored,
    )
