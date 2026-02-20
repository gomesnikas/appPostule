# app.py
import streamlit as st
from pathlib import Path
import tomllib

from db import (
    init_db,
    list_jobs,
    get_job_by_hash,
    log_application,
    count_sent_today,
    upsert_job,
)
from mailer import send_email

# Collecteurs (optionnels)
try:
    from collector_ft import run_collect
    FT_AVAILABLE = True
except Exception:
    FT_AVAILABLE = False

try:
    from collector_careers import scrape_simple_career_page
    CAREERS_AVAILABLE = True
except Exception:
    CAREERS_AVAILABLE = False


def safe_read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def load_config():
    cfg_path = Path("config.toml")
    if not cfg_path.exists():
        st.error("Fichier `config.toml` introuvable à la racine du projet.")
        st.info("Crée un fichier `config.toml` dans le même dossier que `app.py`.")
        st.stop()

    with cfg_path.open("rb") as f:
        cfg = tomllib.load(f)

    # Vérifs minimales
    if "profile" not in cfg:
        st.error("Section [profile] manquante dans config.toml")
        st.stop()
    if "mail" not in cfg:
        st.error("Section [mail] manquante dans config.toml")
        st.stop()

    return cfg


def render_template(tpl: str, ctx: dict) -> str:
    for k, v in ctx.items():
        tpl = tpl.replace("{{" + k + "}}", v or "")
    return tpl


