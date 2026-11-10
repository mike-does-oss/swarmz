"""Pseudonyms for personas.

Every persona gets a name like "Skeptical Otter" (a forecasting temperament and an
animal, in the spirit of Excalidraw's anonymous collaborators) and a handle, chosen
deterministically from its persona_id and unique within the registry. Swarm agents post under these
names, so a persona is recognisable across questions and nothing in the cast
points at the entities the question is about.
"""

from __future__ import annotations

import hashlib
import unicodedata

FIRST = [  # temperaments a forecaster might be known for
    "Calibrated", "Skeptical", "Patient", "Contrarian", "Cautious", "Steady", "Curious", "Methodical",
    "Quiet", "Stubborn", "Nimble", "Candid", "Wary", "Hopeful", "Sober", "Restless", "Precise", "Bold",
    "Gentle", "Dogged", "Lucid", "Humble", "Brisk", "Watchful", "Earnest", "Measured", "Shrewd", "Tenacious",
    "Frank", "Deliberate", "Keen", "Unhurried", "Hedged", "Rigorous", "Plain", "Sanguine", "Vigilant", "Thoughtful",
]
LAST = [
    "Otter", "Heron", "Lynx", "Falcon", "Badger", "Osprey", "Marten", "Tern", "Ibex", "Kestrel",
    "Puffin", "Caribou", "Gecko", "Walrus", "Finch", "Ocelot", "Newt", "Hare", "Egret", "Mole",
    "Cormorant", "Fox", "Bison", "Lark", "Beaver", "Quail", "Stoat", "Wren", "Yak", "Dormouse",
    "Pelican", "Civet", "Magpie", "Vole", "Jackdaw", "Capybara", "Grebe", "Tapir", "Owl", "Ermine",
]


def pseudonym(persona_id: str, taken: set[str] = frozenset()) -> tuple[str, str]:
    """(display name, handle) for a persona id; walks the hash until the name is unused."""
    for salt in range(10_000):
        h = hashlib.sha256(f"{persona_id}|{salt}".encode()).digest()
        first, last = FIRST[h[0] % len(FIRST)], LAST[h[1] % len(LAST)]
        name = f"{first} {last}"
        if name not in taken and not (salt < 40 and any(t.endswith(" " + last) for t in taken)):  # one animal each, if we can
            handle = unicodedata.normalize("NFKD", f"{first}-{last}").encode("ascii", "ignore").decode().lower()
            return name, handle
    raise RuntimeError("ran out of pseudonyms")


def ensure_names(con) -> int:
    """Give every unnamed persona a pseudonym. Returns how many were named."""
    taken = {r[0] for r in con.execute("SELECT display_name FROM personas WHERE display_name IS NOT NULL").fetchall()}
    missing = [r[0] for r in con.execute(
        "SELECT persona_id FROM personas WHERE display_name IS NULL ORDER BY persona_id").fetchall()]
    for pid in missing:
        name, handle = pseudonym(pid, taken)
        taken.add(name)
        con.execute("UPDATE personas SET display_name = ?, handle = ? WHERE persona_id = ?", [name, handle, pid])
    return len(missing)
