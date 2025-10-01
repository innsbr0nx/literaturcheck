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
# Parser für Literaturlisten
# ---------------------------------------------------

def parse_einträge(text: str):
    """Extrahiert Einträge aus einer Literaturliste."""
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
# API-Abfragen (ISBN + DOI) – wie vorher definiert
# ---------------------------------------------------
# hier kommen get_metadata_openlibrary, get_metadata_googlebooks,
# get_metadata_worldcat_sru, get_metadata_dnb, get_metadata_zdb,
# get_metadata_crossref, get_metadata_opencitations, get_metadata_doaj,
# get_metadata_datacite, get_metadata_doi_rest (gekürzt, siehe vorige Version)

# ---------------------------------------------------
# Vergleich und Darstellung
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
        return ['background-color: #c8e6c9'] * len(row)  # grün
    elif row["Titel-Ähnlichkeit (%)"] >= 70 or row["Autor:in gefunden"] == "Ja":
        return ['background-color: #fff9c4'] * len(row)  # gelb
    else:
        return ['background-color: #ffcdd2'] * len(row)  # rot


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
        else:  # DOCX
            doc = Document(uploaded_file)
            text = "\n".join([p.text for p in doc.paragraphs])

        einträge = parse_einträge(text)
        if not einträge:
            st.warning("Keine ISBN oder DOI im Text gefunden.")
        else:
            überprüfe(einträge)


if __name__ == "__main__":
    main()
