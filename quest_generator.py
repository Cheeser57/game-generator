"""
Quest Generator
===============
Generuje serię questów do gry tekstowej na podstawie ogólnego zarysu fabuły
i pliku domain.pddl. Każdy quest to para plików problem.pddl + misc.json.

Wymaga:
    - Ollama z modelem qwen3-coder (ollama pull qwen3-coder)
    - unified_planning[fast-downward]
    - requests

Użycie:
    python quest_generator.py "Młody chłopiec odkrywa, że jego wioska jest
    zagrożona przez starożytne zło. Wyrusza w podróż, by zdobyć artefakt
    mogący ocalić wszystkich." domain.pddl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import requests

# ─────────────────────────── unified_planning imports ────────────────────────
import unified_planning as up
from unified_planning.io import PDDLReader
from unified_planning.engines import UPSequentialSimulator  # noqa: F401  (verify import)
from unified_planning.shortcuts import OneshotPlanner

# ══════════════════════════════════════════════════════════════════════════════
#  KONFIGURACJA
# ══════════════════════════════════════════════════════════════════════════════

OLLAMA_URL   = "http://localhost:11434/api/generate"
MODEL        = "qwen3-coder"  #"qwen3:8b"
NUM_QUESTS   = 10          # ile questów wygenerować
MAX_RETRIES  = 10         # ile razy próbować naprawić quest
THINK_BUDGET = 8192       # tokeny na "myślenie" (/no_think wyłącza CoT)
OUTPUT_DIR   = Path("quests")
DEBUG        = False          # włącz flagą --debug


# ══════════════════════════════════════════════════════════════════════════════
#  KOLORY ANSI
# ══════════════════════════════════════════════════════════════════════════════

class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    # prompt wysłany do modelu
    PROMPT  = "\033[38;5;39m"   # niebieski
    # surowa odpowiedź modelu
    RAW     = "\033[38;5;220m"  # złoty
    # wyciągnięty PDDL
    PDDL    = "\033[38;5;114m"  # zielony
    # wyciągnięty JSON
    JSON    = "\033[38;5;208m"  # pomarańczowy
    # błędy
    ERR     = "\033[38;5;196m"  # czerwony
    # nagłówki sekcji
    HDR     = "\033[38;5;135m"  # fioletowy


def dbg_section(label: str) -> None:
    if not DEBUG:
        return
    bar = "─" * 60
    print(f"\n{C.HDR}{C.BOLD}{bar}")
    print(f"  🔍 DEBUG: {label}")
    print(f"{bar}{C.RESET}")


def dbg_prompt(prompt: str, system: str = "") -> None:
    if not DEBUG:
        return
    # if system:
    #     print(f"{C.DIM}{C.PROMPT}[SYSTEM]\n{system}{C.RESET}")
    # print(f"{C.PROMPT}[PROMPT]\n{prompt}{C.RESET}")
    return


def dbg_raw(raw: str) -> None:
    if not DEBUG:
        return
    print(f"{C.RAW}[RAW RESPONSE – {len(raw)} znaków]\n{raw}{C.RESET}")


def dbg_pddl(pddl: str) -> None:
    if not DEBUG:
        return
    print(f"{C.PDDL}[PDDL]\n{pddl}{C.RESET}")


def dbg_json(data: Any) -> None:
    if not DEBUG:
        return
    print(f"{C.JSON}[JSON]\n{json.dumps(data, ensure_ascii=False, indent=2)}{C.RESET}")


def dbg_err(msg: str) -> None:
    if not DEBUG:
        return
    print(f"{C.ERR}[ERROR] {msg}{C.RESET}")


# ══════════════════════════════════════════════════════════════════════════════
#  OLLAMA HELPER
# ══════════════════════════════════════════════════════════════════════════════

def ollama(prompt: str, system: str = "", temperature: float = 0.7,
           max_tokens: int = 4096) -> str:
    """Wysyła prompt do lokalnego modelu Ollama i zwraca odpowiedź."""
    payload: dict[str, Any] = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    }
    if system:
        payload["system"] = system

    dbg_prompt(prompt, system)
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        dbg_raw(raw)
        return raw
    except requests.exceptions.ConnectionError:
        print("\n[BŁĄD] Nie można połączyć się z Ollama. "
              "Upewnij się, że serwer działa (ollama serve).")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
#  PARSERY JSON / PDDL z ODPOWIEDZI LLM
# ══════════════════════════════════════════════════════════════════════════════

def extract_json(text: str) -> Any:
    """Wyciąga pierwszy blok JSON (```json … ``` lub czysty JSON)."""
    # Blok fenced
    m = re.search(r"```json\s*([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        return json.loads(candidate)
    # Czysty JSON – spróbuj od pierwszego nawiasu otwierającego
    decoder = json.JSONDecoder()
    starts = [idx for idx in (text.find("{"), text.find("[")) if idx != -1]
    for start in sorted(starts):
        try:
            obj, _ = decoder.raw_decode(text[start:])
            return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("Brak bloku JSON w odpowiedzi.")


def extract_pddl(text: str, tag: str = "pddl") -> str:
    """Wyciąga blok PDDL oznaczony jako ```pddl … ``` lub ```lisp … ```."""
    for pattern in [
        rf"```{tag}\s*([\s\S]*?)```",
        r"```lisp\s*([\s\S]*?)```",
        r"```\s*([\s\S]*?)```",
        r"(\(define[\s\S]*?\)\s*\))",   # surowy PDDL
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    start = text.lower().find("(define")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            ch = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1].strip()
    raise ValueError("Brak bloku PDDL w odpowiedzi.")


def normalize_world_bible(data: Any) -> dict[str, Any]:
    """Normalizuje odpowiedź modelu do słownika biblii świata.

    Modele czasem zwracają listę zamiast pojedynczego obiektu JSON. W takiej
    sytuacji scalamy słowniki z listy płytko, preferując późniejsze wpisy.
    """
    if isinstance(data, dict):
        return data

    if isinstance(data, list):
        merged: dict[str, Any] = {}
        for item in data:
            if isinstance(item, dict):
                merged.update(item)
        if merged:
            return merged

    raise TypeError(f"Niepoprawny format biblii świata: {type(data).__name__}")


def normalize_mapping(data: Any) -> dict[str, Any]:
    """Normalizuje zagnieżdżoną strukturę do słownika, jeśli model zwróci listę."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        merged: dict[str, Any] = {}
        for item in data:
            if isinstance(item, dict):
                merged.update(item)
        if merged:
            return merged
    return {}