def main():
    st.set_page_config(page_title="Data Jobs MVP", layout="wide")
    cfg = load_config()
    init_db()

    st.title("📌 Data Jobs – MVP (France Travail + Careers whitelist)")
    st.caption("Collecte d'offres Data + préparation et envoi de candidatures par email (avec validation).")

    # ---------------- Sidebar ----------------
    with st.sidebar:
        st.header("⚙️ Collecte")

        # France Travail import
        st.subheader("France Travail (API)")
        ft_ready = (
            "france_travail" in cfg
            and cfg["france_travail"].get("client_id")
            and cfg["france_travail"].get("client_secret")
        )

        if not FT_AVAILABLE:
            st.warning("Module France Travail non disponible (collector_ft.py manquant ou erreur d'import).")
        elif not ft_ready:
            st.info("France Travail non configuré (client_id / client_secret manquants dans config.toml).")
        else:
            if st.button("🔄 Import France Travail (Data)"):
                try:
                    run_collect(upsert_job, max_pages=3)
                    st.success("Import France Travail terminé ✅")
                except Exception as e:
                    st.error("Échec import France Travail.")
                    st.write("Détail erreur :")
                    st.code(str(e))

        st.markdown("---")

        # Whitelist pages carrières
        st.subheader("Pages carrières autorisées")
        if not CAREERS_AVAILABLE:
            st.warning("Module pages carrières non disponible (collector_careers.py manquant ou erreur d'import).")
        else:
            url = st.text_input("URL page carrière autorisée", placeholder="https://.../careers ou /jobs")
            name = st.text_input("Nom entreprise (source)", placeholder="Ex: CompanyName")
            if st.button("➕ Scraper cette page"):
                if not url or not name:
                    st.warning("Remplis l’URL + le nom entreprise.")
                else:
                    try:
                        jobs = scrape_simple_career_page(url, name)
                        for j in jobs:
                            upsert_job(j)
                        st.success(f"{len(jobs)} offre(s) ajoutée(s) ✅")
                    except Exception as e:
                        st.error("Échec scraping page carrière.")
                        st.code(str(e))

        st.markdown("---")
        st.header("🔎 Filtres")

        q = st.text_input("Recherche (mots-clés)", value="data engineer")
        loc = st.text_input("Localisation", value="")
        remote = st.selectbox("Télétravail", ["Tous", "Oui", "Non"])
        not_applied = st.checkbox("Masquer déjà postulé", value=True)

    # ---------------- Main data ----------------
    filters = {"query": q, "location": loc, "remote": remote, "not_applied": not_applied}
    jobs = list_jobs(filters)

    col1, col2 = st.columns([1, 2], gap="large")

    # ---------------- Left: list ----------------
    with col1:
        st.subheader(f"Offres ({len(jobs)})")

        if not jobs:
            st.info("Aucune offre trouvée. Lance un import ou change les filtres.")
        else:
            # selection
            if "selected_hash" not in st.session_state:
                st.session_state.selected_hash = jobs[0]["hash"]

            for j in jobs[:250]:
                label = f"{j['title']} — {j['company']} ({j['location']})"
                if st.button(label, key=j["hash"]):
                    st.session_state.selected_hash = j["hash"]

    # ---------------- Right: details + apply ----------------
    with col2:
        st.subheader("Détail & Candidature")

        if not jobs:
            st.stop()

        job = get_job_by_hash(st.session_state.selected_hash)
        if not job:
            st.info("Sélectionne une offre.")
            st.stop()

        st.markdown(f"### {job['title']}")
        st.write(f"**Entreprise :** {job.get('company','')}")
        st.write(f"**Lieu :** {job.get('location','')}")
        if job.get("url"):
            st.write(f"**URL :** {job['url']}")

        st.write("---")
        desc = job.get("description") or ""
        st.write(desc[:3500] if desc else "_Description non disponible._")

        st.markdown("## ✉️ Candidature par email")

        # Templates
        email_tpl_path = Path("templates/email.txt")
        email_tpl = safe_read_text(email_tpl_path)
        if not email_tpl:
            st.warning("Template email introuvable. Crée `templates/email.txt`.")
            email_tpl = (
                "Bonjour,\n\n"
                "Je vous contacte au sujet du poste « {{TITLE}} » chez {{COMPANY}}.\n\n"
                "Cordialement,\n{{NAME}}\n{{PHONE}} | {{EMAIL}}\n"
            )

        # Context
        ctx = {
            "TITLE": job.get("title", ""),
            "COMPANY": job.get("company", ""),
            "NAME": cfg["profile"].get("full_name", ""),
            "PHONE": cfg["profile"].get("phone", ""),
            "EMAIL": cfg["profile"].get("email", ""),
        }

        # Recipient email (often missing => manual)
        to_email = st.text_input("Email destinataire", value=job.get("apply_email") or "", placeholder="recrutement@entreprise.com")

        subject_default = f"Candidature – {job['title']} – {cfg['profile'].get('full_name','')}"
        subject = st.text_input("Objet", value=subject_default)

        body_default = render_template(email_tpl, ctx)
        body = st.text_area("Corps du mail (modifiable)", value=body_default, height=240)

        st.write("---")

        # Attachments
        st.markdown("### 📎 Pièces jointes")
        cv_path = st.text_input("Chemin CV (PDF)", value="assets/CV_Nicolas.pdf")
        cv_file = Path(cv_path)
        attachments = []
        if cv_file.exists() and cv_file.is_file():
            attachments.append(cv_file)
            st.success(f"CV trouvé ✅ ({cv_file.name})")
        else:
            st.warning("CV introuvable. Mets ton CV dans `assets/` ou corrige le chemin.")

        st.write("---")

        # Send limit
        sent_today = count_sent_today()
        daily_limit = int(cfg["mail"].get("daily_limit", 10))
        st.caption(f"Limite d’envoi/jour : {sent_today}/{daily_limit}")

        # Validation checkbox
        confirm = st.checkbox("✅ Je confirme que ce mail est pertinent et personnalisé (anti-spam).")

        send_disabled = (not confirm) or (sent_today >= daily_limit)

        if st.button("📨 Envoyer la candidature", disabled=send_disabled):
            # Basic checks
            if not to_email:
                st.error("Renseigne l’email destinataire.")
            elif not cfg["mail"].get("smtp_user") or not cfg["mail"].get("smtp_password"):
                st.error("SMTP non configuré dans config.toml (smtp_user / smtp_password).")
            elif not attachments:
                st.error("Aucun CV en pièce jointe (chemin invalide).")
            else:
                try:
                    send_email(to_email, subject, body, attachments)
                    log_application(job["hash"], "sent", to_email, subject, body)
                    st.success("Candidature envoyée et enregistrée ✅")
                except Exception as e:
                    st.error("Échec envoi email.")
                    st.write("Détail erreur :")
                    st.code(str(e))

        if sent_today >= daily_limit:
            st.warning("Limite quotidienne atteinte. Augmente `daily_limit` dans config.toml si besoin.")

        st.write("---")
        st.markdown("### 🧾 Conseils rapides")
        st.write("- Ajoute 1–2 lignes personnalisées (mission, techno, secteur) avant d’envoyer.")
        st.write("- Évite l’envoi en masse : qualité > quantité.")

if __name__ == "__main__":
    main()



# # app.py
# import streamlit as st
# from pathlib import Path
# import tomllib

# from db import init_db, list_jobs, get_job_by_hash, log_application, count_sent_today, upsert_job
# from mailer import send_email
# from collector_ft import run_collect
# from collector_careers import scrape_simple_career_page

# def load_config():
#     with open("config.toml", "rb") as f:
#         return tomllib.load(f)

# def render_template(tpl: str, ctx: dict) -> str:
#     for k, v in ctx.items():
#         tpl = tpl.replace("{{"+k+"}}", v or "")
#     return tpl

