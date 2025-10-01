import streamlit as st
import requests
import re
import pandas as pd
from lxml import etree
from fuzzywuzzy import fuzz
from docx import Document

# ---------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------

def normalize_author(name: str) -> str:
    """Normalisiert Autorennamen (Vorname Nachname)."""
    if not name:
        return ""
    name = name.strip()
    parts = re.split(r",\s*", name)
    if len(parts) == 2:
        return f"{parts[1]} {parts[0]}"
    return name


def normalize_isbn(isbn: str) -> str:
    """Normalisiert ISBN und konvertiert ISBN-10 nach ISBN-13."""
    isbn = re.sub(r"[^0-9Xx]", "", isbn)
    if len(isbn) == 10:
        return isbn10_to_isbn13(isbn)
    return isbn


def isbn10_to_isbn13(isbn10: str) -> str:
    """Konvertiert ISBN-10 nach ISBN-13."""
    prefix = "978" + isbn10[:-1]
    total = 0
    for i, digit in enumerate(prefix):
        factor = 1 if i % 2 == 0 else 3
        total += int(digit) * factor
    check = (10 - (total % 10)) % 10
    return prefix + str(check)


# ---------------------------------------------------
# Parser
# ---------------------------------------------------

def parse_einträge(text: str):
    einträge = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # ISBN erkennen
        isbn_match = re.search(r"ISBN[:\s]*([0-9\-Xx]+)", line)
        if isbn_match:
            isbn = normalize_isbn(isbn_match.group(1))
            autor = line.split(",")[0]
            titel = re.split(r"\[ISBN", line)[0]
            einträge.append({"typ": "isbn", "id": isbn, "titel": titel, "autor": [normalize_author(autor)]})
            continue

        # DOI erkennen
        doi_match = re.search(r"(10\.\d{4,9}/\S+)", line)
        if doi_match:
            doi = doi_match.group(1)
            autor = line.split(",")[0]
            titel = re.split(r"\[DOI", line)[0]
            einträge.append({"typ": "doi", "id": doi, "titel": titel, "autor": [normalize_author(autor)]})
            continue

    return einträge


# ---------------------------------------------------
# DOI-APIs
# ---------------------------------------------------

def get_metadata_crossref(doi):
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}")
        if r.status_code != 200:
            return None
        data = r.json()["message"]
        titel = data.get("title", [""])[0]
        autoren = [a.get("family", "") for a in data.get("author", []) if "family" in a]
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
        nachname = autor_raw.split(",")[0] if "," in autor_raw else autor_raw
        return {"quelle": "OpenCitations", "titel": titel, "autoren": [nachname]}
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
        autoren_liste = [a.get("name", "") for a in bib.get("author", []) if a.get("name")]
        return {"quelle": "DOAJ", "titel": titel, "autoren": autoren_liste}
    except:
        return None


def get_metadata_datacite(doi):
    try:
        url = f"https://api.datacite.org/works/{doi}"
        r = requests.get(url)
        if r.status_code != 200:
            return None
        data = r.json().get("data", {}).get("attributes", {})
        titel = data.get("title", [""])[0] if isinstance(data.get("title"), list) else data.get("title", "")
        autoren = [c.get("familyName", "") for c in data.get("creators", []) if "familyName" in c]
        return {"quelle": "DataCite", "titel": titel, "autoren": autoren}
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
        autoren = [a.get("family", "") for a in data.get("author", []) if "family" in a]
        return {"quelle": "DOI REST API", "titel": titel, "autoren": autoren}
    except:
        return None


# ---------------------------------------------------
# ISBN-APIs
# ---------------------------------------------------

def get_metadata_openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&jscmd=data&format=json"
        r = requests.get(url)
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
        r = requests.get(url)
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
                autoren.append(elem.text)
        return {"quelle": "WorldCat", "titel": titel or "", "autoren": autoren}
    except:
        return None


def get_metadata_dnb(isbn):
    try:
        url = f"https://services.dnb.de/sru/dnb?version=1.1&operation=searchRetrieve&query=isbn={isbn}&recordSchema=MARC21-xml"
        r = requests.get(url)
        if r.status_code != 200:
            return None
        tree = etree.fromstring(r.content)
        titel = None
        autoren = []
        for elem in tree.iter():
            if elem.tag.endswith("title") and not titel:
                titel = elem.text
            if elem.tag.endswith("name"):
                autoren.append(elem.text)
        return {"quelle": "DNB", "titel": titel or "", "autoren": autoren}
    except:
        return None