# ══════════════════════════════════════════════════════════════════════════════
#  WALIDACJA PDDL + FAST-DOWNWARD
# ══════════════════════════════════════════════════════════════════════════════

def validate_and_plan(domain_path: str, problem_pddl: str,
                      tmp_dir: Path) -> tuple[bool, str, list[str]]:
    """
    Zapisuje problem_pddl do pliku tymczasowego, parsuje i planuje.
    Zwraca (ok, komunikat, lista kroków planu jako str).
    """
    prob_file = tmp_dir / "_tmp_problem.pddl"
    prob_file.write_text(problem_pddl, encoding="utf-8")

    try:
        reader = PDDLReader()
        problem = reader.parse_problem(str(domain_path), str(prob_file))
    except Exception as e:
        return False, f"Błąd parsowania PDDL: {e}", []

    try:
        with OneshotPlanner(name="fast-downward") as planner:
            result = planner.solve(problem)
    except Exception as e:
        return False, f"Błąd solvera: {e}", []

    if result.plan is None:
        return False, "Solver nie znalazł planu (quest niewykonalny).", []

    steps = [str(a) for a in result.plan.actions]
    return True, "OK", steps


def validate_misc_keys(problem_pddl: str, misc_data: dict) -> tuple[bool, str]:
    """
    Sprawdza czy klucze w misc.json odpowiadają nazwom obiektów w problem.pddl.
    Zwraca (ok, komunikat_bledu).
    """
    import re as _re

    # Wyciągnij obiekty z sekcji :objects
    objects_block = _re.search(r":objects(.*?)\(:", problem_pddl, _re.DOTALL | _re.IGNORECASE)
    if not objects_block:
        return True, "OK"  # nie można sprawdzić, przepuść

    block = objects_block.group(1)

    def extract_typed(text: str, type_name: str) -> set:
        """
        Wyciąga wszystkie obiekty danego typu z bloku :objects.
        Obsługuje wiele obiektów na jednej linii: 'a b c - room'.
        Analizuje linia po linii, żeby nie "wciągać" tokenów z innych linii.
        """
        result = set()
        for line in text.splitlines():
            m = _re.search(
                r"^(.*?)\s+-\s+" + type_name + r"\s*$",
                line.strip(),
                _re.IGNORECASE,
            )
            if m:
                result.update(m.group(1).split())
        return result

    rooms      = extract_typed(block, "room")
    items      = extract_typed(block, "item")
    characters = extract_typed(block, "character")

    misc_locs  = set(misc_data.get("locations",  {}).keys())
    misc_items = set(misc_data.get("items",       {}).keys())
    misc_chars = set(misc_data.get("characters",  {}).keys())

    errors = []

    missing_locs  = rooms      - misc_locs
    missing_items = items      - misc_items
    missing_chars = characters - misc_chars
    extra_locs    = misc_locs  - rooms
    extra_items   = misc_items - items
    extra_chars   = misc_chars - characters

    if missing_locs:
        errors.append(f"Brakuje lokacji w misc.json: {missing_locs}")
    if extra_locs:
        errors.append(f"Nadmiarowe lokacje w misc.json (nie ma ich w pddl): {extra_locs}")
    if missing_items:
        errors.append(f"Brakuje przedmiotow w misc.json: {missing_items}")
    if extra_items:
        errors.append(f"Nadmiarowe przedmioty w misc.json: {extra_items}")
    if missing_chars:
        errors.append(f"Brakuje postaci w misc.json: {missing_chars}")
    if extra_chars:
        errors.append(f"Nadmiarowe postacie w misc.json: {extra_chars}")

    if errors:
        return False, " | ".join(errors)
    return True, "OK"



