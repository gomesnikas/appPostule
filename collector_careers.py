# collector_careers.py
import hashlib
import requests
from bs4 import BeautifulSoup

def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def scrape_simple_career_page(url: str, source_name: str):
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    jobs = []
    # ⚠️ À ADAPTER par site : sélecteurs
    for a in soup.select("a"):
        text = a.get_text(" ", strip=True).lower()
        if "data" in text and ("engineer" in text or "analyst" in text or "scientist" in text):
            link = a.get("href")
            if not link:
                continue
            if link.startswith("/"):
                # naïf : tu peux utiliser urllib.parse.urljoin
                link = url.rstrip("/") + link
            title = a.get_text(" ", strip=True)
            company = source_name
            location = ""
            desc = ""
            h = sha(f"{title.lower()}|{company.lower()}|{link}")
            jobs.append({
                "source": f"career:{source_name}",
                "source_job_id": None,
                "url": link,
                "title": title,
                "company": company,
                "location": location,
                "contract": "",
                "seniority": "",
                "remote": "Tous",
                "published_at": "",
                "description": desc,
                "apply_email": None,
                "hash": h
            })
    return jobs
