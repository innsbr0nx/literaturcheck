import streamlit as st
import re
import requests
from fuzzywuzzy import fuzz
from docx import Document
from lxml import etree
import pandas as pd

# ----------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------

def lade_datei(datei):
    """Lädt Text- oder Worddatei und gibt Zeilen zurück."""
    if datei.name.endswith(".txt"):
        zeilen = [l.strip() for l in datei.getvalue().decode("utf-8").splitlines() if l.strip()]
    elif datei.name.endswith(".docx"):
        doc = Document(datei)
        zeilen = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
    else:
        st.error("Nur .txt oder .docx werden unterstützt.")
        return []
    return zeilen


def parse_einträge(zeilen):
    """Extrahiert DOI oder ISBN, Autor und Titel aus den Zeilen."""
    einträge = []
    for zeile in zeilen:
        try:
            doi_match = re.search(r'\[DOI:\s*(10\.\S+?)\]', zeile)
            isbn_match = re.search(r'\[ISBN:\s*([\d\-]+)\]', zeile)

            if doi_match:
                identifier = doi_match.group(1).strip()
                id_typ = "doi"
            elif isbn_match:
                identifier = isbn_match.group(1).replace("-", "").strip()
                id_typ = "isbn"
            else:
                continue

            autor_teil = zeile.split(',')[0].strip()
            autor_teil = re.sub(r"\(Hrsg\.\)", "", autor_teil)
            autor_teil = re.sub(r"et al\.?", "", autor_teil, flags=re.IGNORECASE)
            autor = autor_teil.strip()

            teile = zeile.split(',')
            titel = teile[2].strip() if len(teile) >= 3 else "unbekannter Titel"

            einträge.append({
                'typ': id_typ,
                'id': identifier,
                'titel': titel,
                'autor': autor
            })
        except:
            continue
    return einträge


def normalize_isbn(isbn: str) -> str:
    """Entfernt Bindestriche/Leerzeichen aus ISBN."""
    return isbn.replace("-", "").replace(" ", "").strip()


# ----------------------------------------------------
# DOI-Quellen
# ----------------------------------------------------

def get_metadata_crossref(doi):
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}", timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()["message"]
        titel = data.get("title", [""])[0]
        autoren = [a["family"] for a in data.get("author", []) if "family" in a]
        return {"quelle": "CrossRef", "titel": titel, "autoren": autoren}
    except:
        return None


