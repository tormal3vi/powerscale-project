"""Pydantic response/request models for the API.

Deliberately mirror the shapes normalizer.py/parser.py/calculator.py
already produce (NormalizedRange, NormalizedForm, CharacterForm,
Verdict, AxisComparison, AbilityFlag) field-for-field, rather than
inventing a new shape - this is a thin HTTP layer, not a second data
model. Nothing here computes anything; main.py just packs existing
dataclass/dict data into these for a typed, self-documenting response.
"""

from datetime import datetime
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field


# --- shared building blocks ---------------------------------------------

class NormalizedRangeOut(BaseModel):
    raw: Optional[str] = None
    baseline: Optional[float] = None
    baseline_qualifier: Optional[str] = None
    baseline_label: Optional[str] = None
    peak: Optional[float] = None
    peak_qualifier: Optional[str] = None
    peak_label: Optional[str] = None


class FormOut(BaseModel):
    """One CharacterForm merged with its matching NormalizedForm - raw
    wiki-format text (for display) alongside the normalized score (for
    the radar chart / stat bars), field-for-field, per stat."""
    name: str
    is_omnipresent: bool = False

    tier_raw: Optional[str] = None
    tier: NormalizedRangeOut

    attack_potency_raw: Optional[str] = None
    attack_potency: NormalizedRangeOut

    speed_raw: Optional[str] = None
    speed: NormalizedRangeOut

    durability_raw: Optional[str] = None
    durability: NormalizedRangeOut

    # Not normalized anywhere in normalizer.py (see its module docstring)
    # - kept as raw text only, same as parser.py/StatBlock.
    lifting_strength_raw: Optional[str] = None
    striking_strength_raw: Optional[str] = None
    stamina_raw: Optional[str] = None
    range_raw: Optional[str] = None


# --- /api/categories ------------------------------------------------------

class CategoryOut(BaseModel):
    name: str
    count: int


# --- /api/characters (list) ------------------------------------------------

class CharacterSummaryOut(BaseModel):
    id: int
    name: str
    category: str
    tier_label: Optional[str] = None  # highest-tier form's Tier, for the card badge
    tier_score: Optional[float] = None  # same form's numeric Tier - for sorting by strength
    aliases: str = ""  # the full stored name/alias list - searchable, not displayed
    scorable: bool = False  # default form has 2+ scored stats (can get a verdict)
    form_count: int = 1
    is_multi_form: bool = False


class CharacterListOut(BaseModel):
    total: int
    characters: List[CharacterSummaryOut]


# --- /api/characters/{id} (detail) -----------------------------------------

class CharacterDetailOut(BaseModel):
    id: int
    name: str
    category: str
    source_url: str
    origin: Optional[str] = None
    classification: Optional[str] = None
    powers_and_abilities: List[str] = []
    weaknesses: Optional[str] = None
    forms: List[FormOut]


# --- /api/characters/fetch (add a character) --------------------------------

class FetchCharacterIn(BaseModel):
    query: str  # character name or full wiki URL, same as app.py's sidebar input


# --- /api/compare -----------------------------------------------------------

CharacterRef = Union[int, str]


class CompareIn(BaseModel):
    char_a: CharacterRef
    char_b: CharacterRef
    form_a: Optional[str] = None
    form_b: Optional[str] = None


class AxisComparisonOut(BaseModel):
    axis: str
    a_value: Optional[float] = None
    a_source: str
    b_value: Optional[float] = None
    b_source: str
    delta: Optional[float] = None
    advantage: Optional[float] = None
    weight_used: Optional[float] = None


class AbilityFlagOut(BaseModel):
    tag: str
    characters: List[str]


class OverrideOut(BaseModel):
    """An admin's ruling on one exact matchup (characters + forms), shown
    instead of - never mixed into - the calculator's own verdict."""
    winner_id: int
    winner_name: str
    note: str
    admin: str
    created_at: datetime


class VerdictOut(BaseModel):
    character_a: str
    character_b: str
    form_a: str
    form_b: str
    axis_comparisons: List[AxisComparisonOut]
    axes_used: int
    composite: Optional[float] = None
    label: str
    confidence_hint: str
    favored: Optional[str] = None
    partial_data: bool
    ability_flags: List[AbilityFlagOut]
    notes: List[str]
    override: Optional[OverrideOut] = None


# --- accounts -------------------------------------------------------------------

class AuthIn(BaseModel):
    username: str = Field(..., max_length=40)
    password: str = Field(..., max_length=200)


class UserOut(BaseModel):
    username: str
    is_admin: bool


class MeOut(BaseModel):
    user: Optional[UserOut] = None


# --- admin overrules --------------------------------------------------------------

class OverrideIn(BaseModel):
    char_a: int
    char_b: int
    form_a: str = Field(..., max_length=200)
    form_b: str = Field(..., max_length=200)
    winner_id: int
    note: str = Field("", max_length=300)


# --- message board ----------------------------------------------------------------

class MatchupOut(BaseModel):
    char_a: int
    char_b: int
    form_a: Optional[str] = None
    form_b: Optional[str] = None
    name_a: str          # display name, e.g. "Dante (Devil May Cry)"
    name_b: str
    label_a: str         # short name with no "(...)", e.g. "Dante"
    label_b: str
    category_a: str
    category_b: str
    calc_verdict: str    # the calculator's own call, e.g. "Kratos favored — Overwhelming favorite"
    overruled_winner: Optional[str] = None  # short name of the admins' pick, if overruled
    overruled_winner_id: Optional[int] = None


class PostIn(BaseModel):
    body: str = Field(..., max_length=2000)
    parent_id: Optional[int] = None
    char_a: Optional[int] = None
    char_b: Optional[int] = None
    form_a: Optional[str] = Field(None, max_length=200)
    form_b: Optional[str] = Field(None, max_length=200)


class RulingOut(BaseModel):
    """What an admin-overrule post ruled, as of when it was posted."""
    winner: str   # short name, e.g. "Dante"
    status: str   # "current", "changed" (since re-ruled) or "lifted" (overrule removed)


class PostOut(BaseModel):
    id: int
    parent_id: Optional[int] = None
    author: str
    author_is_admin: bool = False
    kind: Optional[str] = None  # "overrule" for posts made by an admin ruling
    ruling: Optional[RulingOut] = None
    body: str
    created_at: datetime
    like_count: int
    reply_count: int
    liked_by_me: bool
    can_delete: bool
    matchup: Optional[MatchupOut] = None


class PostListOut(BaseModel):
    posts: List[PostOut]
    next_before: Optional[int] = None


class ThreadOut(BaseModel):
    post: PostOut
    replies: List[PostOut]


class LikeOut(BaseModel):
    liked: bool
    like_count: int
