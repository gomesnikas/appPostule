# collector_ft.py
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List, Optional

import requests


# --------- Config loading (Streamlit Cloud secrets OR local config.toml) ---------

def load_ft_credentials() -> tuple[str, str]:
    """
    Returns (client_id, client_secret).
    Priority:
      1) Streamlit Cloud: st.secrets["france_travail"]
      2) Local config.toml via tomllib
      3) Env vars: FT_CLIENT_ID / FT_CLIENT_SECRET
    """
    # 3) env vars
    env_id = os.getenv("FT_CLIENT_ID")
    env_secret = os.getenv("FT_CLIENT_SECRET")

    # 1) streamlit secrets if available
    try:
        import streamlit as st  # type: ignore
        if hasattr(st, "secrets") and "france_travail" in st.secrets:
            cid = st.secrets["france_travail"].get("client_id", "")  # type: ignore
            csec = st.secrets["france_travail"].get("client_secret", "")  # type: ignore
            if cid and csec:
                return cid, csec
    except Exception:
        pass

    # 2) local config.toml
    try:
        import tomllib  # py3.11+
        with open("config.toml", "rb") as f:
            cfg = tomllib.load(f)
        cid = (cfg.get("france_travail") or {}).get("client_id", "")
        csec = (cfg.get("france_travail") or {}).get("client_secret", "")
        if cid and csec:
            return cid, csec
    except Exception:
        pass

    # fallback env
    if env_id and env_secret:
        return env_id, env_secret

    raise RuntimeError(
        "Identifiants France Travail manquants. "
        "Ajoute [france_travail].client_id / client_secret dans Streamlit Secrets "
        "ou dans config.toml (local)."
    )


# --------- HTTP helpers ---------

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"

# Fallback bases: Streamlit Cloud semble parfois refuser/filtrer api.pole-emploi.io
BASE_URLS = [
    "https://api.francetravail.io",
    "https://api.emploi-store.fr",
    "https://api.pole-emploi.io",
]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def ft_get(path: str, headers: dict, params: dict, timeout: int = 30) -> requests.Response:
    last_exc: Optional[Exception] = None
    for base in BASE_URLS:
        try:
            r = requests.get(base + path, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            continue
    raise RuntimeError(f"Impossible de joindre l’API France Travail via {BASE_URLS}. Dernière erreur: {last_exc}")


# --------- OAuth token ---------

def get_token(client_id: str, client_secret: str) -> str:
    """
    Fetch OAuth2 token using client_credentials.
    """
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        # scopes courants pour l'API offres (peuvent varier selon ton compte)
        "scope": "api_offresdemploiv2 o2dsoffre",
    }
    r = requests.post(TOKEN_URL, data=data, timeout=30)
    # Si erreur, on remonte le contenu pour debug
    if r.status_code >= 400:
        raise RuntimeError(f"Erreur token FT ({r.status_code}): {r.text[:500]}")
    js = r.json()
    if "access_token" not in js:
        raise RuntimeError(f"Réponse token inattendue: {js}")
    return js["access_token"]


# --------- Search & normalize ---------

def fetch_jobs_data_only(token: str, page: int = 0, size: int = 50) -> Dict[str, Any]:
    path = "/partenaire/offresdemploi/v2/offres/search"

    mots_cles = "data engineer OR dataops OR data analyst OR data scientist OR big data OR databricks OR spark"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        # ✅ Range en header (format souvent attendu)
        "Range": f"items={page*size}-{page*size + size - 1}",
    }

    params = {
        "motsCles": mots_cles,
    }

    r = ft_get(path, headers=headers, params=params, timeout=30)

    if r.status_code == 204:
        # Aucun contenu retourné
        return {"resultats": []}

    ctype = (r.headers.get("Content-Type") or "").lower()
    body_preview = (r.text or "")[:800]

    if "application/json" not in ctype:
        raise RuntimeError(
            f"Réponse non-JSON de l'API FT.\n"
            f"URL: {r.url}\n"
            f"Status: {r.status_code}\n"
            f"Content-Type: {ctype}\n"
            f"Body (début):\n{body_preview}"
        )

    return r.json()


# def fetch_jobs_data_only(token: str, page: int = 0, size: int = 50) -> Dict[str, Any]:
#     """
#     Calls FT job offers search endpoint.
#     """
#     path = "/partenaire/offresdemploi/v2/offres/search"
#     headers = {"Authorization": f"Bearer {token}"}

#     # Mots-clés Data (tu peux affiner)
#     mots_cles = "data engineer OR dataops OR data analyst OR data scientist OR big data OR databricks OR spark"

#     params = {
#         "motsCles": mots_cles,
#         "range": f"{page*size}-{page*size + size - 1}",
#     }

#     # IMPORTANT : ne pas mettre departement vide
#     # Si tu veux filtrer, ajoute: params["departement"] = "69" etc.

#     r = ft_get(path, headers=headers, params=params, timeout=30)
#     return r.json()


