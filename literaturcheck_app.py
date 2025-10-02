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
# ISBN Normalisierung & Varianten
# ===============================

def normalize_isbn(isbn: str) -> str:
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

def generate_isbn_variants(isbn: str) -> list:
    variants = set()
    clean = re.sub(r"[^0-9Xx]", "", isbn).upper()
    variants.add(clean)  # nur reine Nummer, ohne Bindestriche
    
    # ISBN10 → ISBN13 umwandeln (wenn ISBN10)
    if len(clean) == 10:
        variants.add(isbn10_to_isbn13(clean))
    
    # Optional: ISBN13 mit 978-Präfix ohne Prüfziffer (Core)
    # falls relevant, kannst du das drinlassen oder weglassen
    if len(clean) == 13 and clean.startswith("978"):
        core = clean[3:-1]
        variants.add(core)
    
    return list(variants)

# ===============================
# DOI-Quellen
# ===============================

def get_metadata_crossref(doi):
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}", timeout=6)
        if r.status_code != 200:
            return None
        data = r.json()["message"]
        titel = data.get("title", [""])[0]
        autoren = [a.get("family", "") for a in data.get("author", []) if "family" in a]
        return {"quelle": "CrossRef", "titel": titel, "autoren": autoren}
    except:
        return None

def get_metadata_doi_rest(doi):
    try:
        url = f"https://doi.org/{doi}"
        headers = {"Accept": "application/citeproc+json"}
        r = requests.get(url, headers=headers, timeout=6)
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
        r = requests.get(url, timeout=6)
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
        r = requests.get(url, timeout=6)
        data = r.json()
        if "items" not in data:
            return None
        volume_info = data["items"][0].get("volumeInfo", {})
        titel = volume_info.get("title", "")
        autoren = volume_info.get("authors", [])
        return {"quelle": "Google Books", "titel": titel, "autoren": autoren}
    except:
        return None

def get_metadata_worldcat_sru(isbn):
    try:
        url = f"https://worldcat.org/webservices/catalog/search/sru?version=1.2&operation=searchRetrieve&query=isbn={isbn}&maximumRecords=1"
        headers = {"Accept": "application/xml"}
        r = requests.get(url, headers=headers, timeout=8)
        tree = etree.fromstring(r.content)
        ns = {"srw": "http://www.loc.gov/zing/srw/"}
        records = tree.findall(".//srw:record", ns)
        if not records:
            return None
        titel, autoren = None, []
        for elem in records[0].iter():
            if elem.tag.endswith("title") and not titel:
                titel = elem.text
            if elem.tag.endswith("name"):
                autoren.append(elem.text)
        return {"quelle": "WorldCat", "titel": titel or "", "autoren": autoren}
    except:
        return None

def query_isbn_sources(isbn, titel=None, langsame=False):
    results = []
    variants = generate_isbn_variants(isbn)
    
    for variant in variants:
        for func in [get_metadata_googlebooks, get_metadata_openlibrary]:
            try:
                md = func(variant)
                if md:
                    md["isbn_variant"] = variant
                    results.append(md)
            except:
                continue

        if langsame:
            try:
                md = get_metadata_worldcat_sru(variant)
                if md:
                    md["isbn_variant"] = variant
                    results.append(md)
            except:
                continue


    # 2) DNB/ZDB (ISBN → Fallback Titel)
    if langsame:
        try:
            dnb = get_metadata_dnb({"id": isbn, "typ": "isbn", "titel": titel, "autor": ""})
            if dnb:
                results.append(dnb)
        except:
            pass
        try:
            zdb = get_metadata_zdb({"id": isbn, "typ": "isbn", "titel": titel, "autor": ""})
            if zdb:
                results.append(zdb)
        except:
            pass

    # 3) Falls noch nichts gefunden → reine Titelsuche (langsamer)
    if not results and titel and langsame:
        for func in [get_metadata_dnb, get_metadata_zdb]:
            try:
                md = func({"id": None, "typ": "titel", "titel": titel, "autor": ""})
                if md:
                    results.append(md)
            except:
                continue

    return results

# ===============================
# DNB & ZDB SRU-Schnittstellen
# ===============================

def parse_marcxml_records(xml_content, quelle):
    """Parst MARCXML von DNB/ZDB und extrahiert Titel & Autoren."""
    try:
        tree = etree.fromstring(xml_content)
        ns = {"marc": "http://www.loc.gov/MARC21/slim"}
        records = []

        for record in tree.findall(".//marc:record", ns):
            titel = ""
            autoren = []

            for df in record.findall("marc:datafield", ns):
                tag = df.attrib.get("tag")
                if tag == "245":  # Titel
                    sub_a = df.find("marc:subfield[@code='a']", ns)
                    if sub_a is not None:
                        titel = sub_a.text or ""
                if tag in ["100", "700"]:  # Autoren
                    sub_a = df.find("marc:subfield[@code='a']", ns)
                    if sub_a is not None:
                        autoren.append(sub_a.text or "")

            if titel:
                records.append({"quelle": quelle, "titel": titel, "autoren": autoren})

        return records
    except Exception as e:
        return []