def get_metadata_zdb(isbn):
    try:
        url = f"https://services.dnb.de/sru/zdb?version=1.1&operation=searchRetrieve&query=isbn={isbn}&recordSchema=MARC21-xml"
        r = requests.get(url)
        if r.status_code != 200:
            return None
        tree = etree.fromstring(r.content)
        titel = None
        autoren = []
        for elem in tree.iter():
            if elem.tag.endswith("title") and not titel:
                titel = elem.text
            if elem.tag.endswith("name"):
                autoren.append(elem.text)
        return {"quelle": "ZDB", "titel": titel or "", "autoren": autoren}
    except:
        return None


# ---------------------------------------------------
# Vergleich
# ---------------------------------------------------

def vergleiche(eintrag, metadata):
    if not metadata:
        return {"quelle": "unbekannt", "titel_score": 0, "autor_match": False, "autoren_api": []}

    titel_score = fuzz.token_sort_ratio(
        str(eintrag["titel"]).lower(),
        str(metadata.get("titel", "")).lower()
    )

    autoren_input = eintrag["autor"]
    if isinstance(autoren_input, str):
        autoren_input = [autoren_input]
    autoren_api = metadata.get("autoren", [])

    autor_match = False
    for a_in in autoren_input:
        for a_api in autoren_api:
            if not a_in or not a_api:
                continue
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
        "autoren_api": autoren_api
    }


def highlight_rows(row):
    if row["Titel-Ähnlichkeit (%)"] >= 85 and row["Autor:in gefunden"] == "Ja":
        return ['background-color: #c8e6c9'] * len(row)
    elif row["Titel-Ähnlichkeit (%)"] >= 70 or row["Autor:in gefunden"] == "Ja":
        return ['background-color: #fff9c4'] * len(row)
    else:
        return ['background-color: #ffcdd2'] * len(row)


def überprüfe(einträge):
    ergebnisse = []

    for eintrag in einträge:
        if eintrag["typ"] == "isbn":
            isbn_norm = normalize_isbn(eintrag["id"])
            eintrag["id"] = isbn_norm
            quellen = [get_metadata_openlibrary, get_metadata_googlebooks,
                       get_metadata_worldcat_sru, get_metadata_dnb, get_metadata_zdb]
        else:
            quellen = [get_metadata_crossref, get_metadata_opencitations,
                       get_metadata_doaj, get_metadata_datacite, get_metadata_doi_rest]

        res_list = []
        for q in quellen:
            md = q(eintrag["id"])
            res_list.append(vergleiche(eintrag, md))

        if res_list:
            best = max(res_list, key=lambda r: r["titel_score"])
            ergebnisse.append({
                "Titel (Eingabe)": eintrag["titel"],
                "Autor:innen (Eingabe)": ", ".join(eintrag["autor"]) if isinstance(eintrag["autor"], list) else eintrag["autor"],
                "Quelle": best["quelle"],
                "Titel-Ähnlichkeit (%)": best["titel_score"],
                "Autor:in gefunden": "Ja" if best["autor_match"] else "Nein",
                "Autor:innen (API)": ", ".join(best["autoren_api"])
            })

    if ergebnisse:
        df = pd.DataFrame(ergebnisse)
        styled = df.style.apply(highlight_rows, axis=1)
        st.dataframe(styled, use_container_width=True)
    else:
        st.warning("Keine Ergebnisse gefunden.")


# ---------------------------------------------------
# Streamlit UI
# ---------------------------------------------------

def main():
    st.title("📚 Litcheck – Literatur-Validierung")

    uploaded_file = st.file_uploader("Literaturliste hochladen (TXT oder DOCX)", type=["txt", "docx"])
    if uploaded_file:
        if uploaded_file.type == "text/plain":
            text = uploaded_file.read().decode("utf-8")
        else:
            doc = Document(uploaded_file)
            text = "\n".join([p.text for p in doc.paragraphs])

        einträge = parse_einträge(text)
        if not einträge:
            st.warning("Keine ISBN oder DOI im Text gefunden.")
        else:
            überprüfe(einträge)


if __name__ == "__main__":
    main()
