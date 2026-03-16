"""Polish-language utilities for Zdrowa portal."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

# ── Lookup tables (Title Case keys) ──────────────────────────────────────────

_FEMALE_VOC: dict[str, str] = {
    # Anna / Hanna
    "Anna": "Anno", "Ania": "Aniu", "Hanna": "Hanno",
    # Maria
    "Maria": "Mario", "Marysia": "Marysiu", "Maryja": "Maryjo",
    # Katarzyna
    "Katarzyna": "Katarzyno", "Kasia": "Kasiu", "Katia": "Katiu",
    # Małgorzata
    "Małgorzata": "Małgorzato", "Gosia": "Gosiu", "Małgosia": "Małgosiu",
    # Agnieszka
    "Agnieszka": "Agnieszko",
    # Barbara
    "Barbara": "Barbaro", "Basia": "Basiu",
    # Elżbieta
    "Elżbieta": "Elżbieto", "Ela": "Elo",
    # Krystyna
    "Krystyna": "Krystyno", "Krysia": "Krysiu",
    # Zofia
    "Zofia": "Zofio", "Zosia": "Zosiu",
    # Teresa
    "Teresa": "Tereso",
    # Jadwiga
    "Jadwiga": "Jadwigo",
    # Danuta
    "Danuta": "Danuto", "Danka": "Danko",
    # Halina
    "Halina": "Halino",
    # Irena
    "Irena": "Ireno",
    # Joanna
    "Joanna": "Joanno", "Asia": "Asiu",
    # Monika
    "Monika": "Moniko",
    # Alicja
    "Alicja": "Alicjo",
    # Beata
    "Beata": "Beato",
    # Wanda
    "Wanda": "Wando",
    # Ewelina / Ewa
    "Ewelina": "Ewelino", "Ewa": "Ewo",
    # Helena
    "Helena": "Heleno",
    # Izabela
    "Izabela": "Izabelo", "Iza": "Izo",
    # Justyna
    "Justyna": "Justyno",
    # Kamila
    "Kamila": "Kamilo",
    # Klaudia
    "Klaudia": "Klaudio",
    # Laura
    "Laura": "Lauro",
    # Lidia
    "Lidia": "Lidio",
    # Magdalena / Magda
    "Magdalena": "Magdaleno", "Magda": "Magdo",
    # Natalia
    "Natalia": "Natalio",
    # Nikola
    "Nikola": "Nikolo",
    # Olga
    "Olga": "Olgo",
    # Patrycja
    "Patrycja": "Patrycjo",
    # Paulina
    "Paulina": "Paulino",
    # Sandra
    "Sandra": "Sandro",
    # Sara
    "Sara": "Saro",
    # Sylwia
    "Sylwia": "Sylwio",
    # Weronika
    "Weronika": "Weroniko",
    # Zuzanna / Zuzia
    "Zuzanna": "Zuzanno", "Zuzia": "Zuziu",
    # Celina
    "Celina": "Celino",
    # Julia
    "Julia": "Julio",
    # Aleksandra / Ola
    "Aleksandra": "Aleksandro", "Ola": "Olo",
    # Dominika
    "Dominika": "Dominiko",
    # Karolina
    "Karolina": "Karolino",
    # Marta
    "Marta": "Marto",
    # Dorota
    "Dorota": "Doroto",
    # Lucja / Lucia
    "Lucja": "Lucjo", "Lucia": "Luciu",
    # Renata
    "Renata": "Renato",
    # Edyta
    "Edyta": "Edyto",
    # Agata
    "Agata": "Agato",
    # Maja / Mia
    "Maja": "Majo",
    # Wiktoria
    "Wiktoria": "Wiktorio",
    # Nina
    "Nina": "Nino",
    # Emilia
    "Emilia": "Emilio",
    # Aleksia
    "Aleks": "Aleks",
    # Oliwia
    "Oliwia": "Oliwio",
    # Amelia
    "Amelia": "Amelio",
    # Zofia alias
    "Zofia": "Zofio",
    # Hania
    "Hania": "Haniu",
    # Misia
    "Misia": "Misiu",
    # Stasia
    "Stasia": "Stasiu",
}

_MALE_VOC: dict[str, str] = {
    "Adam": "Adamie",
    "Aleksander": "Aleksandrze",
    "Andrzej": "Andrzeju",
    "Arkadiusz": "Arkadiuszu",
    "Artur": "Arturze",
    "Bartek": "Bartku", "Bartłomiej": "Bartłomieju", "Bartosz": "Bartoszu",
    "Damian": "Damianie",
    "Daniel": "Danielu",
    "Dariusz": "Dariuszu",
    "Dawid": "Dawidzie",
    "Dominik": "Dominiku",
    "Filip": "Filipie",
    "Grzegorz": "Grzegorzu",
    "Hubert": "Hubercie",
    "Igor": "Igorze",
    "Jacek": "Jacku",
    "Jan": "Janie", "Janek": "Janku",
    "Jarosław": "Jarosławie",
    "Kamil": "Kamilu",
    "Karol": "Karolu",
    "Konrad": "Konradzie",
    "Krystian": "Krystianie",
    "Krzysztof": "Krzysztofie",
    "Kuba": "Kubo",
    "Lech": "Lechu",
    "Łukasz": "Łukaszu",
    "Maciej": "Macieju",
    "Marek": "Marku",
    "Marcin": "Marcinie",
    "Mariusz": "Mariuszu",
    "Mateusz": "Mateuszu",
    "Michał": "Michale",
    "Mikołaj": "Mikołaju",
    "Miłosz": "Miłoszu",
    "Mirosław": "Mirosławie",
    "Oskar": "Oskarze",
    "Patryk": "Patryku",
    "Paweł": "Pawle",
    "Piotr": "Piotrze",
    "Przemek": "Przemku", "Przemysław": "Przemysławie",
    "Rafał": "Rafale",
    "Robert": "Robercie",
    "Sebastian": "Sebastianie",
    "Stanisław": "Stanisławie",
    "Stefan": "Stefanie",
    "Szymon": "Szymonie",
    "Tadeusz": "Tadeuszu",
    "Tomasz": "Tomaszu", "Tomek": "Tomku",
    "Wiktor": "Wiktorze",
    "Wojciech": "Wojciechu",
    "Zbigniew": "Zbigniewie",
    "Zygmunt": "Zygmuncie",
    "Marek": "Marku",
    "Norbert": "Norbercie",
    "Radosław": "Radosławie",
    "Sławomir": "Sławomirze",
    "Waldemar": "Waldemar",   # vocative same as nominative for this name
    "Zenon": "Zenonie",
}


def _normalize(name: str) -> str:
    """Capitalize first letter of first word, lower-case the rest."""
    first = name.strip().split()[0] if name.strip() else name.strip()
    return first[0].upper() + first[1:].lower() if first else ""


def vocative(name: str, gender: str) -> str:
    """
    Return Polish vocative form of *name* for use in greetings.

    Falls back to the nominative (unmodified name) when the declension
    cannot be determined with confidence.

    Parameters
    ----------
    name:   First name (any case; only the first word is used).
    gender: 'female', 'male', or anything else → no change.
    """
    if not name or not name.strip():
        return name
    title = _normalize(name)   # e.g. "ANNA" → "Anna"
    gnd = (gender or "").lower()

    if gnd == "female":
        v = _FEMALE_VOC.get(title)
        if v:
            return v
        low = title.lower()
        # -ia (Kasia→Kasiu, Zosia→Zosiu) — must check before plain -a
        if low.endswith("ia"):
            return title[:-1] + "u"
        # plain -a (Anna→Anno, Magda→Magdo)
        if low.endswith("a"):
            return title[:-1] + "o"
        return title  # foreign / unknown → nominative

    elif gnd == "male":
        v = _MALE_VOC.get(title)
        if v:
            return v
        low = title.lower()
        # -ek → -ku  (Marek→Marku, Jacek→Jacku)
        if low.endswith("ek"):
            return title[:-2] + "ku"
        # -sz → +u   (Tomasz→Tomaszu, Łukasz→Łukaszu)
        if low.endswith("sz"):
            return title + "u"
        # -rz → +u   (Grzegorz→Grzegorzu)
        if low.endswith("rz"):
            return title + "u"
        # -aw → +ie  (Jarosław→Jarosławie)
        if low.endswith("aw"):
            return title + "ie"
        # -eł → -le  (Paweł→Pawle)
        if low.endswith("eł"):
            return title[:-2] + "le"
        # -ał → -ale (Rafał→Rafale, Michał→Michale)
        if low.endswith("ał"):
            return title[:-2] + "ale"
        # -a → -o    (Kuba→Kubo)
        if low.endswith("a"):
            return title[:-1] + "o"
        # -r → +ze   (Piotr→Piotrze, Igor→Igorze, Artur→Arturze)
        if low.endswith("r"):
            return title + "ze"
        # -n → +ie   (Jan→Janie, Damian→Damianie, Szymon→Szymonie)
        if low.endswith("n"):
            return title + "ie"
        # -l → +u    (Kamil→Kamilu, Karol→Karolu)
        if low.endswith("l"):
            return title + "u"
        # -p → +ie   (Filip→Filipie)
        if low.endswith("p"):
            return title + "ie"
        # -f → +ie   (Krzysztof→Krzysztofie)
        if low.endswith("f"):
            return title + "ie"
        return title  # unknown → nominative

    else:
        return title  # unspecified gender → nominative


def greeting(first_name: str, gender: str) -> str | None:
    """
    Return a time-of-day greeting in Polish, e.g. "Dzień dobry, Anno!",
    or None if *first_name* is empty.

    Time zones: Europe/Warsaw.
    """
    if not first_name or not first_name.strip():
        return None

    hour = datetime.now(WARSAW).hour
    voc = vocative(first_name, gender)

    if 5 <= hour < 12:
        return f"Dzień dobry, {voc}!"
    if 12 <= hour < 18:
        return f"Cześć, {voc}!"
    if 18 <= hour < 22:
        return f"Dobry wieczór, {voc}!"
    return f"Dobranoc, {voc}"
