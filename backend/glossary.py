"""Course vocabulary for lecture transcription.

Lectures are full of names and terms the model has never seen: a lecturer's name,
a course-specific term, a formula name. Without help the same word is misspelled
the same way all term. This module keeps a small user-editable vocabulary and
uses it two ways:

* as a hint for models that accept a prompt, and
* as a conservative spelling correction for the terms that come back close.

Corrections are deliberately tight. A word is only replaced when it is nearly
identical to a listed term, so ordinary English is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

MAX_GLOSSARY_TERMS = 50
MAX_TERM_CHARS = 48
MAX_PROMPT_CHARS = 400

# Never absorb more than a fifth of a term's letters into a correction.
MAX_EDIT_RATIO = 0.2
MIN_EDIT_BUDGET = 1


def parse_glossary(value: object) -> Tuple[str, ...]:
    """Read a comma/newline separated vocabulary without ever failing a session."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        raw_items: Iterable[object] = value
    else:
        text = str(value)
        for separator in ("\n", ";", "，", "、", "|"):
            text = text.replace(separator, ",")
        raw_items = text.split(",")

    terms: List[str] = []
    seen = set()
    for item in raw_items:
        term = " ".join(str(item).split()).strip()
        if not term or len(term) > MAX_TERM_CHARS:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= MAX_GLOSSARY_TERMS:
            break
    return tuple(terms)


def edit_distance(left: str, right: str, limit: int) -> int:
    """Levenshtein distance, or limit + 1 once the budget is spent."""
    if left == right:
        return 0
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        best = row
        for column, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + cost,
            )
            current.append(value)
            best = min(best, value)
        if best > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _edit_budget(term: str) -> int:
    letters = sum(1 for char in term if char.isalnum())
    return max(MIN_EDIT_BUDGET, int(letters * MAX_EDIT_RATIO))


def _tokens(text: str) -> List[str]:
    return [token for token in text.replace("-", " ").split() if token]


def _strip_punctuation(token: str) -> str:
    return token.strip(".,!?;:\"'()[]{}…“”‘’·")


def _same_letters(left: str, right: str) -> bool:
    """Whole-word anagram check, which catches transpositions in short words."""
    return sorted(left) == sorted(right)


def correct_term(candidate: str, term: str) -> bool:
    """Report whether a transcribed word is a near-miss for a listed term."""
    if candidate == term:
        return False
    if not candidate or not term:
        return False
    # A wrong first letter almost always means a different word.
    if candidate[:1].casefold() != term[:1].casefold():
        return False
    folded_candidate = candidate.casefold()
    folded_term = term.casefold()
    if _same_letters(folded_candidate, folded_term):
        return True
    if abs(len(candidate) - len(term)) > max(len(term) // 3, 1):
        return False
    budget = _edit_budget(term)
    return edit_distance(folded_candidate, folded_term, budget) <= budget


@dataclass(frozen=True)
class Correction:
    original: str
    replacement: str


class Glossary:
    """Turn a raw vocabulary into model hints and downstream corrections."""

    def __init__(self, terms: object = None):
        self.terms: Tuple[str, ...] = parse_glossary(terms)
        self._by_length: Tuple[str, ...] = tuple(sorted(self.terms, key=len, reverse=True))

    def __bool__(self) -> bool:
        return bool(self.terms)

    @property
    def prompt(self) -> str:
        """A vocabulary hint for models that accept an initial prompt."""
        if not self.terms:
            return ""
        prompt = "Vocabulary: " + ", ".join(self.terms) + "."
        return prompt[:MAX_PROMPT_CHARS]

    def correct(self, text: str) -> str:
        """Fix near-miss spellings of listed terms inside one caption."""
        if not self.terms or not text:
            return text
        words = text.split(" ")
        for term in self._by_length:
            term_words = _tokens(term)
            hits = self._replace_from(words, term, term_words)
            if hits:
                words = hits
        return " ".join(words)

    def corrections(self, text: str) -> List[Correction]:
        """Report the replacements that :meth:`correct` would make."""
        if not self.terms or not text:
            return []
        found: List[Correction] = []
        for word in text.split(" "):
            token = _strip_punctuation(word)
            if not token:
                continue
            for term in self._by_length:
                if len(_tokens(term)) != 1:
                    continue
                if correct_term(token, term):
                    found.append(Correction(token, term))
                    break
        return found

    def _replace_from(
        self, words: Sequence[str], term: str, term_words: Sequence[str]
    ) -> List[str]:
        span = len(term_words)
        if span == 0 or len(words) < span:
            return []
        output = list(words)
        replaced = False
        index = 0
        while index <= len(output) - span:
            window = output[index : index + span]
            stripped = [_strip_punctuation(word) for word in window]
            if all(stripped) and self._matches(stripped, term_words, term):
                leading = window[0][: len(window[0]) - len(stripped[0])]
                trailing = window[-1][len(stripped[-1]) :]
                output[index : index + span] = [leading + term + trailing]
                replaced = True
            index += 1
        return output if replaced else []

    @staticmethod
    def _matches(stripped: Sequence[str], term_words: Sequence[str], term: str) -> bool:
        if len(stripped) != len(term_words):
            return False
        if len(term_words) == 1:
            return correct_term(stripped[0], term_words[0])
        budget = max(MIN_EDIT_BUDGET, _edit_budget(term) // len(term_words))
        for candidate, expected in zip(stripped, term_words):
            if candidate == expected:
                continue
            if correct_term(candidate, expected):
                continue
            if edit_distance(candidate.casefold(), expected.casefold(), budget) > budget:
                return False
        return True