def normalize_ft_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert FT API offer payload into our DB job schema.
    """
    title = offer.get("intitule", "") or ""
    entreprise = offer.get("entreprise") or {}
    company = entreprise.get("nom", "") or ""

    lieu = offer.get("lieuTravail") or {}
    location = lieu.get("libelle", "") or ""

    desc = offer.get("description", "") or ""

    # URL d’origine si dispo
    url = ""
    origine = offer.get("origineOffre") or {}
    url = origine.get("urlOrigine") or offer.get("url") or ""

    contract = offer.get("typeContrat", "") or ""
    published = offer.get("dateCreation") or offer.get("dateActualisation") or ""

    source_job_id = offer.get("id")

    # Hash stable
    key = f"{(source_job_id or '')}|{title.lower()}|{company.lower()}|{location.lower()}"
    h = sha(key)

    # FT ne fournit pas toujours un email direct
    apply_email = None

    return {
        "source": "france_travail",
        "source_job_id": source_job_id,
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "contract": contract,
        "seniority": "",
        "remote": "Tous",
        "published_at": published,
        "description": desc,
        "apply_email": apply_email,
        "hash": h,
    }


def run_collect(db_upsert_fn, max_pages: int = 3, page_size: int = 50) -> int:
    """
    Collects offers from FT and upserts them into DB via db_upsert_fn(job_dict).
    Returns number of jobs processed.
    """
    client_id, client_secret = load_ft_credentials()
    token = get_token(client_id, client_secret)

    total = 0
    for p in range(max_pages):
        data = fetch_jobs_data_only(token, page=p, size=page_size)

        # selon versions: "resultats" ou "offres"
        results: List[Dict[str, Any]] = data.get("resultats") or data.get("offres") or []
        if not results:
            # stop si plus rien
            break

        for off in results:
            job = normalize_ft_offer(off)
            db_upsert_fn(job)
            total += 1

    return total







# # collector_ft.py
# import hashlib
# import requests
# import tomllib
# from datetime import datetime

# def load_config():
#     with open("config.toml", "rb") as f:
#         return tomllib.load(f)

# def sha(s: str) -> str:
#     return hashlib.sha256(s.encode("utf-8")).hexdigest()

# def get_token(client_id: str, client_secret: str) -> str:
#     # Endpoint à adapter selon doc France Travail (OAuth2)
#     url = "https://entreprise.pole-emploi.fr/connexion/oauth2/access_token?realm=/partenaire"
#     data = {
#         "grant_type": "client_credentials",
#         "client_id": client_id,
#         "client_secret": client_secret,
#         "scope": "api_offresdemploiv2 o2dsoffre"
#     }
#     r = requests.post(url, data=data, timeout=30)
#     r.raise_for_status()
#     return r.json()["access_token"]

# def fetch_jobs_data_only(token: str, page: int = 0, size: int = 50):
#     # Endpoint à adapter selon doc v2
#     url = "https://api.pole-emploi.io/partenaire/offresdemploi/v2/offres/search"
#     headers = {"Authorization": f"Bearer {token}"}

#     # Requête Data-only: mots-clés
#     params = {
#         "motsCles": "data engineer OR dataops OR data analyst OR data scientist OR big data OR databricks OR spark",
#         "range": f"{page*size}-{page*size+size-1}",
#         "departement": "",  # optionnel
#     }

#     r = requests.get(url, headers=headers, params=params, timeout=30)
#     r.raise_for_status()
#     return r.json()

# def normalize_ft_offer(offer: dict) -> dict:
#     title = offer.get("intitule", "") or ""
#     company = (offer.get("entreprise") or {}).get("nom", "") or ""
#     location = (offer.get("lieuTravail") or {}).get("libelle", "") or ""
#     desc = offer.get("description", "") or ""
#     url = offer.get("origineOffre", {}).get("urlOrigine", "") or offer.get("url", "")
#     apply_email = None  # souvent pas fourni directement → parfois dans contact / origine

#     key = f"{title.lower()}|{company.lower()}|{location.lower()}|{(offer.get('id') or '')}"
#     h = sha(key)

#     published = offer.get("dateCreation") or offer.get("dateActualisation") or ""
#     remote = "Tous"
#     # Si l’API expose télétravail, map ici.

#     return {
#         "source": "france_travail",
#         "source_job_id": offer.get("id"),
#         "url": url,
#         "title": title,
#         "company": company,
#         "location": location,
#         "contract": offer.get("typeContrat", ""),
#         "seniority": "",
#         "remote": remote,
#         "published_at": published,
#         "description": desc,
#         "apply_email": apply_email,
#         "hash": h
#     }

# def run_collect(db_upsert_fn, max_pages: int = 3):
#     cfg = load_config()
#     token = get_token(cfg["france_travail"]["client_id"], cfg["france_travail"]["client_secret"])

#     for p in range(max_pages):
#         data = fetch_jobs_data_only(token, page=p, size=50)
#         results = data.get("resultats", []) or data.get("offres", []) or []
#         for off in results:
#             job = normalize_ft_offer(off)
#             db_upsert_fn(job)
