Diese kleine Streamlit-App ist ein reiner Proof-of-Concept, größtenteils mit KI-generiertem Code, der sicherlich zahlreiche Fehler enthält. 
Der Zweck der App ist es, eine Literaturliste mithilfe mehrere Datenbanken zu überprüfen. Konkret geht es darum, die Existenz von Titeln 
nachzuweisen. Die App funktioniert nur mit einem speziellen Zitierstil - einer vereinfachten Variante der zukünftigen historia.scribere-Regeln.
Derzeit erkennt das Skript nur Monographien und Sammelbände relativ zuverlässig.

Bekannte Probleme:
- Es wird nur der Erstautor erkannt
- Es werden nur Titelteile vor dem ersten Komma erkannt
- Beiträge in Sammelbänden ohne eigene DOI (Nur mit ISBN) werden nicht richtig erkannt
- Titel ohne ISBN oder DOI werden übersprungen
