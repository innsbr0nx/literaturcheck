import streamlit as st
import re
import requests
from fuzzywuzzy import fuzz
from docx import Document
from lxml import etree
import pandas as pd

# ---------------------------------------------------
# Hilfsfunktionen zur Namensnormalisierung
# ---------------------------------------------------

def normalize_author(name: str) -> str:
    """Normalisiert Autor:innen-Namen ins Format 'Vorname Nachname'."""
    name = name.strip()
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2:
            return f"{parts[1]} {parts[0]}".strip()
    return name

# ---------------------------------------------------
# Datei-Upload & Parsing
# ---------------------------------------------------

def lade_datei(datei):
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

            # Autoren extrahieren (alles vor dem ersten Komma)
            autor_roh = zeile.split(',')[0]
            autor_roh = re.sub(r"\(Hrsg\.\)", "", autor_roh)
            autor_roh = autor_roh.replace("et al.", "").replace("et al", "")

            # Split nach "und" oder "u." oder "&"
            autoren = re.split(r" und | u\. | & ", autor_roh)
            autoren = [normalize_author(a) for a in autoren if a.strip()]

            teile = zeile.split(',')
            titel = teile[2].strip() if len(teile) >= 3 else "unbekannter Titel"

            einträge.append({
                'typ': id_typ,
                'id': identifier,
                'titel': titel,
                'autor': autoren
            })
        except:
            continue
    return einträge

# ---------------------------------------------------
# DOI-Quellen
# ---------------------------------------------------

def get_metadata_crossref(doi):
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}")
        if r.status_code != 200:
            return None
        data = r.json()["message"]
        titel = data.get("title", [""])[0]
        autoren = [normalize_author(f"{a.get('given','')} {a.get('family','')}".strip()) 
                   for a in data.get("author", []) if "family" in a]
        return {"quelle": "CrossRef", "titel": titel, "autoren": autoren}
    except:
        return None


def get_metadata_opencitations(doi):
    try:
        r = requests.get(f"https://opencitations.net/index/api/v1/metadata/{doi}")
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or not isinstance(data, list):
            return None
        eintrag = data[0]
        titel = eintrag.get("title", "")
        autor_raw = eintrag.get("author", "")
        autor = normalize_author(autor_raw)
        return {"quelle": "OpenCitations", "titel": titel, "autoren": [autor]}
    except:
        return None


def get_metadata_doaj(doi):
    try:
        url = f"https://doaj.org/api/v2/search/articles/doi:{doi.replace('/', '%2F')}"
        r = requests.get(url)
        if r.status_code != 200:
            return None
        data = r.json()
        if "results" not in data or not data["results"]:
            return None
        artikel = data["results"][0]
        bib = artikel.get("bibjson", {})
        titel = bib.get("title", "")
        autoren_liste = [normalize_author(a.get("name", "")) for a in bib.get("author", []) if a.get("name")]
        return {"quelle": "DOAJ", "titel": titel, "autoren": autoren_liste}
    except:
        return None


def get_metadata_doi_rest(doi):
    try:
        url = f"https://doi.org/{doi}"
        headers = {"Accept": "application/citeproc+json"}
        r = requests.get(url, headers=headers)
        if r.status_code != 200:
            return None
        data = r.json()
        titel = data.get("title", "")
        autoren = [normalize_author(f"{a.get('given','')} {a.get('family','')}".strip()) 
                   for a in data.get("author", [])] if "author" in data else []
        return {"quelle": "DOI REST API", "titel": titel, "autoren": autoren}
    except:
        return None

# ---------------------------------------------------
# ISBN-Quellen
# ---------------------------------------------------

def get_metadata_openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&jscmd=data&format=json"
        r = requests.get(url)
        data = r.json().get(f"ISBN:{isbn}")
        if not data:
            return None
        titel = data.get("title", "")
        autoren = [normalize_author(a.get("name", "")) for a in data.get("authors", [])]
        return {"quelle": "OpenLibrary", "titel": titel, "autoren": autoren}
    except:
        return None


