import streamlit as st
import re
import requests
from fuzzywuzzy import fuzz
from docx import Document
from lxml import etree
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===============================
# Hilfsfunktionen
# ===============================

def lade_datei(datei):
    """Liest TXT oder DOCX und gibt Zeilenliste zurück"""
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
    """Extrahiert DOI/ISBN, Autor, Titel"""
    einträge = []
    for zeile in zeilen:
        try:
            doi_match = re.search(r'\[DOI:\s*(10\.\S+?)\]', zeile)
            isbn_match = re.search(r'\[ISBN:\s*([\d\-]+)\]', zeile)

            if doi_match:
                identifier = doi_match.group(1).strip()
                id_typ = "doi"
            elif isbn_match:
                identifier = normalize_isbn(isbn_match.group(1))
                id_typ = "isbn"
            else:
                continue

            autor_teil = zeile.split(',')[0].strip()
            autor_teil = re.sub(r"\(Hrsg\.\)", "", autor_teil, flags=re.IGNORECASE)
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
        except Exception:
            continue
    return einträge


# ===============================
# ISBN Normalisierung
# ===============================

def normalize_isbn(isbn: str) -> str:
    """Entfernt Bindestriche und Leerzeichen, wandelt ISBN10 → ISBN13"""
    isbn = re.sub(r"[^0-9Xx]", "", isbn)
    if len(isbn) == 10:
        return isbn10_to_isbn13(isbn)
    return isbn

def isbn10_to_isbn13(isbn10: str) -> str:
    prefix = "978" + isbn10[:-1]
    total = 0
    for i, digit in enumerate(prefix):
        factor = 1 if i % 2 == 0 else 3
        total += int(digit) * factor
    check = (10 - (total % 10)) % 10
    return prefix + str(check)


# ===============================
# DOI-Quellen
# ===============================

def get_metadata_crossref(doi):
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}", timeout=5)
        if r.status_code != 200:
            return None
        data = r.json()["message"]
        titel = data.get("title", [""])[0]
        autoren = [a["family"] for a in data.get("author", []) if "family" in a]
        return {"quelle": "CrossRef", "titel": titel, "autoren": autoren}
    except:
        return None

def get_metadata_doi_rest(doi):
    try:
        url = f"https://doi.org/{doi}"
        headers = {"Accept": "application/citeproc+json"}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code != 200:
            return None
        data = r.json()
        titel = data.get("title", "")
        autoren = [a.get("family", "") for a in data.get("author", []) if "family" in a] if "author" in data else []
        return {"quelle": "DOI REST", "titel": titel, "autoren": autoren}
    except:
        return None


# ===============================
# ISBN-Quellen
# ===============================

def get_metadata_openlibrary(isbn):
    try:
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&jscmd=data&format=json"
        r = requests.get(url, timeout=5)
        data = r.json().get(f"ISBN:{isbn}")
        if not data:
            return None
        titel = data.get("title", "")
        autoren = [a.get("name", "") for a in data.get("authors", [])]
        return {"quelle": "OpenLibrary", "titel": titel, "autoren": autoren}
    except:
        return None

def get_metadata_googlebooks(isbn):
    results = []
    for candidate in {isbn, isbn10_to_isbn13(isbn) if len(isbn)==10 else isbn}:
        try:
            url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{candidate}"
            r = requests.get(url, timeout=5)
            data = r.json()
            if 'items' in data:
                volume_info = data['items'][0].get('volumeInfo', {})
                titel = volume_info.get('title', '')
                autoren = volume_info.get('authors', [])
                results.append({"quelle": "Google Books", "titel": titel, "autoren": autoren})
        except:
            continue
    return results[0] if results else None

def get_metadata_worldcat_sru(isbn):
    try:
        url = f"https://worldcat.org/webservices/catalog/search/sru?version=1.2&operation=searchRetrieve&query=isbn={isbn}&maximumRecords=1"
        headers = {'Accept': 'application/xml'}
        r = requests.get(url, headers=headers, timeout=5)
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


# ===============================
# Vergleichsfunktionen
# ===============================