# ══════════════════════════════════════════════════════════════════════════════
#  KROK 1 – GENEROWANIE LISTY QUESTÓW (ogólny plan fabuły)
# ══════════════════════════════════════════════════════════════════════════════

QUEST_LIST_SYSTEM = textwrap.dedent("""\
    You are a creative narrative designer for text adventure games.
    You write ONLY in English.
    You respond ONLY in JSON, with no text before or after.
    Do NOT use <think> tags or any internal reasoning - only clean JSON.
""")

QUEST_LIST_PROMPT = textwrap.dedent("""\
    Story outline:
    {story}

    Quest length hint:
    {target_actions}

    PDDL domain (available actions and predicates):
    {domain}

        Design exactly {n} quests that form a complete story based on the monomyth
        structure (Call to Adventure -> Trials -> Transformation -> Return).

        Requirements:
        - Each quest has a clear START and END action (names from the PDDL domain).
        - Consecutive quests must have a logical flow (the end of one leads into the next).
        - Prefer unique locations for each quest so every quest feels distinct.
        - Reuse locations only when it clearly improves continuity or the story.
        - The quest chain should create a narrative arc: exposition, rising tension, climax, falling tension, resolution.
        - If a quest length hint is provided, treat it as a soft target only.
        - Do not force the exact number of actions if the story needs a shorter or longer quest.

        Return JSON in the following format:
    ```json
    [
      {{
        "id": 1,
                "title": "Quest title",
                "phase": "Monomyth phase",
                "summary": "Short summary (2-3 sentences)",
                "start_action": "PDDL action name",
                "end_action": "PDDL action name",
                "key_objects": ["object1", "object2"],
                "narrative_goal": "What the player should achieve narratively"
      }}
    ]
    ```
""")


def generate_quest_list(story: str, domain_text: str, target_actions: int | None = None) -> list[dict]:
    """Generuje listę {NUM_QUESTS} questów z LLM."""
    print(f"\n{'='*60}")
    print("KROK 1: Generowanie listy questów...")
    print('='*60)

    length_hint = (
        f"Aim for roughly {target_actions} player actions per quest." if target_actions else ""
    )

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  Próba {attempt}/{MAX_RETRIES}...", end=" ", flush=True)
        raw = ollama(
            QUEST_LIST_PROMPT.format(
                story=story, domain=domain_text, n=NUM_QUESTS, target_actions=length_hint
            ),
            system=QUEST_LIST_SYSTEM,
            temperature=0.8,
        )
        try:
            quests = extract_json(raw)
            assert isinstance(quests, list), "Oczekiwano listy."
            assert len(quests) >= NUM_QUESTS, \
                f"Za mało questów: {len(quests)} < {NUM_QUESTS}"
            print(f"OK – wygenerowano {len(quests)} questów.")
            return quests[:NUM_QUESTS]
        except Exception as e:
            print(f"BŁĄD: {e}")

    raise RuntimeError("Nie udało się wygenerować listy questów.")


# ══════════════════════════════════════════════════════════════════════════════
#  KROK 2 – ROZWINIĘCIE QUESTA → problem.pddl + misc.json
# ══════════════════════════════════════════════════════════════════════════════

QUEST_EXPAND_SYSTEM = textwrap.dedent("""\
    You are an expert in PDDL planning and text adventure design.
    You write ONLY in English (descriptions, dialogue, names).
    You respond ONLY with code blocks, without any text outside them.
    Do NOT use <think> tags or any internal reasoning.
""")

