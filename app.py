from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SQLITE_PATH = DATA_DIR / "krillion.db"


def read_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.casefold(), flags=re.UNICODE)).strip()


def candidate_matches_prompt(answer: str, prompt: str) -> bool:
    """Reject suggestions that violate simple, explicit prompt constraints."""
    text = prompt.casefold()
    candidate = answer.strip().casefold()
    first_letter = re.search(r"\b(?:start(?:s|ing)?|begin(?:s|ning)?)\s+with\s+(?:the\s+)?(?:letter\s+)?['\"]?([a-z])\b", text)
    last_letter = re.search(r"\bend(?:s|ing)?\s+with\s+(?:the\s+)?(?:letter\s+)?['\"]?([a-z])\b", text)
    contains_letter = re.search(r"\bcontains?\s+(?:the\s+)?letter\s+['\"]?([a-z])\b", text)
    if first_letter and not candidate.startswith(first_letter.group(1)):
        return False
    if last_letter and not candidate.endswith(last_letter.group(1)):
        return False
    if contains_letter and contains_letter.group(1) not in candidate:
        return False
    counts = {"one": 1, "1": 1, "two": 2, "2": 2, "three": 3, "3": 3, "four": 4, "4": 4}
    word_range = re.search(r"\b(one|1|two|2|three|3)\s+or\s+(one|1|two|2|three|3)\s+words?\b", text)
    word_count = re.search(r"\b(?:exactly\s+)?(?:(one|1|two|2|three|3|four|4)[- ]words?|single[- ]words?)\b", text)
    answer_word_count = len(candidate.split())
    if word_range and answer_word_count not in {counts[word_range.group(1)], counts[word_range.group(2)]}:
        return False
    if word_count and answer_word_count != (counts[word_count.group(1)] if word_count.group(1) else 1):
        return False
    syllable_rule = re.search(r"\b(?:exactly\s+)?(\d+)\s+syllables?\b", text)
    if syllable_rule and not candidate_syllables_match(candidate, int(syllable_rule.group(1))):
        return False
    if re.search(r"\bcompound\s+words?\b", text) and not looks_compound(candidate):
        return False
    if not country_constraint_matches(answer, prompt):
        return False
    excluded = re.search(r"\b(?:not|excluding|except|without)\s+([a-z][a-z -]{1,35})", text)
    if not excluded:
        excluded = re.search(r"\b([a-z][a-z -]{1,35}?)\s+(?:does not|doesn't|do not|don't)\s+count\b", text)
    if excluded:
        excluded_text = excluded.group(1)
        excluded_terms = {normalize(part) for part in re.split(r",|\bor\b|\band\b", excluded_text)}
        if normalize(candidate) in excluded_terms:
            return False
    return True


CMU_PRONUNCIATIONS = None
CMU_LOAD_ATTEMPTED = False


def candidate_syllables_match(answer: str, expected: int) -> bool:
    """Use CMU pronunciations when installed; avoid guessing if a word is unknown."""
    global CMU_PRONUNCIATIONS, CMU_LOAD_ATTEMPTED
    if not CMU_LOAD_ATTEMPTED:
        CMU_LOAD_ATTEMPTED = True
        try:
            import cmudict
            CMU_PRONUNCIATIONS = cmudict.dict()
        except ImportError:
            CMU_PRONUNCIATIONS = {}
    pronunciations = CMU_PRONUNCIATIONS
    if not pronunciations:
        return True
    counts = []
    for word in re.findall(r"[a-z']+", answer.casefold()):
        entries = pronunciations.get(word)
        if not entries:
            return True
        counts.append(min(sum(phoneme[-1:].isdigit() for phoneme in entry) for entry in entries))
    return bool(counts) and sum(counts) == expected


def looks_compound(answer: str) -> bool:
    normalized = normalize(answer)
    return "-" in answer or normalized in {
        "sunflower", "toothbrush", "rainbow", "notebook", "football", "basketball",
        "bedroom", "airport", "popcorn", "snowman", "butterfly", "firefly", "moonlight",
        "ice cream", "hot dog", "post office", "high school", "living room", "coffee table",
        "fire truck", "parking lot", "swimming pool", "washing machine", "ice cube", "mailbox",
    }


def extract_visible_answer_lines(page_text: str, prompt: str) -> set[str]:
    """Collect short standalone text rows that may be answers already on the game page."""
    visible: set[str] = set()
    skip = re.compile(r"^(?:question(?:\s+\d+)?|prompt|category|round(?:\s+\d+)?|timer|score|players?|submit|start game|settings|leave game|waiting(?: for players)?|your turn)$", re.I)
    for raw_line in page_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" \t•*-0123456789.)\t")
        candidate = line.split(":", 1)[1].strip() if ":" in line else line
        candidate = re.sub(r"\s+(?:accepted|correct|rejected|incorrect|invalid)\s*$", "", candidate, flags=re.I)
        candidate = re.sub(r"^\[(?:accepted|correct|rejected|incorrect|invalid)\]\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"^[\u2713\u2714\u2715\u2716\u274c\s]+", "", candidate)
        candidate = re.sub(r"[\s\u2713\u2714\u2715\u2716\u274c]+$", "", candidate).strip()
        if not candidate or len(candidate) > 90 or normalize(candidate) == normalize(prompt) or skip.search(candidate):
            continue
        if re.match(r"^(?:name|list|give|think of|come up with|say|tell me|identify|which|what|who|where|when|how)\b", candidate, re.I):
            continue
        visible.add(normalize(candidate))
    return {value for value in visible if value}


def detect_question(page_text: str) -> str | None:
    """Find a likely standalone game prompt in visible browser text."""
    direct = re.compile(r"^(?:name|list|give(?: me)?|think of|come up with|say|tell me|identify|which|what|who|where|when|how)\b", re.I)
    rule_prompt = re.compile(r"\b(?:that|which)\s+(?:start|begin|end|contain|include|have|is|are)\b|\b(?:starting|beginning|ending|containing)\s+with\b", re.I)
    category_prompt = re.compile(
        r"^(?:(?:name|list|give)\s+)?(?:a|an|any|some|all)?\s*(?:coastal|seaside)?\s*"
        r"(?:cities|city|countries|country|nations|animals|animal|foods|food|fruits|fruit|vegetables|sports|"
        r"planets|continents|oceans|colors|colours|musical instruments|jobs|occupations|vehicles|plants|gemstones|dinosaurs|board games)\b",
        re.I,
    )
    candidates: list[tuple[int, str]] = []
    for raw_line in page_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" \t•*-–—")
        line = re.sub(r"^(?:question|prompt|category)(?:\s*\d+)?\s*[:：-]\s*", "", line, flags=re.I)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if not 5 <= len(line) <= 220:
            continue
        if not (direct.match(line) or rule_prompt.search(line) or category_prompt.match(line)):
            continue
        # Ignore instructions/help text; these tend to be full explanatory sentences.
        if re.search(r"\b(?:press|click|type|enter|submit|timer|round starts|take turns)\b", line, re.I):
            continue
        score = 3 if direct.match(line) else 1
        if rule_prompt.search(line):
            score += 1
        if line.endswith("?"):
            score += 1
        candidates.append((score, line.rstrip(".!? ")))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def detect_page_question(page, page_text: str) -> tuple[str | None, str]:
    """Check likely prompt containers before falling back to all visible page text."""
    selector = "[data-testid*='prompt' i], [class*='prompt' i], [class*='question' i], h1, h2, h3, [role='heading']"
    try:
        selected_text = "\n".join(page.locator(selector).all_inner_texts())
        prompt = detect_question(selected_text)
        if prompt:
            return prompt, "Prompt or heading elements"
    except Exception:
        pass
    return detect_question(page_text), "Visible page text"


