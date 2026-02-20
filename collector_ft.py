# collector_ft.py
import hashlib
import requests
import tomllib
from datetime import datetime

def load_config():
    with open("config.toml", "rb") as f:
        return tomllib.load(f)

def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def get_token(client_id: str, client_secret: str) -> str:
    # Endpoint à adapter selon doc France Travail (OAuth2)
    url = "https://entreprise.pole-emploi.fr/connexion/oauth2/access_token?realm=/partenaire"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "api_offresdemploiv2 o2dsoffre"
    }
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

def fetch_jobs_data_only(token: str, page: int = 0, size: int = 50):
    # Endpoint à adapter selon doc v2
    url = "https://api.pole-emploi.io/partenaire/offresdemploi/v2/offres/search"
    headers = {"Authorization": f"Bearer {token}"}

    # Requête Data-only: mots-clés
    params = {
        "motsCles": "data engineer OR dataops OR data analyst OR data scientist OR big data OR databricks OR spark",
        "range": f"{page*size}-{page*size+size-1}",
        "departement": "",  # optionnel
    }

    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def normalize_ft_offer(offer: dict) -> dict:
    title = offer.get("intitule", "") or ""
    company = (offer.get("entreprise") or {}).get("nom", "") or ""
    location = (offer.get("lieuTravail") or {}).get("libelle", "") or ""
    desc = offer.get("description", "") or ""
    url = offer.get("origineOffre", {}).get("urlOrigine", "") or offer.get("url", "")
    apply_email = None  # souvent pas fourni directement → parfois dans contact / origine

    key = f"{title.lower()}|{company.lower()}|{location.lower()}|{(offer.get('id') or '')}"
    h = sha(key)

    published = offer.get("dateCreation") or offer.get("dateActualisation") or ""
    remote = "Tous"
    # Si l’API expose télétravail, map ici.

    return {
        "source": "france_travail",
        "source_job_id": offer.get("id"),
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "contract": offer.get("typeContrat", ""),
        "seniority": "",
        "remote": remote,
        "published_at": published,
        "description": desc,
        "apply_email": apply_email,
        "hash": h
    }

def run_collect(db_upsert_fn, max_pages: int = 3):
    cfg = load_config()
    token = get_token(cfg["france_travail"]["client_id"], cfg["france_travail"]["client_secret"])

    for p in range(max_pages):
        data = fetch_jobs_data_only(token, page=p, size=50)
        results = data.get("resultats", []) or data.get("offres", []) or []
        for off in results:
            job = normalize_ft_offer(off)
            db_upsert_fn(job)