QUEST_EXPAND_PROMPT = textwrap.dedent("""\
    PDDL domain:
    @DOMAIN@

    Quest to expand:
    @QUEST_JSON@

    Narrative context from previous quests:
    @CONTEXT@

    Suggested quest length:
    @TARGET_ACTIONS@

    READY-MADE OBJECTS FOR THIS QUEST (use EXACTLY these names in :objects and misc.json):
    @OBJECTS_BLOCK@

    Generate:

    1) A problem.pddl file for this domain that implements this quest.
            - Use ONLY types and actions from the domain.
            - In :objects declare ONLY:
                hero - player
                [rooms from the READY-MADE OBJECTS list] - room
                [items from the READY-MADE OBJECTS list] - item
                [characters from the READY-MADE OBJECTS list] - character
                        - Connect locations with (connected ...) predicates so the player can move.
                        - Prefer bi-directional room connections to avoid hard-locks.
                        - If you use unlock-path, define exactly one quest-specific item with
                            (path-key <item>) in :init so the problem chooses which item can unlock it.
                        - The initial state (:init) should logically reflect the start of the quest.
                        - The goal (:goal) must be reachable by the fast-downward solver.
                        - The goal may be ANY reachable state or reachable conjunction of facts.
                        - Prefer narrative goals such as (at hero <room>), (has hero <item>),
                            (inspected <room>), (talked-to <character>), (given <character> <item>), 
                            or (defeated <character>).

    2) A misc.json file - use EXACTLY the same keys as in the READY-MADE OBJECTS.
            Copy the provided names and descriptions into the locations/items/characters sections.
            Add an "action_text" section with templates for every action you use.
            Include templates for the actions you actually use in this quest.
            If you use a parameterized action, define its template clearly.

             Example action_text format:
             ```json
             {
                 "action_text": {
                     "move":    "I move from ${from} to ${to}.",
                     "take":    "I pick up ${item}.",
                     "drop":    "I drop ${item} in ${r}.",
                     "talk":    "I talk to ${character}.",
                     "attack":  "I attack ${character} with ${weapon}.",
                     "give":    "I give ${item} to ${character}.",
                     "inspect": "I inspect ${r}.",
                     "unlock-path": "I unlock a new path from ${from} to ${to} with ${item}."
                 }
             }
             ```

            For inspect and talk actions, include short response text in the room
            descriptions and character dialogue fields so the engine can print them.

        CRITICAL: Technical names in :objects and keys in misc.json must be
        IDENTICAL to the identifiers from the READY-MADE OBJECTS list. Do not invent new ones.

        Respond ONLY with two code blocks:

    ```pddl
    (define (problem ...)
      ...
    )
    ```

    ```json
    {{ ... }}
    ```
""")

QUEST_FIX_PROMPT = textwrap.dedent("""\
    The previous quest version did NOT pass validation.

    ERROR TO FIX: @ERROR@
    Plan (if the solver found anything): @PLAN@

    ── Previous PDDL version (to fix) ───────────────────────────────────────────
    @PREV_PDDL@
    ─────────────────────────────────────────────────────────────────────────────

    ── Quest metadata (narrative context only, NOT misc.json format) ───────────
    @QUEST_JSON@
    ─────────────────────────────────────────────────────────────────────────────

    PDDL domain:
    @DOMAIN@

    READY-MADE OBJECTS (use EXACTLY these names in :objects and misc.json):
    @OBJECTS_BLOCK@

    ── Fix problem.pddl ─────────────────────────────────────────────────────────
    1. :objects - ONLY the objects from READY-MADE OBJECTS plus "hero - player". No others.
        2. :init - place the hero in the first location, connect locations with (connected ...),
            preferably in both directions, place items with (item-at) and NPCs with (npc-at).
                Do not write any (not ...) predicate. If unlock-path is used, add exactly one
                (path-key <item>) fact for the item that should unlock the route.
    3. :goal - must contain predicates REACHABLE through the domain actions from this :init.
            Think through the action path from :init to every predicate in :goal.
            The goal may be any reachable state, not just a final win predicate.
            Prefer a goal that matches the quest's narrative endpoint.

    ── Fix misc.json ────────────────────────────────────────────────────────────
    misc.json is a DICTIONARY OF GAME OBJECT DESCRIPTIONS - its structure is strictly defined.
    Do NOT put quest metadata fields here (id, title, phase, summary, etc.)!

    Required misc.json format:
    {{
    "locations": {{
            "<key_from_READY-MADE_OBJECTS>": "name - location description.",
            ... (one entry for each location from READY-MADE OBJECTS)
    }},
    "items": {{
            "<key_from_READY-MADE_OBJECTS>": "name - item description.",
            ... (one entry for each item from READY-MADE OBJECTS)
    }},
    "characters": {{
            "<key_from_READY-MADE_OBJECTS>": "name - character description.",
            ... (one entry for each character from READY-MADE OBJECTS)
    }},
    "action_text": {{
                "move":    "I move from ${{from}} to ${{to}}.",
                "take":    "I pick up ${{item}}.",
                "drop":    "I drop ${{item}} in ${{r}}.",
                "talk":    "I talk to ${{character}}.",
                "attack":  "I attack ${{character}} with ${{weapon}}.",
                "give":    "I give ${{item}} to ${{character}}.",
                "inspect": "I inspect ${{r}}.",
                "unlock-path": "I unlock a new path from ${{from}} to ${{to}} with ${{item}}."
      }}
    }}

        For inspect and talk actions, include short response text in the room
        descriptions and character dialogue fields so the engine can print them.

        Respond ONLY with two code blocks (with no extra text):

    ```pddl
    ...
    ```

    ```json
    {{ ... }}
    ```
""")