def detect_answer_events(page_text: str) -> list[tuple[str, str, str]]:
    """Extract explicit accepted/rejected answer messages from visible game text."""
    events: list[tuple[str, str, str]] = []
    patterns = (
        (re.compile(r"^(.{1,90}?)\s+(?:was|is)\s+accepted\b", re.I), "accepted", None),
        (re.compile(r"^(.{1,90}?)\s+(?:was|is)\s+rejected\b", re.I), "rejected", None),
        (re.compile(r"^(?:accepted|correct)\s*[:\-]\s*(.{1,90})$", re.I), "accepted", None),
        (re.compile(r"^(?:rejected|incorrect|invalid)\s*[:\-]\s*(.{1,90})$", re.I), "rejected", None),
        (re.compile(r"^(.{1,90}?)\s*[—–-]\s*(?:accepted|correct)$", re.I), "accepted", None),
        (re.compile(r"^(.{1,90}?)\s*[—–-]\s*(?:rejected|incorrect|invalid)$", re.I), "rejected", None),
        (re.compile(r"^(.{1,50}?)\s*:\s*(.{1,70}?)\s*[✓✔]$", re.I), "accepted", "player"),
        (re.compile(r"^(.{1,50}?)\s*:\s*(.{1,70}?)\s*[✕✖❌]$", re.I), "rejected", "player"),
    )
    for raw in page_text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip(" \t•*-–—")
        if not line or len(line) > 140:
            continue
        for pattern, status, _source in patterns:
            match = pattern.match(line)
            if match:
                reporter = None
                if pattern.groups == 2:
                    reporter = match.group(1).strip()
                    answer = match.group(2).strip(" \t:—–-\"'")
                else:
                    answer = match.group(1).strip(" \t:—–-\"'")
                if answer and not re.search(r"\b(?:player|round|answer|turn)\b", answer, re.I):
                    events.append((answer, status, reporter))
                break
    return events


COUNTRIES = [
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda", "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan",
    "Bahamas", "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso", "Burundi",
    "Cabo Verde", "Cambodia", "Cameroon", "Canada", "Central African Republic", "Chad", "Chile", "China", "Colombia", "Comoros", "Costa Rica", "Côte d'Ivoire", "Croatia", "Cuba", "Cyprus", "Czechia",
    "Democratic Republic of the Congo", "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia",
    "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece", "Grenada", "Guatemala", "Guinea", "Guyana", "Haiti", "Honduras", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Jamaica", "Japan", "Jordan",
    "Kazakhstan", "Kenya", "Kiribati", "Kosovo", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar",
    "Namibia", "Nauru", "Nepal", "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria", "North Korea", "North Macedonia", "Norway", "Oman", "Pakistan", "Palau", "Palestine", "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Republic of the Congo", "Romania", "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia", "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe"
]

COUNTRY_ALIASES = {
    "Bahamas": ["The Bahamas"],
    "Cabo Verde": ["Cape Verde"],
    "Côte d'Ivoire": ["Ivory Coast"],
    "Czechia": ["Czech Republic"],
    "Democratic Republic of the Congo": ["DR Congo", "Congo-Kinshasa"],
    "Eswatini": ["Swaziland"],
    "Gambia": ["The Gambia"],
    "Laos": ["Lao People's Democratic Republic"],
    "Micronesia": ["Federated States of Micronesia"],
    "Moldova": ["Republic of Moldova"],
    "Myanmar": ["Burma"],
    "Netherlands": ["Holland"],
    "North Macedonia": ["Macedonia"],
    "Palestine": ["State of Palestine"],
    "Republic of the Congo": ["Congo-Brazzaville"],
    "Russia": ["Russian Federation"],
    "South Korea": ["Republic of Korea", "Korea, South"],
    "Syria": ["Syrian Arab Republic"],
    "Taiwan": ["Republic of China"],
    "Tanzania": ["United Republic of Tanzania"],
    "Timor-Leste": ["East Timor"],
    "Turkey": ["Türkiye", "Republic of Türkiye"],
    "United Kingdom": ["UK", "U.K.", "Great Britain", "Britain"],
    "United States": ["USA", "U.S.A.", "US", "U.S.", "United States of America", "America"],
    "Vatican City": ["Holy See"],
    "Vietnam": ["Viet Nam"],
}