def query_dnb(isbn=None, titel=None):
    """Fragt die DNB per SRU ab – zuerst ISBN, dann Titel."""
    base = "https://services.dnb.de/sru/dnb"
    params = {"version": "1.1", "operation": "searchRetrieve", "maximumRecords": "5", "recordSchema": "MARC21-xml"}

    # Erst ISBN
    if isbn:
        params["query"] = f"pica.isb={isbn}"
        try:
            r = requests.get(base, params=params, timeout=10)
            recs = parse_marcxml_records(r.content, "DNB")
            if recs:
                return recs
        except:
            pass

    # Fallback: Titel
    if titel:
        params["query"] = f"pica.tit={titel}"
        try:
            r = requests.get(base, params=params, timeout=10)
            return parse_marcxml_records(r.content, "DNB")
        except:
            return []
    return []


def query_zdb(isbn=None, titel=None):
    """Fragt die ZDB per SRU ab – zuerst ISBN, dann Titel."""
    base = "https://services.dnb.de/sru/zdb"
    params = {"version": "1.1", "operation": "searchRetrieve", "maximumRecords": "5", "recordSchema": "MARC21-xml"}

    if isbn:
        params["query"] = f"pica.isb={isbn}"
        try:
            r = requests.get(base, params=params, timeout=10)
            recs = parse_marcxml_records(r.content, "ZDB")
            if recs:
                return recs
        except:
            pass

    if titel:
        params["query"] = f"pica.tit={titel}"
        try:
            r = requests.get(base, params=params, timeout=10)
            return parse_marcxml_records(r.content, "ZDB")
        except:
            return []
    return []


# ===============================
# Wrapper für Metadaten-Abfragen
# ===============================

def get_metadata_dnb(eintrag):
    recs = query_dnb(isbn=eintrag["id"] if eintrag["typ"]=="isbn" else None,
                     titel=eintrag["titel"])
    if not recs:
        return None
    # besten Treffer wählen
    best = max(recs, key=lambda r: fuzz.token_sort_ratio(eintrag["titel"].lower(), r["titel"].lower()))
    return best

def get_metadata_zdb(eintrag):
    recs = query_zdb(isbn=eintrag["id"] if eintrag["typ"]=="isbn" else None,
                     titel=eintrag["titel"])
    if not recs:
        return None
    best = max(recs, key=lambda r: fuzz.token_sort_ratio(eintrag["titel"].lower(), r["titel"].lower()))
    return best


def fetch_all_metadata(eintrag, quellen, langsame=False):
    if eintrag["typ"] == "isbn":
        md_list = query_isbn_sources(eintrag["id"], eintrag["titel"], langsame=langsame)
        return [vergleiche(eintrag, md) for md in md_list]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=len(quellen)) as executor:
            futures = {executor.submit(q, eintrag["id"]): q for q in quellen}
            for f in as_completed(futures):
                try:
                    md = f.result(timeout=6)
                    results.append(vergleiche(eintrag, md))
                except:
                    continue
        return results

# ===============================
# Vergleichsfunktionen
# ===============================

def vergleiche(eintrag, metadata):
    if not metadata:
        return {"quelle": "keine", "titel_score": 0, "autor_match": False, "autoren_api": []}

    titel_score = fuzz.token_sort_ratio(
        eintrag["titel"].lower(), 
        metadata["titel"].lower()
    )

    # Autor-Vergleich toleranter machen
    autor_score = 0
    for a in metadata.get("autoren", []):
        autor_score = max(autor_score, fuzz.partial_ratio(eintrag["autor"].lower(), a.lower()))

    autor_match = autor_score >= 60   # vorher 70

    # Kombinierte Bewertung
    combined_score = titel_score
    if autor_match:
        combined_score += 15   # Bonus, wenn Autor passt

    return {
        "quelle": metadata["quelle"],
        "titel_score": combined_score,
        "autor_match": autor_match,
        "autoren_api": metadata.get("autoren", []),
    }


# ===============================
# Ergebnisdarstellung
# ===============================

def highlight_rows(row):
    if row["Titel-Ähnlichkeit (%)"] >= 85 and row["Autor:in gefunden"] == "Ja":
        return ["background-color: #c8e6c9"] * len(row)
    elif row["Titel-Ähnlichkeit (%)"] >= 70 or row["Autor:in gefunden"] == "Ja":
        return ["background-color: #fff9c4"] * len(row)
    else:
        return ["background-color: #ffcdd2"] * len(row)


def überprüfe(einträge, langsame_quellen=False):
    beste_ergebnisse = []

    for eintrag in einträge:
        st.markdown(f"### 🔍 {eintrag['titel']} ({eintrag['autor']})")

        if eintrag["typ"] == "doi":
            quellen = [get_metadata_crossref, get_metadata_doi_rest]
            res_list = fetch_all_metadata(eintrag, quellen)
        else:
            res_list = fetch_all_metadata(eintrag, [], langsame=langsame_quellen)

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
    st.caption("Prüft DOIs und ISBNs gegen mehrere Datenbanken (mit Fallback auf Titelsuche). Erkennt derzeit nur Monographien und Sammelbände einigermaßen zuverlässig. Achtung: viele False Negatives!")

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