# ══════════════════════════════════════════════════════════════════════════════
#  KROK 1.5 – WORLD BIBLE (spójne obiekty dla wszystkich questów)
# ══════════════════════════════════════════════════════════════════════════════

WORLD_BIBLE_SYSTEM = textwrap.dedent("""\
    You are a text adventure world designer.
    Technical identifiers (JSON keys and PDDL names) must be written
    only in lowercase ASCII letters with no spaces (underscores are OK, e.g. "old_well").
    You respond ONLY in JSON, with no text before or after.
    Do NOT use <think> tags or any internal reasoning.
""")

WORLD_BIBLE_PROMPT = textwrap.dedent("""\
        Story outline:
    @STORY@

        Quest list:
    @QUEST_LIST@

        PDDL domain (available object types: player, room, item, character):
    @DOMAIN@

        Create a "world bible" - a complete, coherent dictionary of all objects
        that will appear in the whole game. Rules:

        TECHNICAL IDENTIFIERS (JSON keys and PDDL names):
            - Only lowercase ASCII letters and underscores, e.g. "old_ranch", "deputy_tom"
            - No spaces, no hyphens
            - Once assigned, an identifier never changes

        EACH LOCATION has: name, description (1-2 sentences), inspect_text (short)
        EACH ITEM has: name, description (1 sentence)
        EACH CHARACTER has: name, description, dialogue.default, dialogue.after_key

        QUEST ASSIGNMENT (quest_objects):
            - Each quest must have: at least 2 locations connected to each other, at least 1 item, at least 1 character
            - Prefer assigning unique locations & characters to each quest so quests do not feel visually identical.
            - Reuse locations only if the story clearly benefits from continuity.
            - Objects may be shared between quests
            - quest_objects keys are strings: "1", "2", "3"...

        OUTPUT GUIDELINES:
            - Keep action_text concise and free of extra story text.
            - Put inspect responses in each location's inspect_text field.
            - Put talk responses in character dialogue.default.

        Return JSON in the following format:
    ```json
    {{
      "locations": {{
                "old_ranch": {{"name": "Old Ranch", "description": "An abandoned family ranch..."}},
                "dusty_saloon": {{"name": "Dusty Saloon", "description": "The only bar in town..."}}
      }},
      "items": {{
                "golden_badge": {{"name": "Golden Badge", "description": "The sheriff's badge."}}
      }},
      "characters": {{
        "old_deputy": {{
                    "name": "Old Deputy",
                    "description": "A tired lawman with a past.",
          "dialogue": {{
                        "default": "What are you doing here, boy?",
                        "after_key": "You have the sheriff's badge... that changes things."
          }}
        }}
      }},
      "quest_objects": {{
        "1": {{"rooms": ["old_ranch", "dusty_saloon"], "items": ["golden_badge"], "characters": ["old_deputy"]}},
        "2": {{"rooms": ["dusty_saloon", "jail"], "items": ["wanted_poster"], "characters": ["old_deputy"]}}
      }}
    }}
    ```
""")