LEXICON = {
    "countries": COUNTRIES,
    "coastal cities": [
        "Alexandria", "Auckland", "Barcelona", "Boston", "Busan", "Cape Town", "Casablanca", "Chennai", "Copenhagen", "Dakar",
        "Dublin", "Durban", "Helsinki", "Honolulu", "Hong Kong", "Lagos", "Lima", "Lisbon", "Los Angeles", "Manila",
        "Marseille", "Miami", "Mumbai", "Mombasa", "Naples", "New York City", "Oslo", "Reykjavik", "Rio de Janeiro",
        "San Francisco", "Seattle", "Shanghai", "Singapore", "Stockholm", "Sydney", "Tokyo", "Valparaiso", "Vancouver", "Venice", "Wellington"
    ],
    "planets": ["Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"],
    "continents": ["Africa", "Antarctica", "Asia", "Europe", "North America", "Oceania", "South America"],
    "oceans": ["Arctic Ocean", "Atlantic Ocean", "Indian Ocean", "Pacific Ocean", "Southern Ocean"],
    "colors": ["Red", "Orange", "Yellow", "Green", "Blue", "Indigo", "Violet", "Black", "White", "Pink", "Brown", "Gray"],
    "months": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "days of the week": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "common animals": ["Dog", "Cat", "Horse", "Cow", "Sheep", "Goat", "Pig", "Lion", "Tiger", "Elephant", "Giraffe", "Zebra", "Dolphin", "Whale", "Eagle", "Penguin", "Shark", "Octopus"],
    "common foods": ["Apple", "Banana", "Bread", "Cheese", "Chocolate", "Pizza", "Rice", "Pasta", "Potato", "Tomato", "Carrot", "Chicken", "Salmon", "Egg", "Yogurt"],
    "fruits": ["Apple", "Apricot", "Banana", "Blackberry", "Blueberry", "Cherry", "Coconut", "Cranberry", "Grape", "Grapefruit", "Guava", "Kiwi", "Lemon", "Lime", "Mango", "Melon", "Nectarine", "Orange", "Papaya", "Peach", "Pear", "Pineapple", "Plum", "Raspberry", "Strawberry", "Watermelon"],
    "vegetables": ["Artichoke", "Asparagus", "Beet", "Bell pepper", "Broccoli", "Cabbage", "Carrot", "Cauliflower", "Celery", "Corn", "Cucumber", "Eggplant", "Garlic", "Kale", "Lettuce", "Mushroom", "Onion", "Pea", "Potato", "Pumpkin", "Radish", "Spinach", "Squash", "Sweet potato", "Tomato", "Zucchini"],
    "sports": ["Archery", "Badminton", "Baseball", "Basketball", "Boxing", "Cricket", "Cycling", "Fencing", "Football", "Golf", "Gymnastics", "Hockey", "Ice hockey", "Lacrosse", "Rugby", "Sailing", "Soccer", "Softball", "Swimming", "Tennis", "Track and field", "Volleyball", "Wrestling"],
    "musical instruments": ["Accordion", "Banjo", "Bass guitar", "Cello", "Clarinet", "Drums", "Flute", "French horn", "Guitar", "Harp", "Harmonica", "Oboe", "Piano", "Saxophone", "Trombone", "Trumpet", "Tuba", "Ukulele", "Viola", "Violin"],
    "occupations": ["Accountant", "Actor", "Architect", "Baker", "Carpenter", "Chef", "Dentist", "Doctor", "Electrician", "Farmer", "Firefighter", "Judge", "Mechanic", "Nurse", "Paramedic", "Pilot", "Plumber", "Police officer", "Teacher", "Writer"],
    "vehicles": ["Ambulance", "Bicycle", "Boat", "Bus", "Car", "Ferry", "Helicopter", "Motorcycle", "Pickup truck", "Sailboat", "School bus", "Ship", "Submarine", "Taxi", "Tractor", "Train", "Truck", "Van"],
    "plants": ["Bamboo", "Cactus", "Daisy", "Fern", "Maple", "Oak", "Orchid", "Pine", "Rose", "Sunflower", "Tulip", "Venus flytrap", "Willow"],
    "gemstones": ["Agate", "Amethyst", "Aquamarine", "Diamond", "Emerald", "Garnet", "Jade", "Moonstone", "Opal", "Pearl", "Peridot", "Ruby", "Sapphire", "Topaz", "Turquoise"],
    "board games": ["Backgammon", "Battleship", "Checkers", "Chess", "Clue", "Connect Four", "Go", "Monopoly", "Othello", "Risk", "Scrabble", "Sorry!", "The Game of Life", "Ticket to Ride"],
    "dinosaurs": ["Allosaurus", "Ankylosaurus", "Apatosaurus", "Brachiosaurus", "Diplodocus", "Iguanodon", "Pachycephalosaurus", "Stegosaurus", "Triceratops", "Tyrannosaurus rex", "Velociraptor"],
    "body parts": ["Ankle", "Arm", "Back", "Brain", "Chest", "Chin", "Ear", "Elbow", "Eye", "Finger", "Foot", "Hand", "Heart", "Knee", "Leg", "Lung", "Mouth", "Nose", "Shoulder", "Toe", "Tongue", "Wrist"],
    "interjections": ["Aha", "Ahoy", "Aww", "Bravo", "Eek", "Hooray", "Hmm", "Oops", "Ouch", "Phew", "Uh-oh", "Whoa", "Wow", "Yikes", "Yippee"],
    "filler words": ["Actually", "Basically", "Like", "Literally", "Perhaps", "Probably", "Really", "Seriously", "So", "Well", "You know"],
}

COASTAL_COUNTRIES = {country.strip() for country in """
Albania|Algeria|Angola|Antigua and Barbuda|Argentina|Australia|Bahamas|Bahrain|Bangladesh|Barbados|Belgium|Belize|Benin|Bosnia and Herzegovina|Brazil|Brunei|Cambodia|Cameroon|Canada|Chile|China|Colombia|Comoros|Costa Rica|Côte d'Ivoire|Croatia|Cuba|Cyprus|Denmark|Djibouti|Dominica|Dominican Republic|Ecuador|Egypt|El Salvador|Equatorial Guinea|Eritrea|Estonia|Fiji|Finland|France|Gabon|Gambia|Georgia|Germany|Ghana|Greece|Grenada|Guatemala|Guinea|Guyana|Haiti|Honduras|Iceland|India|Indonesia|Iran|Iraq|Ireland|Israel|Italy|Jamaica|Japan|Jordan|Kenya|Kiribati|Kuwait|Lebanon|Liberia|Libya|Madagascar|Malaysia|Maldives|Malta|Marshall Islands|Mauritania|Mauritius|Mexico|Micronesia|Monaco|Montenegro|Morocco|Mozambique|Myanmar|Namibia|Nauru|Netherlands|New Zealand|Nicaragua|Nigeria|North Korea|Norway|Oman|Pakistan|Palau|Palestine|Panama|Papua New Guinea|Peru|Philippines|Poland|Portugal|Qatar|Republic of the Congo|Romania|Russia|Samoa|Sao Tome and Principe|Saudi Arabia|Senegal|Sierra Leone|Singapore|Slovenia|Solomon Islands|Somalia|South Africa|South Korea|Spain|Sri Lanka|Sudan|Suriname|Sweden|Syria|Taiwan|Tanzania|Thailand|Timor-Leste|Togo|Tonga|Trinidad and Tobago|Tunisia|Turkey|Tuvalu|Ukraine|United Arab Emirates|United Kingdom|United States|Uruguay|Vanuatu|Venezuela|Vietnam|Yemen
""".split("|") if country.strip()}

COUNTRY_REGIONS = {
    "europe": set("Albania|Andorra|Austria|Belarus|Belgium|Bosnia and Herzegovina|Bulgaria|Croatia|Cyprus|Czechia|Denmark|Estonia|Finland|France|Germany|Greece|Hungary|Iceland|Ireland|Italy|Kosovo|Latvia|Liechtenstein|Lithuania|Luxembourg|Malta|Moldova|Monaco|Montenegro|Netherlands|North Macedonia|Norway|Poland|Portugal|Romania|Russia|San Marino|Serbia|Slovakia|Slovenia|Spain|Sweden|Switzerland|Ukraine|United Kingdom|Vatican City".split("|")),
    "africa": set("Algeria|Angola|Benin|Botswana|Burkina Faso|Burundi|Cabo Verde|Cameroon|Central African Republic|Chad|Comoros|Democratic Republic of the Congo|Republic of the Congo|Côte d'Ivoire|Djibouti|Egypt|Equatorial Guinea|Eritrea|Eswatini|Ethiopia|Gabon|Gambia|Ghana|Guinea|Guinea-Bissau|Kenya|Lesotho|Liberia|Libya|Madagascar|Malawi|Mali|Mauritania|Mauritius|Morocco|Mozambique|Namibia|Niger|Nigeria|Rwanda|Sao Tome and Principe|Senegal|Seychelles|Sierra Leone|Somalia|South Africa|South Sudan|Sudan|Tanzania|Togo|Tunisia|Uganda|Zambia|Zimbabwe".split("|")),
    "asia": set("Afghanistan|Armenia|Azerbaijan|Bahrain|Bangladesh|Bhutan|Brunei|Cambodia|China|Cyprus|Georgia|India|Indonesia|Iran|Iraq|Israel|Japan|Jordan|Kazakhstan|Kuwait|Kyrgyzstan|Laos|Lebanon|Malaysia|Maldives|Mongolia|Myanmar|Nepal|North Korea|Oman|Pakistan|Palestine|Philippines|Qatar|Russia|Saudi Arabia|Singapore|South Korea|Sri Lanka|Syria|Taiwan|Tajikistan|Thailand|Timor-Leste|Turkey|Turkmenistan|United Arab Emirates|Uzbekistan|Vietnam|Yemen".split("|")),
    "north america": set("Antigua and Barbuda|Bahamas|Barbados|Belize|Canada|Costa Rica|Cuba|Dominica|Dominican Republic|El Salvador|Grenada|Guatemala|Haiti|Honduras|Jamaica|Mexico|Nicaragua|Panama|Saint Kitts and Nevis|Saint Lucia|Saint Vincent and the Grenadines|Trinidad and Tobago|United States".split("|")),
    "south america": set("Argentina|Bolivia|Brazil|Chile|Colombia|Ecuador|Guyana|Paraguay|Peru|Suriname|Uruguay|Venezuela".split("|")),
    "oceania": set("Australia|Fiji|Kiribati|Marshall Islands|Micronesia|Nauru|New Zealand|Palau|Papua New Guinea|Samoa|Solomon Islands|Tonga|Tuvalu|Vanuatu".split("|")),
}


