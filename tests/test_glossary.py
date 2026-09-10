import pytest

from backend.glossary import (
    MAX_GLOSSARY_TERMS,
    Correction,
    Glossary,
    correct_term,
    edit_distance,
    parse_glossary,
)


def test_parse_glossary_accepts_separators_and_drops_noise():
    terms = parse_glossary("  gradient descent, Kubernetess ;\n eigenvalue，收敛 、  |  ")

    assert terms == ("gradient descent", "Kubernetess", "eigenvalue", "收敛")
    assert parse_glossary(None) == ()
    assert parse_glossary("") == ()
    # Duplicates collapse, order is preserved.
    assert parse_glossary("Adam, adam, ADAM") == ("Adam",)


def test_parse_glossary_bounds_the_vocabulary():
    crowded = ", ".join("term-%d" % index for index in range(MAX_GLOSSARY_TERMS + 25))

    assert len(parse_glossary(crowded)) == MAX_GLOSSARY_TERMS
    assert parse_glossary("x" * 200) == ()


def test_edit_distance_stops_once_the_budget_is_spent():
    assert edit_distance("abcd", "abcd", 1) == 0
    assert edit_distance("abcd", "abce", 1) == 1
    assert edit_distance("abcd", "abxy", 1) == 2
    assert edit_distance("a", "abcdef", 1) == 2


def test_correction_only_fires_for_near_misses():
    assert correct_term("gradien", "gradient") is True
    # A heavily truncated word is not a spelling mistake we should guess at.
    assert correct_term("gradi", "gradient") is False
    # A transposition in a short name is still the same word.
    assert correct_term("adma", "Adam") is True
    # Matching case only still loses to the spelling the user listed.
    assert correct_term("Gradient", "gradient") is True
    assert correct_term("gradient", "gradient") is False
    # A different word that merely starts with the same letter is left alone.
    assert correct_term("grand", "gradient") is False
    assert correct_term("cat", "car") is True  # single substitution, same stem
    assert correct_term("cart", "colt") is False
    assert correct_term("unrelated", "eigenvalue") is False


def test_glossary_builds_a_bounded_prompt():
    glossary = Glossary("gradient descent, eigenvalue")

    assert glossary.prompt.startswith("Vocabulary: ")
    assert "gradient descent" in glossary.prompt
    assert Glossary("").prompt == ""
    long_prompt = Glossary(",".join("term-%d" % index for index in range(50))).prompt
    assert len(long_prompt) <= 400


def test_glossary_repairs_single_and_multi_word_terms():
    glossary = Glossary("gradient descent, eigenvalue, Adam")

    assert glossary.correct("we use gradien descent here") == "we use gradient descent here"
    assert glossary.correct("the eigenvlue is 3") == "the eigenvalue is 3"
    assert glossary.correct("Adam optimizer") == "Adam optimizer"
    assert glossary.correct("the adma optimizer") == "the Adam optimizer"


def test_glossary_keeps_punctuation_and_ordinary_words():
    glossary = Glossary("gradient descent")

    assert glossary.correct("gradien descent.") == "gradient descent."
    assert glossary.correct("gradient descent") == "gradient descent"
    assert glossary.correct("the grand canyon is deep") == "the grand canyon is deep"
    assert glossary.correct("") == ""
    assert Glossary("").correct("gradien descent") == "gradien descent"


def test_glossary_can_report_what_it_changed():
    glossary = Glossary("gradient descent, eigenvalue")

    found = glossary.corrections("the eigenvlue is a gradien")

    assert [(item.original, item.replacement) for item in found] == [
        ("eigenvlue", "eigenvalue"),
    ]
    assert glossary.corrections("nothing to fix") == []
    assert Glossary("Kubernetess").corrections("we deploy Kubernets today") == [
        Correction("Kubernets", "Kubernetess")
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Gradien descent is used", "gradient descent is used"),
        ("Gradient Decent is used", "gradient descent is used"),
        ("the Gradient Descent step", "the gradient descent step"),
        ("no terms here", "no terms here"),
    ],
)
def test_glossary_uses_the_listed_spelling(text, expected):
    glossary = Glossary("gradient descent")

    assert glossary.correct(text) == expected


def test_glossary_normalizes_a_plural_to_the_listed_term():
    glossary = Glossary("eigenvalue")

    assert glossary.correct("two eigenvlues appear") == "two eigenvalue appear"