def vergleiche(eintrag, metadata):
    if not metadata:
        return {"quelle": "keine", "titel_score": 0, "autor_match": False, "autoren_api": []}
    titel_score = fuzz.token_sort_ratio(eintrag["titel"].lower(), metadata["titel"].lower())
    autor_match = any(
        fuzz.partial_ratio(eintrag["autor"].lower(), a.lower()) >= 70
        for a in metadata.get("autoren", [])
    )
    return {
        "quelle": metadata["quelle"],
        "titel_score": titel_score,
        "autor_match": autor_match,
        "autoren_api": metadata.get("autoren", [])
    }


def fetch_all_metadata(eintrag, quellen):
    results = []
    with ThreadPoolExecutor(max_workers=len(quellen)) as executor:
        future_to_source = {executor.submit(q, eintrag["id"]): q for q in quellen}
        for future in as_completed(future_to_source):
            try:
                md = future.result(timeout=6)
                results.append(vergleiche(eintrag, md))
            except Exception:
                continue
    return results


# ===============================
# Ergebnisdarstellung
# ===============================

def highlight_rows(row):
    if row["Titel-Ähnlichkeit (%)"] >= 85 and row["Autor:in gefunden"] == "Ja":
        return ['background-color: #c8e6c9'] * len(row)
    elif row["Titel-Ähnlichkeit (%)"] >= 70 or row["Autor:in gefunden"] == "Ja":
        return ['background-color: #fff9c4'] * len(row)
    else:
        return ['background-color: #ffcdd2'] * len(row)


def überprüfe(einträge, langsame_quellen=False):
    beste_ergebnisse = []

    for eintrag in einträge:
        st.markdown(f"### 🔍 {eintrag['titel']} ({eintrag['autor']})")

        if eintrag["typ"] == "doi":
            quellen = [get_metadata_crossref, get_metadata_doi_rest]
        else:
            quellen = [get_metadata_googlebooks, get_metadata_openlibrary]
            if langsame_quellen:
                quellen += [get_metadata_worldcat_sru]  # + DNB/ZDB möglich

        res_list = fetch_all_metadata(eintrag, quellen)

        if res_list:
            best = max(res_list, key=lambda r: r["titel_score"])
            beste_ergebnisse.append({
                "Titel (Input)": eintrag["titel"],
                "Autor (Input)": eintrag["autor"],
                "ID": eintrag["id"],
                "Quelle (beste)": best["quelle"],
                "Titel-Ähnlichkeit (%)": best["titel_score"],
                "Autor:in gefunden": "Ja" if best["autor_match"] else "Nein",
                "Autor:innen (API)": ", ".join(best["autoren_api"])
            })
        else:
            beste_ergebnisse.append({
                "Titel (Input)": eintrag["titel"],
                "Autor (Input)": eintrag["autor"],
                "ID": eintrag["id"],
                "Quelle (beste)": "keine",
                "Titel-Ähnlichkeit (%)": 0,
                "Autor:in gefunden": "Nein",
                "Autor:innen (API)": ""
            })

    if beste_ergebnisse:
        df = pd.DataFrame(beste_ergebnisse)
        styled = df.style.apply(highlight_rows, axis=1)
        st.dataframe(styled, use_container_width=True)


# ===============================
# Streamlit UI
# ===============================

def main():
    st.title("Litcheck Historia.Scribere BETA")
    st.caption("Prüft DOIs und ISBNs gegen mehrere Datenbanken (schnelle Quellen standardmäßig, langsame optional).")

    langsame = st.checkbox("Auch langsame Quellen (WorldCat, DNB, ZDB) einbeziehen", value=False)

    datei = st.file_uploader("Lade Bibliographie (.txt oder .docx) hoch", type=["txt", "docx"])

    if datei:
        zeilen = lade_datei(datei)
        if zeilen:
            einträge = parse_einträge(zeilen)
            if einträge:
                überprüfe(einträge, langsame_quellen=langsame)
            else:
                st.warning("Keine gültigen Literatureinträge gefunden.")
        else:
            st.warning("Datei ist leer oder konnte nicht gelesen werden.")


if __name__ == "__main__":
    main()