def country_constraint_matches(answer: str, prompt: str) -> bool:
    text = normalize(prompt)
    if not re.search(r"\b(?:countries?|nations?)\b", text):
        return True
    normalized = normalize(answer)
    canonical = next((country for country in COUNTRIES if normalize(country) == normalized), None)
    if canonical is None:
        canonical = next((country for country, aliases in COUNTRY_ALIASES.items() if normalized in {normalize(alias) for alias in aliases}), None)
    if canonical is None:
        return not re.search(r"\b(?:coastal|coastline|landlocked|europe|africa|asia|north america|south america|oceania)\b", text)
    if re.search(r"\b(?:coastal|coastline|by sea|on ocean)\b", text) and canonical not in COASTAL_COUNTRIES:
        return False
    if re.search(r"\blandlocked\b", text) and canonical in COASTAL_COUNTRIES:
        return False
    for region, members in COUNTRY_REGIONS.items():
        if re.search(rf"\b{re.escape(region)}\b", text) and canonical not in members:
            return False
    return True


class Store:
    def __init__(self) -> None:
        self.pg = bool(os.environ.get("DATABASE_URL", "").strip())
        self.conn = None
        if self.pg:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("PostgreSQL is configured but psycopg is missing. Run: pip install -r requirements.txt") from exc
            self.conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
        else:
            DATA_DIR.mkdir(exist_ok=True)
            self.conn = sqlite3.connect(SQLITE_PATH)
            self.conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        if self.pg:
            sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
            with self.conn.cursor() as cur:
                cur.execute(sql)
            self.conn.commit()
        else:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, category TEXT NOT NULL,
                    normalized_category TEXT NOT NULL, UNIQUE(mode, normalized_category));
                CREATE TABLE IF NOT EXISTS answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_id INTEGER NOT NULL REFERENCES prompts(id),
                    answer TEXT NOT NULL, normalized_answer TEXT NOT NULL,
                    status TEXT NOT NULL, level TEXT, source TEXT NOT NULL DEFAULT 'player',
                    reporter TEXT, note TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS answers_lookup_idx ON answers(prompt_id, normalized_answer);
                CREATE TABLE IF NOT EXISTS lexicon (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL, answer TEXT NOT NULL,
                    normalized_answer TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'seed',
                    UNIQUE(topic, normalized_answer));
            """)
            self.conn.commit()
        self.seed_lexicon()

    def _execute(self, sql: str, params=()):
        if self.pg:
            cur = self.conn.cursor()
            cur.execute(sql.replace("?", "%s"), params)
            return cur
        return self.conn.execute(sql, params)

    def seed_lexicon(self) -> None:
        for topic, answers in LEXICON.items():
            for answer in answers:
                aliases = "|".join(COUNTRY_ALIASES.get(answer, [])) if topic == "countries" else ""
                self._execute("""INSERT INTO lexicon(topic, answer, normalized_answer, aliases)
                    VALUES (?, ?, ?, ?) ON CONFLICT(topic, normalized_answer)
                    DO UPDATE SET aliases=excluded.aliases""", (topic, answer, normalize(answer), aliases))
        self.conn.commit()

    def ensure_prompt(self, mode: str, category: str) -> int:
        norm = normalize(category)
        self._execute("INSERT INTO prompts(mode, category, normalized_category) VALUES (?, ?, ?) ON CONFLICT DO NOTHING", (mode, category.strip(), norm))
        row = self._execute("SELECT id FROM prompts WHERE mode = ? AND normalized_category = ?", (mode, norm)).fetchone()
        self.conn.commit()
        return row["id"] if isinstance(row, dict) else row[0]

    def lookup(self, mode: str, category: str) -> list[dict]:
        norm = normalize(category)
        rows = self._execute("""SELECT a.answer, a.status, a.level, a.source, a.reporter, a.note, a.created_at
            FROM answers a JOIN prompts p ON p.id=a.prompt_id
            WHERE p.mode=? AND p.normalized_category=? ORDER BY CASE a.status WHEN 'accepted' THEN 0 WHEN 'unverified' THEN 1 ELSE 2 END, a.created_at DESC""", (mode, norm)).fetchall()
        return [dict(r) for r in rows]

    def lexicon_answers(self, category: str) -> list[str]:
        cat = normalize(category)
        topic_map = {normalize(topic): topic for topic in LEXICON}
        topic_map.update({
            "animals": "common animals", "animal": "common animals",
            "food": "common foods", "foods": "common foods",
            "instrument": "musical instruments", "instruments": "musical instruments",
            "occupation": "occupations", "jobs": "occupations", "professions": "occupations",
            "vehicle": "vehicles", "fruit": "fruits", "vegetable": "vegetables",
            "plant": "plants", "sport": "sports", "gemstone": "gemstones",
            "dinosaur": "dinosaurs", "board game": "board games",
            "body part": "body parts", "body parts": "body parts",
        })
        topic = topic_map.get(cat)
        if not topic:
            topic_terms = (
                (r"\b(?:animals?|mammals?|birds?|fish)\b", "common animals"),
                (r"\b(?:foods?|meals?|things to eat)\b", "common foods"),
                (r"\bfruits?\b", "fruits"), (r"\bvegetables?\b", "vegetables"),
                (r"\bsports?\b", "sports"), (r"\bmusical instruments?\b", "musical instruments"),
                (r"\b(?:jobs?|occupations?|professions?)\b", "occupations"),
                (r"\bvehicles?\b", "vehicles"), (r"\bplants?\b", "plants"),
                (r"\bgemstones?\b", "gemstones"), (r"\bdinosaurs?\b", "dinosaurs"),
                (r"\bboard games?\b", "board games"), (r"\bbody parts?\b", "body parts"),
                (r"\binterjections?\b", "interjections"), (r"\bfiller words?\b", "filler words"),
            )
            topic = next((value for pattern, value in topic_terms if re.search(pattern, cat)), None)
        if not topic and re.search(r"\b(?:coastal|seaside)\s+cit(?:y|ies)\b", cat):
            topic = "coastal cities"
        country_prompt = bool(re.search(r"\b(?:countr(?:y|ies)|nations?)\b", cat))
        if not topic and country_prompt:
            topic = "countries"
        if topic:
            rows = self._execute("SELECT answer, aliases FROM lexicon WHERE topic=? ORDER BY answer", (topic,)).fetchall()
            answers: list[str] = []
            country_answers = [row["answer"] if isinstance(row, dict) else row[0] for row in rows]
            if topic == "countries":
                if re.search(r"\b(?:coastal|coastline|by (?:the )?sea|on (?:an? )?ocean)\b", cat):
                    country_answers = [answer for answer in country_answers if answer in COASTAL_COUNTRIES]
                elif re.search(r"\blandlocked\b", cat):
                    country_answers = [answer for answer in country_answers if answer not in COASTAL_COUNTRIES]
                for region, members in COUNTRY_REGIONS.items():
                    if re.search(rf"\b{re.escape(region)}\b", cat):
                        country_answers = [answer for answer in country_answers if answer in members]
            for row in rows:
                answer = row["answer"] if isinstance(row, dict) else row[0]
                aliases = row["aliases"] if isinstance(row, dict) else row[1]
                if answer not in country_answers:
                    continue
                answers.append(answer)
                if aliases:
                    answers.extend(alias for alias in aliases.split("|") if alias)
            return answers
        return []

    def save_report(self, mode: str, category: str, answer: str, status: str, level: str, reporter: str, note: str, source: str = "player") -> None:
        pid = self.ensure_prompt(mode, category)
        existing = self._execute("SELECT id FROM answers WHERE prompt_id=? AND normalized_answer=? AND status=? AND COALESCE(reporter, '')=?",
                                 (pid, normalize(answer), status, reporter.strip())).fetchone()
        if existing:
            return
        self._execute("INSERT INTO answers(prompt_id, answer, normalized_answer, status, level, source, reporter, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (pid, answer.strip(), normalize(answer), status, level.strip() or None, source, reporter.strip() or None, note.strip() or None))
        self.conn.commit()


AI_CACHE: dict[tuple, tuple[float, list[str]]] = {}
AI_CACHE_TTL = 12 * 60 * 60


def clean_ai_answer(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    answer = value.strip().strip("\"'` ")
    lower = answer.casefold()
    if not answer or len(answer) > 90 or len(answer.split()) > 6 or "\n" in answer or "\r" in answer:
        return None
    if re.match(r"^(?:the answer is|answer\s*:|fallback\s*:|here (?:is|are)|for example|i (?:think|would)|a possible answer|uncertain)\b", lower):
        return None
    if re.search(r"\b(?:because|which is|that is|located in|known for|sounds like|would be|is a|is an|are a|are an|i would choose)\b", lower):
        return None
    if any(mark in answer for mark in ("\t", ";", ":", "?", "!", "(", ")", "—", "–")) or ", " in answer or " or " in lower:
        return None
    if re.search(r"\b(?:is|are|was|were)\s+(?:located|situated|coastal|a|an|the)\b", lower):
        return None
    return answer


def ollama_suggest(category: str, known: list[str], count: int = 8, mode: str = "Krillion", on_candidate=None, force: bool = False, cancel_event: threading.Event | None = None, hints: list[str] | None = None) -> list[str]:
    endpoint = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    hints = hints or []
    cache_key = (model, mode, normalize(category), count, tuple(sorted(normalize(x) for x in known)), tuple(sorted(normalize(x) for x in hints)))
    cached = AI_CACHE.get(cache_key)
    if not force and cached and time.time() - cached[0] < AI_CACHE_TTL:
        if on_candidate:
            for index, answer in enumerate(cached[1]):
                on_candidate(answer, index == 0)
        return cached[1]
    count = max(1, min(count, 10))
    answers: list[str] = []
    decoder = json.JSONDecoder()

    def generate_batch(batch_count: int, quick: bool) -> None:
        if cancel_event and cancel_event.is_set():
            return
        prompt = (
            f"For {mode}, answer this category: {category!r}. Return up to {batch_count} answer strings as JSON. "
            "Use only real, independently existing things that clearly fit. Never invent names or use game-related words. "
            "For coastal cities, use real cities that touch a sea, ocean, gulf, or bay. Obey explicit letter, word-count, syllable, and compound-word rules. "
            "Give the most recognizable, low-risk answer first, then valid less common examples. "
            "Omit uncertain answers. Output answer strings only, with no explanation. Local category examples (do not repeat): "
            + ", ".join(hints[:30]) + ". Avoid these answers: "
            + ", ".join((known + answers)[:30])
        )
        schema = {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "string"}, "maxItems": batch_count}}, "required": ["answers"], "additionalProperties": False}
        options = {"temperature": 0.25}
        if quick:
            options["num_predict"] = 40
        payload = json.dumps({
            "model": model, "prompt": prompt, "format": schema, "stream": True,
            "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "10m"),
            "options": options,
        }).encode("utf-8")
        req = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        generated = ""
        def accept(value: object) -> None:
            answer = clean_ai_answer(value)
            if answer and candidate_matches_prompt(answer, category) and normalize(answer) not in {normalize(x) for x in known + answers}:
                answers.append(answer)
                if on_candidate:
                    on_candidate(answer, len(answers) == 1)

        def consume() -> None:
            match = re.search(r'"answers"\s*:\s*\[', generated)
            if not match:
                return
            cursor = match.end()
            while cursor < len(generated):
                while cursor < len(generated) and (generated[cursor].isspace() or generated[cursor] == ","):
                    cursor += 1
                if cursor >= len(generated) or generated[cursor] == "]":
                    return
                try:
                    value, cursor = decoder.raw_decode(generated, cursor)
                except json.JSONDecodeError:
                    return
                before = len(answers)
                accept(value)
                if len(answers) == before:
                    continue

        with urllib.request.urlopen(req, timeout=90) as response:
            stream_finished = threading.Event()

            def close_stream_on_cancel():
                while not stream_finished.wait(0.1):
                    if cancel_event and cancel_event.is_set():
                        response.close()
                        return

            if cancel_event:
                threading.Thread(target=close_stream_on_cancel, daemon=True).start()
            try:
                for raw_line in response:
                    if cancel_event and cancel_event.is_set():
                        return
                    if not raw_line.strip():
                        continue
                    item = json.loads(raw_line.decode("utf-8"))
                    if item.get("error"):
                        raise RuntimeError(item["error"])
                    generated += item.get("response", "")
                    consume()
            except Exception:
                if cancel_event and cancel_event.is_set():
                    return
                raise
            finally:
                stream_finished.set()
        try:
            parsed = json.loads(generated)
            for value in parsed.get("answers", []):
                if len(answers) >= count:
                    break
                accept(value)
        except (json.JSONDecodeError, AttributeError):
            if not answers:
                raise RuntimeError("Ollama did not return the required answer-only JSON")

    # The first short generation gives the UI an early fallback; the second expands it.
    generate_batch(1, quick=True)
    if len(answers) < count and not (cancel_event and cancel_event.is_set()):
        generate_batch(count - len(answers), quick=False)
    if cancel_event and cancel_event.is_set():
        return answers
    AI_CACHE[cache_key] = (time.time(), answers)
    return answers


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Krillion Solver — early version")
        self.geometry("560x460")
        self.minsize(500, 420)
        try:
            self.store = Store()
        except Exception as exc:
            messagebox.showerror("Database connection", str(exc))
            self.destroy()
            return
        self.search_generation = 0
        self.result_answers: dict[int, str] = {}
        self.ai_answers: set[str] = set()
        self.ai_feedback_done: set[str] = set()
        self.ai_feedback_since_refresh = 0
        self.ai_feedback_threshold = 2
        self.ai_cancel_event: threading.Event | None = None
        self.visible_game_answers: set[str] = set()
        self.visible_game_prompt: tuple[str, str] | None = None
        self.browser_thread: threading.Thread | None = None
        self.browser_stop: threading.Event | None = None
        self.diagnostics_window = None
        self.diagnostics_text = None
        self.diagnostics_state = {"URL": "Not connected", "Detection method": "Not running", "Selected prompt": "None", "Browser error": "None", "Extracted page text": ""}
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close_app)

    def _build(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="Krillion Solver", font=("Segoe UI", 16, "bold")).pack(side="left")
        self.always_on_top = tk.BooleanVar(value=False)
        ttk.Checkbutton(header, text="Always on top", variable=self.always_on_top, command=self._toggle_topmost).pack(side="right", padx=(6, 0))
        self.compact_mode = False
        self.view_button = ttk.Button(header, text="Compact", command=self.toggle_compact)
        self.view_button.pack(side="right")
        form = ttk.Frame(root)
        form.pack(fill="x")
        self.form = form
        self.mode_label = ttk.Label(form, text="Mode")
        self.mode_label.grid(row=0, column=0, sticky="w")
        self.mode = tk.StringVar(value="Krillion")
        self.mode_combo = ttk.Combobox(form, textvariable=self.mode, values=["Krillion", "Puffer Party"], state="readonly", width=18)
        self.mode_combo.grid(row=1, column=0, sticky="w", padx=(0, 12))
        self.category_label = ttk.Label(form, text="Category / prompt")
        self.category_label.grid(row=0, column=1, sticky="w")
        self.category = tk.StringVar()
        self.category_entry = ttk.Entry(form, textvariable=self.category)
        self.category_entry.grid(row=1, column=1, sticky="ew", padx=(0, 12))
        self.search_button = ttk.Button(form, text="Search", command=self.search)
        self.search_button.grid(row=1, column=2)
        form.columnconfigure(1, weight=1)
        browser_controls = ttk.Frame(root)
        browser_controls.pack(fill="x", pady=(8, 0))
        self.detect_button = ttk.Button(browser_controls, text="Detect prompt", command=self.toggle_detection)
        self.detect_button.pack(side="left", padx=(0, 8))
        ttk.Button(browser_controls, text="Diagnostics", command=self.show_diagnostics).pack(side="left", padx=(0, 8))
        self.detection_status = tk.StringVar(value="Browser detection is stopped.")
        ttk.Label(browser_controls, textvariable=self.detection_status, foreground="#555").pack(side="left", fill="x", expand=True)
        self.use_ai = tk.BooleanVar(value=False)
        ttk.Checkbutton(root, text="Ollama AI suggestions (optional; unverified)", variable=self.use_ai).pack(anchor="w", pady=(5, 4))
        self.results = tk.Listbox(root, height=5, font=("Segoe UI", 10))
        self.results.pack(fill="both", expand=True, pady=(4, 10))
        self.results.insert("end", "Enter a prompt or click Detect prompt.")
        result_actions = ttk.Frame(root)
        result_actions.pack(fill="x", pady=(0, 6))
        ttk.Button(result_actions, text="Copy", command=self.copy_selected).pack(side="left", padx=(0, 6))
        ttk.Button(result_actions, text="Clear", command=self.clear_results).pack(side="left", padx=(0, 6))
        ttk.Button(result_actions, text="Regenerate", command=self.regenerate).pack(side="right")
        ttk.Button(result_actions, text="✓ Accepted", command=lambda: self.mark_selected("accepted")).pack(side="left", padx=(0, 6))
        ttk.Button(result_actions, text="✕ Rejected", command=lambda: self.mark_selected("rejected")).pack(side="left")
        self.report_expanded = False
        self.report_toggle = ttk.Button(root, text="Add player report  +", command=self.toggle_report)
        self.report_toggle.pack(anchor="w", pady=(2, 0))
        self.report_panel = ttk.Frame(root)
        report = ttk.Frame(self.report_panel)
        report.pack(fill="x", pady=(5, 0))
        self.answer = tk.StringVar()
        ttk.Entry(report, textvariable=self.answer).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.status = tk.StringVar(value="accepted")
        ttk.Combobox(report, textvariable=self.status, values=["accepted", "rejected", "unverified"], state="readonly", width=12).grid(row=0, column=1, padx=(0, 8))
        self.level = tk.StringVar()
        ttk.Entry(report, textvariable=self.level, width=14).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(report, text="Save report", command=self.save).grid(row=0, column=3)
        report.columnconfigure(0, weight=1)
        details = ttk.Frame(self.report_panel)
        details.pack(fill="x", pady=(5, 0))
        self.reporter = tk.StringVar()
        self.note = tk.StringVar()
        ttk.Label(details, text="Reported by").grid(row=0, column=0, sticky="w")
        ttk.Entry(details, textvariable=self.reporter).grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(details, text="Note (optional)").grid(row=0, column=1, sticky="w")
        ttk.Entry(details, textvariable=self.note).grid(row=1, column=1, sticky="ew")
        details.columnconfigure(0, weight=1)
        details.columnconfigure(1, weight=2)
        ttk.Label(self.report_panel, text="Level is optional. Reports are scoped to the selected mode and category.", foreground="#555").pack(anchor="w", pady=(4, 0))
        self.bind("<Return>", lambda _event: self.search())

    def toggle_report(self):
        self.report_expanded = not self.report_expanded
        if self.report_expanded:
            self.report_panel.pack(fill="x", pady=(0, 4))
            self.report_toggle.configure(text="Add player report  −")
        else:
            self.report_panel.pack_forget()
            self.report_toggle.configure(text="Add player report  +")

    def toggle_compact(self):
        self.compact_mode = not self.compact_mode
        if self.compact_mode:
            self.mode_label.grid_remove()
            self.mode_combo.grid_remove()
            self.category_label.grid_configure(column=0)
            self.category_entry.grid_configure(column=0, padx=(0, 8))
            self.search_button.grid_configure(column=1)
            self.form.columnconfigure(0, weight=1)
            self.form.columnconfigure(1, weight=0)
            self.form.columnconfigure(2, weight=0)
            self.report_expanded = False
            self.report_panel.pack_forget()
            self.report_toggle.pack_forget()
            self.results.configure(height=3)
            self.geometry("440x350")
            self.minsize(420, 330)
            self.view_button.configure(text="Full view")
        else:
            self.mode_label.grid()
            self.mode_combo.grid()
            self.category_label.grid_configure(column=1)
            self.category_entry.grid_configure(column=1, padx=(0, 12))
            self.search_button.grid_configure(column=2)
            self.form.columnconfigure(0, weight=0)
            self.form.columnconfigure(1, weight=1)
            self.form.columnconfigure(2, weight=0)
            self.report_toggle.pack(anchor="w", pady=(2, 0), after=self.result_actions)
            self.results.configure(height=5)
            self.geometry("560x460")
            self.minsize(500, 420)
            self.view_button.configure(text="Compact")

    def _toggle_topmost(self):
        self.attributes("-topmost", self.always_on_top.get())

    def show_diagnostics(self):
        if self.diagnostics_window and self.diagnostics_window.winfo_exists():
            self.diagnostics_window.lift()
            return
        window = tk.Toplevel(self)
        window.title("Prompt detection diagnostics")
        window.geometry("680x500")
        self.diagnostics_window = window
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set, state="disabled")
        scrollbar.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        self.diagnostics_text = text
        window.protocol("WM_DELETE_WINDOW", self._close_diagnostics)
        self._render_diagnostics()

    def _close_diagnostics(self):
        if self.diagnostics_window:
            self.diagnostics_window.destroy()
        self.diagnostics_window = None
        self.diagnostics_text = None

    def _update_diagnostics(self, values: dict):
        self.diagnostics_state.update(values)
        self._render_diagnostics()

    def _render_diagnostics(self):
        if not self.diagnostics_text or not self.diagnostics_text.winfo_exists():
            return
        scroll_position = self.diagnostics_text.yview()[0]
        lines = [f"{key}:\n{value}" for key, value in self.diagnostics_state.items()]
        self.diagnostics_text.configure(state="normal")
        self.diagnostics_text.delete("1.0", "end")
        self.diagnostics_text.insert("1.0", "\n\n".join(lines))
        self.diagnostics_text.configure(state="disabled")
        self.diagnostics_text.yview_moveto(scroll_position)

    def start_detection(self):
        if self.browser_thread and self.browser_thread.is_alive():
            return
        self.visible_game_answers.clear()
        self.visible_game_prompt = None
        mode = self.mode.get()
        self.browser_stop = threading.Event()
        self.detection_status.set("Starting a separate Chromium browser…")
        self.detect_button.configure(text="Stop detection")
        self.browser_thread = threading.Thread(target=self._browser_worker, args=(mode, self.browser_stop), daemon=True)
        self.browser_thread.start()

    def toggle_detection(self):
        if self.browser_thread and self.browser_thread.is_alive():
            self.stop_detection()
        else:
            self.start_detection()

    def stop_detection(self):
        if self.browser_stop:
            self.browser_stop.set()
            self.visible_game_answers.clear()
            self.visible_game_prompt = None
            self.detection_status.set("Stopping browser detection…")
            self.detect_button.configure(text="Stopping…")

    def close_app(self):
        if self.ai_cancel_event:
            self.ai_cancel_event.set()
        if self.browser_thread and self.browser_thread.is_alive() and self.browser_stop:
            self.browser_stop.set()
            self.detection_status.set("Closing browser before exit…")
            self.after(2500, self.destroy)
        else:
            self.destroy()

    def _browser_worker(self, mode: str, stop_event: threading.Event):
        try:
            from playwright.sync_api import Error as PlaywrightError, sync_playwright
        except ImportError:
            self.after(0, self._detection_error, "Playwright is not installed. Run: pip install -r requirements.txt, then py -m playwright install chromium")
            return
        profile_dir = DATA_DIR / "browser-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        url = "https://krillion.io/party" if mode == "Puffer Party" else "https://krillion.io/"
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(str(profile_dir), headless=False)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self.after(0, self.detection_status.set, "Watching the game page for a prompt…")
                last_prompt = ""
                missed_prompt_polls = 0
                seen_events: set[tuple[str, str, str]] = set()
                while not stop_event.is_set():
                    try:
                        visible_text = page.locator("body").inner_text(timeout=1200)
                        prompt, detection_method = detect_page_question(page, visible_text)
                        visible_answers = extract_visible_answer_lines(visible_text, prompt or "")
                        self.after(0, self._update_diagnostics, {
                            "URL": page.url,
                            "Detection method": detection_method if prompt else "No prompt pattern matched",
                            "Selected prompt": prompt or "None",
                            "Browser error": "None",
                            "Extracted page text": visible_text[:8000],
                        })
                        if prompt:
                            missed_prompt_polls = 0
                            if prompt != last_prompt:
                                last_prompt = prompt
                                self.after(0, self._prompt_detected, mode, prompt)
                            for answer, status, reporter in detect_answer_events(visible_text):
                                event = (normalize(prompt), normalize(answer), status)
                                if event not in seen_events:
                                    seen_events.add(event)
                                    self.after(0, self._save_detected_answer, mode, prompt, answer, status, reporter)
                            self.after(0, self._update_visible_answers, mode, prompt, visible_answers)
                        else:
                            missed_prompt_polls += 1
                            if missed_prompt_polls >= 4:
                                last_prompt = ""
                        page.wait_for_timeout(500)
                    except PlaywrightError as exc:
                        self.after(0, self._update_diagnostics, {"Browser error": str(exc), "Detection method": "Retrying after a temporary browser read error"})
                        pages = [candidate for candidate in context.pages if not candidate.is_closed()]
                        if pages:
                            page = pages[-1]
                        else:
                            page = context.new_page()
                            page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        stop_event.wait(0.4)
                context.close()
        except Exception as exc:
            self.after(0, self._detection_error, str(exc))
            return
        self.after(0, self.detection_status.set, "Browser detection is stopped.")
        self.after(0, self.detect_button.configure, {"text": "Detect prompt"})

    def _prompt_detected(self, mode: str, prompt: str):
        self.mode.set(mode)
        self.category.set(prompt)
        self.visible_game_answers.clear()
        self.visible_game_prompt = (mode, normalize(prompt))
        self.detection_status.set(f"Detected: {prompt}")
        if not self.compact_mode:
            self.toggle_compact()
        self.search()

    def _update_visible_answers(self, mode: str, prompt: str, visible_answers: set[str]):
        if self.mode.get() != mode or normalize(self.category.get()) != normalize(prompt):
            return
        self.visible_game_prompt = (mode, normalize(prompt))
        self.visible_game_answers.update(visible_answers)
        current = [(index, value) for index, value in self.result_answers.items() if normalize(value) in self.visible_game_answers]
        for index, answer in reversed(current):
            self._remove_result(index)
            self.detection_status.set(f"Already used in game; hiding {answer}")

    def _remove_result(self, index: int):
        answer = self.result_answers.get(index)
        self.results.delete(index)
        self.result_answers = {
            old_index - (old_index > index): value
            for old_index, value in self.result_answers.items()
            if old_index != index
        }
        if answer and normalize(answer) in self.ai_answers:
            self.ai_answers.discard(normalize(answer))

    def _save_detected_answer(self, mode: str, prompt: str, answer: str, status: str, reporter: str | None):
        try:
            self.store.save_report(
                mode, prompt, answer, status, "", reporter or "Game detected", "Detected from visible game feedback", "game_detected",
            )
            if self.mode.get() == mode and normalize(self.category.get()) == normalize(prompt):
                self.search()
        except Exception as exc:
            self.detection_status.set(f"Could not save game feedback: {exc}")

    def _detection_error(self, error: str):
        self._update_diagnostics({"Browser error": error, "Detection method": "Browser error"})
        self.detection_status.set("Browser detection could not start.")
        self.detect_button.configure(text="Detect prompt")
        messagebox.showerror("Browser detection", error)

    def regenerate(self):
        if not self.category.get().strip():
            messagebox.showinfo("Prompt required", "Enter or detect a prompt before regenerating answers.")
            return
        self.search(regenerate=True)

    def search(self, regenerate: bool = False):
        if self.ai_cancel_event:
            self.ai_cancel_event.set()
        self.ai_cancel_event = None
        self.search_generation += 1
        generation = self.search_generation
        previous_answers = list(self.result_answers.values()) if regenerate else []
        category = self.category.get().strip()
        if not category:
            messagebox.showinfo("Category required", "Enter a category or prompt first.")
            return
        if self.browser_thread and self.browser_thread.is_alive():
            self.detection_status.set("Watching the game page; manual prompt entered. Auto-detection remains active.")
        if self.visible_game_prompt != (self.mode.get(), normalize(category)):
            self.visible_game_answers.clear()
            self.visible_game_prompt = None
        known = self.store.lookup(self.mode.get(), category)
        lex = self.store.lexicon_answers(category)
        local_hints = [answer for answer in lex if candidate_matches_prompt(answer, category)]
        self.results.delete(0, "end")
        self.result_answers.clear()
        self.ai_answers.clear()
        shown = set()
        for row in known:
            if row["status"] == "rejected" or normalize(row["answer"]) in self.visible_game_answers:
                continue
            label = f"[{row['status'].upper()}] {row['answer']}" + (f" — level: {row['level']}" if row["level"] else "") + (f" — by {row['reporter']}" if row["reporter"] else "")
            self._add_result(label, row["answer"])
            shown.add(normalize(row["answer"]))
        added_lex = 0
        for answer in lex:
            if candidate_matches_prompt(answer, category) and normalize(answer) not in shown and normalize(answer) not in self.visible_game_answers:
                self._add_result(f"[LEXICON / UNVERIFIED] {answer}", answer)
                shown.add(normalize(answer)); added_lex += 1
        self.results.insert("end", f"--- {len(known)} stored reports; {added_lex} lexicon suggestions ---")
        useful_answers = {
            normalize(row["answer"]) for row in known
            if row["status"] == "accepted" and normalize(row["answer"]) not in self.visible_game_answers
        }
        useful_answers.update(
            normalize(answer) for answer in lex
            if candidate_matches_prompt(answer, category) and normalize(answer) not in self.visible_game_answers
        )
        useful_count = len(useful_answers)
        if regenerate or (self.use_ai.get() and useful_count < 5):
            self.results.insert("end", "Regenerating AI answers…" if regenerate else "AI candidates (unverified) will appear as they arrive…")
            needed = 8 if regenerate else max(1, 8 - useful_count)
            blocked = {normalize(row["answer"]) for row in known if row["status"] != "accepted"}
            prior = list(dict.fromkeys(list(shown) + previous_answers))
            ai_known = prior + [row["answer"] for row in known if normalize(row["answer"]) in blocked] + list(self.visible_game_answers)
            self.ai_cancel_event = threading.Event()
            threading.Thread(target=self._ai_worker, args=(category, ai_known, needed, self.mode.get(), generation, regenerate, self.ai_cancel_event, local_hints), daemon=True).start()
        elif not known and not lex:
            self.results.insert("end", "No stored answers or matching lexicon. Enable Ollama or add a player report.")
        elif self.use_ai.get() and useful_count >= 5:
            self.results.insert("end", "AI skipped: lookup already found at least five accepted or lexicon suggestions.")

    def _add_result(self, label: str, answer: str | None = None):
        index = self.results.size()
        self.results.insert("end", label)
        if answer:
            self.result_answers[index] = answer

    def _selected_answer(self) -> str | None:
        selection = self.results.curselection()
        return self.result_answers.get(selection[0]) if selection else None

    def copy_selected(self):
        answer = self._selected_answer()
        if not answer:
            messagebox.showinfo("Select an answer", "Select an answer row to copy it.")
            return
        self.clipboard_clear()
        self.clipboard_append(answer)

    def clear_results(self):
        if self.ai_cancel_event:
            self.ai_cancel_event.set()
        self.ai_cancel_event = None
        self.search_generation += 1
        self.results.delete(0, "end")
        self.result_answers.clear()
        self.ai_answers.clear()

    def mark_selected(self, status: str):
        selection = self.results.curselection()
        index = selection[0] if selection else None
        answer = self._selected_answer()
        category = self.category.get().strip()
        if not answer or not category:
            messagebox.showinfo("Select an answer", "Select an answer row to record its result.")
            return
        try:
            reporter = self.reporter.get().strip() or "Local player"
            self.store.save_report(self.mode.get(), category, answer, status, self.level.get(), reporter, self.note.get())
            is_ai_answer = normalize(answer) in self.ai_answers
            if is_ai_answer and normalize(answer) not in self.ai_feedback_done:
                self.ai_feedback_done.add(normalize(answer))
                self.ai_feedback_since_refresh += 1
            if index is not None:
                if status == "rejected":
                    self.results.delete(index)
                    self.result_answers = {
                        old_index - (old_index > index): value
                        for old_index, value in self.result_answers.items()
                        if old_index != index
                    }
                    self.ai_answers.discard(normalize(answer))
                else:
                    self.results.delete(index)
                    self.results.insert(index, f"[ACCEPTED] {answer}")
                    self.results.selection_set(index)
            if is_ai_answer and self.ai_feedback_since_refresh >= self.ai_feedback_threshold:
                self.ai_feedback_since_refresh = 0
                self.ai_feedback_threshold = 3 if self.ai_feedback_threshold == 2 else 2
                self.search(regenerate=True)
        except Exception as exc:
            messagebox.showerror("Could not save", str(exc))

    def _ai_worker(self, category: str, known: list[str], count: int, mode: str, generation: int, force: bool = False, cancel_event: threading.Event | None = None, hints: list[str] | None = None):
        try:
            answers = ollama_suggest(
                category, known, count, mode,
                on_candidate=lambda answer, fallback: self.after(0, self._append_ai_candidate, generation, answer, fallback),
                force=force,
                cancel_event=cancel_event,
                hints=hints,
            )
            if not answers and not (cancel_event and cancel_event.is_set()):
                self.after(0, self._show_ai_empty, generation)
        except Exception as exc:
            self.after(0, self._show_ai_error, generation, str(exc))

    def _append_ai_candidate(self, generation: int, answer: str, fallback: bool):
        if generation != self.search_generation:
            return
        if normalize(answer) in self.visible_game_answers or normalize(answer) in {normalize(value) for value in self.result_answers.values()}:
            return
        label = "[AI FALLBACK / UNVERIFIED]" if fallback else "[AI / UNVERIFIED]"
        self._add_result(f"{label} {answer}", answer)
        self.ai_answers.add(normalize(answer))

    def _show_ai_empty(self, generation: int):
        if generation == self.search_generation:
            self.results.insert("end", "No AI candidates this time. Click Regenerate to try again.")

    def _show_ai_error(self, generation: int, error: str):
        if generation == self.search_generation:
            self.results.insert("end", f"[AI unavailable] {error}")

    def save(self):
        category, answer = self.category.get().strip(), self.answer.get().strip()
        if not category or not answer:
            messagebox.showinfo("Missing information", "Enter a category and answer before saving.")
            return
        try:
            self.store.save_report(self.mode.get(), category, answer, self.status.get(), self.level.get(), self.reporter.get(), self.note.get())
            self.answer.set("")
            self.search()
            messagebox.showinfo("Saved", "Player report saved.")
        except Exception as exc:
            messagebox.showerror("Could not save", str(exc))


if __name__ == "__main__":
    read_env()
    app = App()
    if app.winfo_exists():
        app.mainloop()