def get_metadata_opencitations(doi):
    try:
        r = requests.get(f"https://opencitations.net/index/api/v1/metadata/{doi}", timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or not isinstance(data, list):
            return None
        eintrag = data[0]
        titel = eintrag.get("title", "")
        autor_raw = eintrag.get("author", "")
        nachname = autor_raw.split(",")[0] if "," in autor_raw else autor_raw
        return {"quelle": "OpenCitations", "titel": titel, "autoren": [nachname]}
    except:
        return None


def get_metadata_doaj(doi):
    try:
        url = f"https://doaj.org/api/v2/search/articles/doi:{doi.replace('/', '%2F')}"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if "results" not in data or not data["results"]:
            return None
        artikel = data["results"][0]
        bib = artikel.get("bibjson", {})
        titel = bib.get("title", "")
        autoren_liste = [a.get("name", "") for a in bib.get("author", []) if a.get("name")]
        return {"quelle": "DOAJ", "titel": titel, "autoren": autoren_liste}
    except:
        return None


def get_metadata_doi_rest(doi):
    try:
        url = f"https://doi.org/{doi}"
        headers = {"Accept": "application/citeproc+json"}
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        titel = data.get("title", "")
        autoren = [a.get("family", "") for a in data.get("author", []) if "family" in a] if "author" in data else []
        return {"quelle": "DOI REST API", "titel": titel, "autoren": autoren}
    except:
        return None


# ----------------------------------------------------
# ISBN-Quellen
# ----------------------------------------------------

def get_metadata_openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&jscmd=data&format=json"
        r = requests.get(url, timeout=15)
        data = r.json().get(f"ISBN:{isbn}")
        if not data:
            return None
        titel = data.get("title", "")
        autoren = [a.get("name", "") for a in data.get("authors", [])]
        return {"quelle": "OpenLibrary", "titel": titel, "autoren": autoren}
    except:
        return None


def get_metadata_googlebooks(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        r = requests.get(url, timeout=15)
        data = r.json()
        if 'items' not in data:
            return None
        volume_info = data['items'][0].get('volumeInfo', {})
        titel = volume_info.get('title', '')
        autoren = volume_info.get('authors', [])
        return {"quelle": "Google Books", "titel": titel, "autoren": autoren}
    except:
        return None


def get_metadata_worldcat_sru(isbn):
    try:
        url = f"https://worldcat.org/webservices/catalog/search/sru?version=1.2&operation=searchRetrieve&query=isbn={isbn}&maximumRecords=1"
        headers = {'Accept': 'application/xml'}
        r = requests.get(url, headers=headers, timeout=15)
        tree = etree.fromstring(r.content)
        ns = {'srw': 'http://www.loc.gov/zing/srw/'}
        records = tree.findall('.//srw:record', ns)
        if not records:
            return None
        titel = None
        autoren = []
        for elem in records[0].iter():
            if elem.tag.endswith('title') and not titel:
                titel = elem.text
            if elem.tag.endswith('name'):
                autoren.append(elem.text)
        return {"quelle": "WorldCat", "titel": titel or "", "autoren": autoren}
    except:
        return None


def get_metadata_dnb(isbn):
    try:
        url = f"https://services.dnb.de/sru/dnb?version=1.1&operation=searchRetrieve&query=isbn={isbn}&maximumRecords=1"
        headers = {"Accept": "application/xml"}
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        tree = etree.fromstring(r.content)
        ns = {'srw': 'http://www.loc.gov/zing/srw/'}
        records = tree.findall('.//srw:record', ns)
        if not records:
            return None
        titel, autoren = None, []
        for elem in records[0].iter():
            if elem.tag.endswith('title') and not titel:
                titel = elem.text
            if elem.tag.endswith('name'):
                autoren.append(elem.text)
        return {"quelle": "DNB", "titel": titel or "", "autoren": autoren}
    except:
        return None


def get_metadata_zdb(isbn):
    try:
        url = f"https://services.dnb.de/sru/zdb?version=1.1&operation=searchRetrieve&query=isbn={isbn}&maximumRecords=1"
        headers = {"Accept": "application/xml"}
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        tree = etree.fromstring(r.content)
        ns = {'srw': 'http://www.loc.gov/zing/srw/'}
        records = tree.findall('.//srw:record', ns)
        if not records:
            return None
        titel, autoren = None, []
        for elem in records[0].iter():
            if elem.tag.endswith('title') and not titel:
                titel = elem.text
            if elem.tag.endswith('name'):
                autoren.append(elem.text)
        return {"quelle": "ZDB", "titel": titel or "", "autoren": autoren}
    except:
        return None


def get_metadata_from_all_sources(isbn):
    isbn = normalize_isbn(isbn)
    results = []
    for q in [get_metadata_googlebooks, get_metadata_worldcat_sru, get_metadata_openlibrary, get_metadata_dnb, get_metadata_zdb]:
        md = q(isbn)
        if md:
            results.append(md)
    return results


# ----------------------------------------------------
# Vergleich
# ----------------------------------------------------

def vergleiche(eintrag, metadata):
    if not metadata:
        return {"quelle": "unbekannt", "titel_score": 0, "autor_match": False}
    titel_score = fuzz.token_sort_ratio(eintrag["titel"].lower(), metadata["titel"].lower())
    autor_match = any(eintrag["autor"].lower() in a.lower() for a in metadata.get("autoren", []))
    return {"quelle": metadata["quelle"], "titel_score": titel_score, "autor_match": autor_match}


# ----------------------------------------------------
# Hauptlogik
# ----------------------------------------------------

def überprüfe(einträge):
    gesamt_ergebnisse = []

    for eintrag in einträge:
        res_liste = []
        if eintrag["typ"] == "doi":
            quellen = [get_metadata_crossref, get_metadata_opencitations, get_metadata_doaj, get_metadata_doi_rest]
            for q in quellen:
                md = q(eintrag["id"])
                if md:
                    vergleich = vergleiche(eintrag, md)
                    vergleich.update({
                        "quelle": md["quelle"],
                        "titel_api": md.get("titel", ""),
                        "autoren_api": md.get("autoren", [])
                    })
                    res_liste.append(vergleich)
        else:
            md_liste = get_metadata_from_all_sources(eintrag["id"])
            for md in md_liste:
                vergleich = vergleiche(eintrag, md)
                vergleich.update({
                    "quelle": md["quelle"],
                    "titel_api": md.get("titel", ""),
                    "autoren_api": md.get("autoren", [])
                })
                res_liste.append(vergleich)

        if any(r["titel_score"] >= 85 and r["autor_match"] for r in res_liste):
            status = "✅ Übereinstimmung"
        elif any(r["titel_score"] >= 85 or r["autor_match"] for r in res_liste):
            status = "⚠️ Teilweise Übereinstimmung"
        else:
            status = "❌ Keine Übereinstimmung"

        beste_quelle = None
        if res_liste:
            res_liste.sort(key=lambda r: (r["titel_score"], r["autor_match"]), reverse=True)
            beste_quelle = res_liste[0]

        gesamt_ergebnisse.append({
            "Titel (Input)": eintrag["titel"],
            "Autor (Input)": eintrag["autor"],
            "Typ": eintrag["typ"].upper(),
            "ID": eintrag["id"],
            "Status": status,
            "Beste Quelle": beste_quelle["quelle"] if beste_quelle else "-",
            "Titel (API)": beste_quelle["titel_api"] if beste_quelle else "-",
            "Autor:innen (API)": ", ".join(beste_quelle["autoren_api"]) if beste_quelle and isinstance(beste_quelle["autoren_api"], list) else (beste_quelle["autoren_api"] if beste_quelle else "-"),
            "Similarity (Titel)": beste_quelle["titel_score"] if beste_quelle else 0
        })

    if gesamt_ergebnisse:
        df = pd.DataFrame(gesamt_ergebnisse)

        def highlight_status(val):
            if isinstance(val, str):
                if val.startswith("✅"):
                    return 'background-color: #d4edda; color: black;'
                elif val.startswith("⚠️"):
                    return 'background-color: #fff3cd; color: black;'
                elif val.startswith("❌"):
                    return 'background-color: #f8d7da; color: black;'
            return ''

        styled_html = (
            df.style
            .applymap(highlight_status, subset=["Status"])
            .set_table_styles([
                {'selector': 'table', 'props': [('width', '100%'), ('border-collapse', 'collapse')]},
                {'selector': 'th', 'props': [('text-align', 'left'), ('padding', '6px')]},
                {'selector': 'td', 'props': [('padding', '6px'), ('max-width', '400px'), ('white-space', 'normal')]}
            ])
            .hide(axis="index")
            .to_html()
        )

        st.markdown("### Ergebnisse")
        st.markdown(styled_html, unsafe_allow_html=True)

        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Ergebnisse als CSV herunterladen",
            data=csv,
            file_name='literaturcheck_ergebnisse.csv',
            mime='text/csv'
        )


# ----------------------------------------------------
# Streamlit UI
# ----------------------------------------------------

def main():
    st.title("Litcheck Historia.Scribere ALPHA")
    st.caption("Hinweis: Experimentelle Version – es kann noch zu Fehlalarmen kommen.")

    datei = st.file_uploader("Lade Bibliographie (.txt oder .docx) hoch", type=["txt", "docx"])
    if datei:
        zeilen = lade_datei(datei)
        if zeilen:
            einträge = parse_einträge(zeilen)
            if einträge:
                überprüfe(einträge)
            else:
                st.warning("Keine gültigen Literatureinträge gefunden.")
        else:
            st.warning("Datei ist leer oder konnte nicht gelesen werden.")


if __name__ == "__main__":
    main()