def generate_world_bible(story: str, quest_list: list[dict],
                         domain_text: str) -> dict:
    """Generuje spójny słownik wszystkich obiektów świata gry."""
    print(f"\n{'='*60}")
    print("KROK 1.5: Generowanie biblii świata...")
    print('='*60)

    quest_summary = "\n".join(
        f"  Quest {q['id']}: {q['title']} – {q.get('summary', '')}"
        for q in quest_list
    )

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  Próba {attempt}/{MAX_RETRIES}...", end=" ", flush=True)
        prompt = WORLD_BIBLE_PROMPT.replace("@STORY@", story)
        prompt = prompt.replace("@QUEST_LIST@", quest_summary)
        prompt = prompt.replace("@DOMAIN@", domain_text)
        raw = ollama(
            prompt,
            system=WORLD_BIBLE_SYSTEM,
            temperature=0.4,
            max_tokens=8000,
        )
        try:
            bible = normalize_world_bible(extract_json(raw))
            assert "locations"     in bible, "Brak sekcji locations"
            assert "items"         in bible, "Brak sekcji items"
            assert "characters"    in bible, "Brak sekcji characters"
            bible["quest_objects"] = normalize_mapping(bible.get("quest_objects"))
            assert bible["quest_objects"], "Brak sekcji quest_objects"
            assert len(bible["locations"])  >= 2, "Za mało lokacji"
            assert len(bible["items"])      >= 1, "Za mało przedmiotów"
            assert len(bible["characters"]) >= 1, "Za mało postaci"
            # Sprawdź że identyfikatory są poprawne (ascii lowercase + _)
            import re as _re
            bad = [k for section in ("locations","items","characters")
                   for k in bible[section]
                   if not _re.fullmatch(r'[a-z0-9_]+', k)]
            if bad:
                raise ValueError(f"Niepoprawne identyfikatory: {bad}")
            print(f"OK – "
                  f"{len(bible['locations'])} lokacji, "
                  f"{len(bible['items'])} przedmiotów, "
                  f"{len(bible['characters'])} postaci")
            return bible
        except Exception as e:
            print(f"BŁĄD: {e}")

    raise RuntimeError("Nie udało się wygenerować biblii świata.")


def bible_slice_for_quest(bible: dict, quest_id: int) -> tuple[dict, dict]:
    """
    Wyciąga z biblii tylko obiekty przypisane do danego questa.
    Zwraca (misc_slice, pddl_objects).
    """
    bible = normalize_world_bible(bible)
    bible["quest_objects"] = normalize_mapping(bible.get("quest_objects"))
    qkey = str(quest_id)
    quest_objs = bible.get("quest_objects", {}).get(qkey, {})

    rooms      = quest_objs.get("rooms",      [])
    items      = quest_objs.get("items",      [])
    characters = quest_objs.get("characters", [])

    # Fallback jeśli LLM nie przypisał obiektów do questa
    if not rooms:
        rooms = list(bible["locations"].keys())[:3]
    if not items:
        items = list(bible["items"].keys())[:2]
    if not characters:
        characters = list(bible["characters"].keys())[:1]

    # Filtruj tylko istniejące klucze
    rooms      = [r for r in rooms      if r in bible["locations"]]
    items      = [i for i in items      if i in bible["items"]]
    characters = [c for c in characters if c in bible["characters"]]

    misc_slice = {
        "locations":  {k: bible["locations"][k]  for k in rooms},
        "items":      {k: bible["items"][k]       for k in items},
        "characters": {k: bible["characters"][k]  for k in characters},
        "action_text": {
            "move":    "Idź do {to}",
            "take":    "Podnieś {item}",
            "talk":    "Porozmawiaj z {c}",
            "unlock-path": "Otwórz nową ścieżkę z {from} do {to} za pomocą {item}",
        },
    }

    pddl_objects = {
        "rooms": rooms,
        "items": items,
        "characters": characters,
    }

    return misc_slice, pddl_objects


def _format_objects_block(misc_slice: dict | None,
                          pddl_objects: dict | None) -> str:
    """Formatuje gotowe obiekty jako czytelny blok tekstowy dla promptu."""
    if not misc_slice or not pddl_objects:
        return "(brak – wygeneruj obiekty samodzielnie)"
    lines = []
    lines.append("Lokacje (room):")
    for k in pddl_objects.get("rooms", []):
        info = misc_slice["locations"].get(k, {})
        lines.append(f'  {k}: "{info.get("name","?")} – {info.get("description","")}')
    lines.append("Przedmioty (item):")
    for k in pddl_objects.get("items", []):
        info = misc_slice["items"].get(k, {})
        lines.append(f'  {k}: "{info.get("name","?")} – {info.get("description","")}')
    lines.append("Postacie (character):")
    for k in pddl_objects.get("characters", []):
        info = misc_slice["characters"].get(k, {})
        lines.append(f'  {k}: "{info.get("name","?")} – {info.get("description","")}')
    return "\n".join(lines)