# st.set_page_config(page_title="Data Jobs MVP", layout="wide")
# cfg = load_config()
# init_db()

# st.title("📌 Data Jobs – MVP (France Travail + Careers whitelist)")

# with st.sidebar:
#     st.header("Collecte")
#     if st.button("🔄 Import France Travail (Data)"):
#         run_collect(upsert_job, max_pages=3)
#         st.success("Import FT terminé.")

#     st.markdown("---")
#     st.subheader("Whitelist pages carrières")
#     url = st.text_input("URL page carrière autorisée")
#     name = st.text_input("Nom entreprise (source)")
#     if st.button("➕ Scraper cette page"):
#         if url and name:
#             jobs = scrape_simple_career_page(url, name)
#             for j in jobs:
#                 upsert_job(j)
#             st.success(f"{len(jobs)} offre(s) ajoutée(s).")
#         else:
#             st.warning("Remplis URL + nom entreprise.")

#     st.markdown("---")
#     st.header("Filtres")
#     q = st.text_input("Recherche (mots-clés)", value="data engineer")
#     loc = st.text_input("Localisation", value="")
#     remote = st.selectbox("Télétravail", ["Tous", "Oui", "Non"])
#     not_applied = st.checkbox("Masquer déjà postulé", value=True)

# filters = {"query": q, "location": loc, "remote": remote, "not_applied": not_applied}
# jobs = list_jobs(filters)

# col1, col2 = st.columns([1, 2], gap="large")

# with col1:
#     st.subheader(f"Offres ({len(jobs)})")
#     selected_hash = None
#     for j in jobs[:200]:
#         label = f"{j['title']} — {j['company']} ({j['location']})"
#         if st.button(label, key=j["hash"]):
#             selected_hash = j["hash"]

# if "selected_hash" not in st.session_state:
#     st.session_state.selected_hash = None
# if selected_hash:
#     st.session_state.selected_hash = selected_hash

# with col2:
#     st.subheader("Détail")
#     if not st.session_state.selected_hash and jobs:
#         st.session_state.selected_hash = jobs[0]["hash"]

#     job = get_job_by_hash(st.session_state.selected_hash) if st.session_state.selected_hash else None
#     if not job:
#         st.info("Sélectionne une offre.")
#         st.stop()

#     st.markdown(f"### {job['title']}")
#     st.write(f"**Entreprise :** {job['company']}")
#     st.write(f"**Lieu :** {job['location']}")
#     if job["url"]:
#         st.write(f"**URL :** {job['url']}")
#     st.write("---")
#     st.write(job["description"][:3000] if job["description"] else "_Description non disponible._")

#     st.markdown("## ✉️ Candidature par email")

#     # Email cible (si connu). Souvent absent -> tu le remplis.
#     to_email = st.text_input("Email destinataire", value=job.get("apply_email") or "")

#     # Templates
#     email_tpl = Path("templates/email.txt").read_text(encoding="utf-8")
#     ctx = {
#         "TITLE": job["title"],
#         "COMPANY": job["company"],
#         "NAME": cfg["profile"]["full_name"],
#         "PHONE": cfg["profile"]["phone"],
#         "EMAIL": cfg["profile"]["email"],
#     }
#     subject_default = f"Candidature – {job['title']} – {cfg['profile']['full_name']}"
#     subject = st.text_input("Objet", value=subject_default)
#     body = st.text_area("Corps du mail", value=render_template(email_tpl, ctx), height=220)

#     cv_path = st.text_input("Chemin CV (PDF)", value="assets/CV_Nicolas.pdf")
#     attachments = [Path(cv_path)] if Path(cv_path).exists() else []
#     if not attachments:
#         st.warning("CV introuvable : place ton PDF dans assets/ et mets le bon chemin.")

#     st.write("---")
#     sent_today = count_sent_today()
#     limit = int(cfg["mail"].get("daily_limit", 10))
#     st.caption(f"Limite envoi/jour : {sent_today}/{limit}")

#     confirm = st.checkbox("✅ Je confirme que ce mail est pertinent et personnalisé (anti-spam).")
#     send_btn = st.button("📨 Envoyer la candidature", disabled=not confirm)

#     if send_btn:
#         if sent_today >= limit:
#             st.error("Limite d’envoi quotidienne atteinte.")
#         elif not to_email:
#             st.error("Renseigne l’email destinataire.")
#         else:
#             send_email(to_email, subject, body, attachments)
#             log_application(job["hash"], "sent", to_email, subject, body)
#             st.success("Candidature envoyée et enregistrée ✅")
