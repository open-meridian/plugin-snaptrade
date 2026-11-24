"""Security identifiers a broker writes into SnapTrade's description of a
position: an ISIN, a CUSIP or a SEDOL, each read only where its check digit
holds, and none guessed."""

from __future__ import annotations

import pytest

from snaptrade.normalise import Identifier
from snaptrade.security_ids import from_description, is_cusip, is_isin, is_sedol


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("CH0244767585", True),  # UBS Group
        ("US0378331005", True),  # Apple
        ("GB0002634946", True),  # BAE Systems
        ("CH0244767586", False),  # the check digit off by one
        ("US0378331004", False),
        ("US037833100", False),  # one character short
        ("1S0378331005", False),  # no country code
        ("us0378331005", False),  # as written, never upper-cased
    ],
)
def test_an_isin_holds_only_with_its_check_digit(value: str, valid: bool) -> None:
    assert is_isin(value) is valid


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("BRJL176", True),  # UBS Group
        ("0263494", True),  # BAE Systems
        ("B0YBKJ7", True),
        ("BRJL177", False),  # the checksum off by one
        ("BRJA176", False),  # a vowel, which a SEDOL never holds
        ("BRJL17", False),  # one character short
    ],
)
def test_a_sedol_holds_only_with_its_checksum(value: str, valid: bool) -> None:
    assert is_sedol(value) is valid


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("037833100", True),  # Apple
        ("38259P508", True),  # Alphabet, a letter in it
        ("037833101", False),  # the check digit off by one
        ("03783310", False),  # one character short
    ],
)
def test_a_cusip_holds_only_with_its_check_digit(value: str, valid: bool) -> None:
    assert is_cusip(value) is valid


def test_an_isin_and_a_sedol_are_read_out_of_a_brokers_description() -> None:
    assert from_description("UBS GROUP AG ISIN #CH0244767585 SEDOL #BRJL176") == (
        Identifier("isin", "CH0244767585"),
        Identifier("sedol", "BRJL176"),
    )


def test_a_cusip_written_the_same_way_is_read_too() -> None:
    assert from_description("APPLE INC CUSIP #037833100") == (Identifier("cusip", "037833100"),)


def test_the_label_may_be_written_in_any_case_with_or_without_a_mark() -> None:
    assert from_description("Ubs Group Ag isin: CH0244767585 Sedol BRJL176") == (
        Identifier("isin", "CH0244767585"),
        Identifier("sedol", "BRJL176"),
    )


def test_an_identifier_whose_check_fails_is_dropped_never_guessed() -> None:
    assert from_description("UBS GROUP AG ISIN #CH0244767586 SEDOL #BRJL177") == ()
    # The valid one of a pair is still read.
    assert from_description("UBS GROUP AG ISIN #CH0244767585 SEDOL #BRJL177") == (
        Identifier("isin", "CH0244767585"),
    )


def test_a_description_naming_no_identifier_gives_none() -> None:
    for description in (
        "Apple Inc",
        "",
        "ISINGLASS HOLDINGS CORP",  # a word that begins like the label
        "Vanguard Total Stock Market ETF CH0244767585",  # a code with no label
        "UBS GROUP AG ISIN #",
    ):
        assert from_description(description) == ()


def test_an_identifier_written_twice_is_read_once() -> None:
    assert from_description("UBS ISIN CH0244767585 (ISIN CH0244767585)") == (
        Identifier("isin", "CH0244767585"),
    )


def test_the_identifiers_found_say_why_the_rest_were_dropped() -> None:
    dropped: list[str] = []
    found = from_description("UBS GROUP AG ISIN #CH0244767586 SEDOL #BRJL176", dropped)
    assert found == (Identifier("sedol", "BRJL176"),)
    assert dropped == ["the ISIN CH0244767586, whose check digit does not hold"]