def generate_quest_files(
    quest: dict,
    domain_text: str,
    context: str,
    domain_path: str,
    tmp_dir: Path,
    misc_slice: dict | None = None,
    pddl_objects: dict | None = None,
    target_actions: int | None = None,
) -> tuple[str, dict]:
    """
    Generuje i waliduje problem.pddl + misc.json dla jednego questa.
    misc_slice/pddl_objects – gotowe obiekty z biblii świata.
    Zwraca (pddl_text, misc_dict) po pomyślnej walidacji.
    Rzuca RuntimeError jeśli po MAX_RETRIES nie udało się naprawić.
    """
    quest_json = json.dumps(quest, ensure_ascii=False, indent=2)
    objects_block = _format_objects_block(misc_slice, pddl_objects)
    target_actions_text = (
        f"Aim for roughly {target_actions} player actions for this quest."
        if target_actions else ""
    )

    # Pierwsze generowanie
    dbg_section(f"Quest {quest.get('id','?')} – pierwsze generowanie")
    prompt = QUEST_EXPAND_PROMPT.replace("@DOMAIN@", domain_text)
    prompt = prompt.replace("@QUEST_JSON@", quest_json)
    prompt = prompt.replace("@CONTEXT@", context)
    prompt = prompt.replace("@TARGET_ACTIONS@", target_actions_text)
    prompt = prompt.replace("@OBJECTS_BLOCK@", objects_block)
    raw = ollama(
        prompt,
        system=QUEST_EXPAND_SYSTEM,
        temperature=0.4,
    )
    error_msg = "nieznany blad"
    plan_steps: list[str] = []
    prev_pddl = "(brak – pierwsze generowanie nie zwróciło PDDL)"

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"    Walidacja (proba {attempt}/{MAX_RETRIES})...", end=" ", flush=True)

        # Parsowanie odpowiedzi
        try:
            pddl_text = extract_pddl(raw)
            misc_data = extract_json(raw)
            prev_pddl = pddl_text  # zapamiętaj do ewentualnego retry
            dbg_pddl(pddl_text)
            dbg_json(misc_data)
        except ValueError as e:
            error_msg = f"Blad parsowania odpowiedzi LLM: {e}"
            plan_steps = []
            print(f"BLAD PARSOWANIA: {e}")
            dbg_err(str(e))
        else:
            # Walidacja PDDL + solver
            ok, msg, plan_steps = validate_and_plan(domain_path, pddl_text, tmp_dir)

            if ok:
                # Walidacja spojnosci kluczy misc.json z obiektami pddl
                keys_ok, keys_err = validate_misc_keys(pddl_text, misc_data)
                if not keys_ok:
                    error_msg = f"Niespojnosc kluczy misc.json z pddl: {keys_err}"
                    plan_steps = []
                    print(f"BLAD KLUCZY: {keys_err}")
                    dbg_err(keys_err)
                else:
                    start_action = quest.get("start_action") or ""
                    start_ok = (
                        any(start_action in s for s in plan_steps)
                        if start_action else len(plan_steps) > 0
                    )
                    preview = ", ".join(plan_steps[:3])
                    suffix  = "..." if len(plan_steps) > 3 else ""
                    print(f"OK - plan: {len(plan_steps)} krokow ({preview}{suffix})")
                    return pddl_text, misc_data

            error_msg = msg
            plan_steps = []
            print(f"BLAD SOLVERA: {msg}")
            dbg_err(msg)

        if attempt < MAX_RETRIES:
            # Prosimy LLM o naprawe
            plan_str = ", ".join(plan_steps) if plan_steps else "brak"
            dbg_section(f"Quest {quest.get('id','?')} – retry {attempt}")
            prompt = QUEST_FIX_PROMPT.replace("@ERROR@", error_msg)
            prompt = prompt.replace("@PLAN@", plan_str)
            prompt = prompt.replace("@PREV_PDDL@", prev_pddl)
            prompt = prompt.replace("@QUEST_JSON@", quest_json)
            prompt = prompt.replace("@DOMAIN@", domain_text)
            prompt = prompt.replace("@OBJECTS_BLOCK@", objects_block)
            raw = ollama(
                prompt,
                system=QUEST_EXPAND_SYSTEM,
                temperature=0.5,
            )

    raise RuntimeError(
        f"Nie udało się wygenerować poprawnego questa '{quest['title']}' "
        f"po {MAX_RETRIES} próbach. Ostatni błąd: {error_msg}"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  GŁÓWNA PĘTLA
# ══════════════════════════════════════════════════════════════════════════════

def build_context(completed: list[dict]) -> str:
    """Buduje kontekst fabularny z już ukończonych questów."""
    if not completed:
        return "To jest pierwszy quest – brak poprzedniego kontekstu."
    lines = []
    for q in completed[-3:]:  # ostatnie 3 dla zwięzłości
        lines.append(f"Quest {q['id']}: {q['title']} – {q['summary']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generator questów PDDL oparty na Ollama + qwen3:8b"
    )
    parser.add_argument("story", help="Zarys fabuły (tekst lub plik .txt)")
    parser.add_argument("domain", default="domain.pddl", help="Ścieżka do pliku domain.pddl")
    parser.add_argument(
        "--quests", type=int, default=NUM_QUESTS,
        help=f"Liczba questów do wygenerowania (domyślnie {NUM_QUESTS})"
    )
    parser.add_argument(
        "--output", default=str(OUTPUT_DIR),
        help="Katalog wyjściowy (domyślnie ./quests)"
    )
    parser.add_argument(
        "--target-actions", type=int, default=None,
        help="Sugerowana liczba akcji na quest (miękka wskazówka dla modelu)"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Tryb debug: wyświetla pełne prompty i odpowiedzi modelu"
    )
    args = parser.parse_args()

    # ── Wczytaj fabułę ───────────────────────────────────────────────────────
    story_input = args.story
    if os.path.isfile(story_input):
        story = Path(story_input).read_text(encoding="utf-8").strip()
    else:
        story = story_input.strip()

    # ── Wczytaj domenę ───────────────────────────────────────────────────────
    domain_path = Path(args.domain).resolve()
    if not domain_path.exists():
        print(f"[BŁĄD] Nie znaleziono pliku domeny: {domain_path}")
        sys.exit(1)
    domain_text = domain_path.read_text(encoding="utf-8")

    # ── Przygotuj katalogi ───────────────────────────────────────────────────
    global DEBUG
    DEBUG = args.debug
    num_quests = args.quests

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_dir / "_tmp"
    tmp_dir.mkdir(exist_ok=True)

    print(f"\n{'#'*60}")
    print(f"  GENERATOR QUESTÓW – {num_quests} questów")
    print(f"  Model: {MODEL}  |  Domena: {domain_path.name}")
    print(f"  Wyjście: {output_dir.resolve()}")
    print(f"{'#'*60}")
    print(f"\nFabuła: {story[:120]}{'...' if len(story)>120 else ''}\n")

    # ── KROK 1: Lista questów ────────────────────────────────────────────────
    quest_list = generate_quest_list(story, domain_text, target_actions=args.target_actions)

    # Zapisz manifest
    manifest_path = output_dir / "quest_manifest.json"
    manifest_path.write_text(
        json.dumps(quest_list, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\n  Manifest zapisany: {manifest_path}")

    # ── KROK 1.5: Biblia świata ─────────────────────────────────────────────
    world_bible = generate_world_bible(story, quest_list, domain_text)
    bible_path = output_dir / "world_bible.json"
    bible_path.write_text(
        json.dumps(world_bible, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Biblia swiata zapisana: {bible_path}")

    # ── KROK 2: Rozwijanie questów ───────────────────────────────────────────
    print(f"\n{'='*60}")
    print("KROK 2: Generowanie plików questów...")
    print('='*60)

    completed: list[dict] = []
    failed: list[dict]    = []

    for quest in quest_list:
        qid   = quest["id"]
        title = quest["title"]
        phase = quest.get("phase", "")

        print(f"\n  Quest {qid:02d}/{num_quests}: [{phase}] {title}")

        quest_dir = output_dir / f"Quest_{qid:02d}"
        quest_dir.mkdir(exist_ok=True)

        context = build_context(completed)
        misc_slice, pddl_objects = bible_slice_for_quest(world_bible, qid)

        try:
            pddl_text, misc_data = generate_quest_files(
                quest, domain_text, context, str(domain_path), tmp_dir,
                misc_slice=misc_slice,
                pddl_objects=pddl_objects,
                target_actions=args.target_actions,
            )
        except RuntimeError as e:
            print(f"\n  [OSTRZEŻENIE] Quest {qid} pominięty: {e}")
            failed.append(quest)
            # Zapisz informację o błędzie
            (quest_dir / "ERROR.txt").write_text(str(e), encoding="utf-8")
            continue

        # Zapisz pliki
        (quest_dir / "problem.pddl").write_text(pddl_text, encoding="utf-8")
        (quest_dir / "misc.json").write_text(
            json.dumps(misc_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # Zapisz metadane questa
        (quest_dir / "quest_meta.json").write_text(
            json.dumps(quest, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        completed.append(quest)
        print(f"    ✓ Zapisano w: {quest_dir}")

    # ── PODSUMOWANIE ─────────────────────────────────────────────────────────
    print(f"\n{'#'*60}")
    print(f"  PODSUMOWANIE")
    print(f"{'#'*60}")
    print(f"  Wygenerowano: {len(completed)}/{num_quests} questów")
    if failed:
        print(f"  Nieudane:     {len(failed)} questów")
        for q in failed:
            print(f"    - Quest {q['id']}: {q['title']}")
    print(f"  Pliki w:      {output_dir.resolve()}")

    # Zapisz raport
    report = {
        "generated": len(completed),
        "failed":    len(failed),
        "completed_quests": [q["id"] for q in completed],
        "failed_quests":    [{"id": q["id"], "title": q["title"]} for q in failed],
        "story_summary": story[:500],
    }
    report_path = output_dir / "generation_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Raport:       {report_path}")
    print()


if __name__ == "__main__":
    main()