"""Security identifiers a broker writes into its description of a position.

SnapTrade states a position's FIGI (`figi_instrument.figi_code`) and no
ISIN, CUSIP or SEDOL of its own; some brokerages write them into the
description it passes on, labelled, as in "UBS GROUP AG ISIN #CH0244767585
SEDOL #BRJL176". Each is read only where its label names it and its check
digit holds: one that fails is dropped, and said, never corrected or
guessed, and a code with no label is not read. The description itself is
left as SnapTrade gave it.

What is read here goes only to the deployment's sidecar, on a resolve, like
every other identifier of a holding: nothing in this module, or anything it
returns, is sent to SnapTrade or anywhere else.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from .normalise import Identifier

_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
_CUSIP = re.compile(r"[A-Z0-9]{8}[0-9]")
# A SEDOL holds digits and consonants, never a vowel.
_SEDOL = re.compile(r"[0-9B-DF-HJ-NP-TV-Z]{6}[0-9]")
_SEDOL_WEIGHTS = (1, 3, 1, 7, 3, 9, 1)


def _value(character: str) -> int:
    """A digit as itself, a letter as 10 for A to 35 for Z."""
    return int(character) if character.isdigit() else ord(character) - ord("A") + 10


def _luhn_holds(digits: str) -> bool:
    total = 0
    for place, character in enumerate(reversed(digits)):
        digit = int(character)
        if place % 2:
            digit *= 2
            digit -= 9 if digit > 9 else 0
        total += digit
    return total % 10 == 0


def is_isin(value: str) -> bool:
    """An ISIN (ISO 6166) whose check digit holds: its letters as numbers,
    then the Luhn check over every digit."""
    if not _ISIN.fullmatch(value):
        return False
    return _luhn_holds("".join(str(_value(c)) for c in value))


def is_cusip(value: str) -> bool:
    """A CUSIP whose check digit holds (its own modulus 10 double-add-double)."""
    if not _CUSIP.fullmatch(value):
        return False
    total = 0
    for place, character in enumerate(value[:8]):
        number = _value(character) * (2 if place % 2 else 1)
        total += number // 10 + number % 10
    return (10 - total % 10) % 10 == int(value[8])


def is_sedol(value: str) -> bool:
    """A SEDOL whose checksum holds: its weighted sum a multiple of ten."""
    if not _SEDOL.fullmatch(value):
        return False
    return sum(_value(c) * w for c, w in zip(value, _SEDOL_WEIGHTS, strict=True)) % 10 == 0


#: Each scheme read from a description: its label as brokers write it, the
#: shape of its value, and the check it must pass.
_SCHEMES: tuple[tuple[str, str, str, Callable[[str], bool]], ...] = (
    ("isin", "ISIN", r"[A-Z0-9]{12}", is_isin),
    ("cusip", "CUSIP", r"[A-Z0-9]{9}", is_cusip),
    ("sedol", "SEDOL", r"[A-Z0-9]{7}", is_sedol),
)
_LABELLED = {
    scheme: re.compile(rf"(?<![A-Za-z0-9])(?i:{label})\s*[#:]?\s*({shape})(?![A-Za-z0-9])")
    for scheme, label, shape, _ in _SCHEMES
}


def from_description(
    description: str, dropped: list[str] | None = None
) -> tuple[Identifier, ...]:
    """The ISIN, CUSIP and SEDOL a description names, in that order, each
    once and only where its check holds; each that fails is said in
    `dropped`. None where the description names none."""
    found: list[Identifier] = []
    for scheme, label, _, holds in _SCHEMES:
        for value in dict.fromkeys(m.group(1) for m in _LABELLED[scheme].finditer(description)):
            if holds(value):
                found.append(Identifier(scheme, value))
            elif dropped is not None:
                check = "checksum" if scheme == "sedol" else "check digit"
                dropped.append(f"the {label} {value}, whose {check} does not hold")
    return tuple(found)