def get_metadata_googlebooks(isbn):
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
        r = requests.get(url)
        data = r.json()
        if 'items' not in data:
            return None
        volume_info = data['items'][0].get('volumeInfo', {})
        titel = volume_info.get('title', '')
        autoren = [normalize_author(a) for a in volume_info.get('authors', [])]
        return {"quelle": "Google Books", "titel": titel, "autoren": autoren}
    except:
        return None


def get_metadata_worldcat_sru(isbn):
    try:
        url = f"https://worldcat.org/webservices/catalog/search/sru?version=1.2&operation=searchRetrieve&query=isbn={isbn}&maximumRecords=1"
        headers = {'Accept': 'application/xml'}
        r = requests.get(url, headers=headers)
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
                autoren.append(normalize_author(elem.text))
        return {"quelle": "WorldCat", "titel": titel or "", "autoren": autoren}
    except:
        return None

# ---------------------------------------------------
# Vergleich
# ---------------------------------------------------

def vergleiche(eintrag, metadata):
    """Vergleicht den eingelesenen Eintrag mit API-Metadaten."""
    if not metadata:
        return {
            "quelle": "unbekannt",
            "titel_score": 0,
            "autor_match": False,
            "autoren_input": eintrag["autor"],
            "autoren_api": []
        }

    titel_score = fuzz.token_sort_ratio(
        str(eintrag["titel"]).lower(),
        str(metadata.get("titel", "")).lower()
    )

    autoren_input = eintrag["autor"]
    autoren_api = metadata.get("autoren", [])

    autor_match = False
    for a_in in autoren_input:
        for a_api in autoren_api:
            score = fuzz.partial_ratio(a_in.lower(), a_api.lower())
            if score >= 80:
                autor_match = True
                break
        if autor_match:
            break

    return {
        "quelle": metadata["quelle"],
        "titel_score": titel_score,
        "autor_match": autor_match,
        "autoren_input": autoren_input,
        "autoren_api": autoren_api
    }

# ---------------------------------------------------
# Hauptlogik
# ---------------------------------------------------

def überprüfe(einträge):
    alle_ergebnisse = []

    for eintrag in einträge:
        st.markdown(f"### 🔍 {eintrag['titel']} ({', '.join(eintrag['autor'])})")
        ergebnisse = []

        if eintrag["typ"] == "doi":
            quellen = [get_metadata_crossref, get_metadata_opencitations, get_metadata_doaj, get_metadata_doi_rest]
        else:
            quellen = [get_metadata_openlibrary, get_metadata_googlebooks, get_metadata_worldcat_sru]

        for q in quellen:
            md = q(eintrag["id"])
            res = vergleiche(eintrag, md)
            ergebnisse.append(res)
            st.write(f"{res['quelle']}: Titel-Ähnlichkeit = {res['titel_score']}%, "
                     f"Autor:in gefunden: {'✅' if res['autor_match'] else '❌'}")

            alle_ergebnisse.append({
                "Titel (Input)": eintrag["titel"],
                "Autor:innen (Input)": ", ".join(res["autoren_input"]),
                "Typ": eintrag["typ"],
                "ID": eintrag["id"],
                "Quelle": res["quelle"],
                "Titel-Ähnlichkeit": res["titel_score"],
                "Autor:in gefunden": "Ja" if res["autor_match"] else "Nein",
                "Autor:innen (API)": ", ".join(res["autoren_api"])
            })

    # Gesamttabelle anzeigen
    if alle_ergebnisse:
        df = pd.DataFrame(alle_ergebnisse)
        st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Ergebnisse als CSV herunterladen",
            data=csv,
            file_name='literaturcheck_ergebnisse.csv',
            mime='text/csv'
        )

# ---------------------------------------------------
# Streamlit UI
# ---------------------------------------------------

def main():
    st.title("📚 Litcheck Historia.Scribere ALPHA")
    st.caption("Automatischer DOI/ISBN-Abgleich – experimentell")

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
