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
    image_url: Optional[str] = None  # this form's own picture; None = the character's

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

class SubseriesOut(BaseModel):
    name: str
    count: int


class CategoryOut(BaseModel):
    name: str
    count: int
    # Parts of a franchise too big for one filter (DC: Comics, Arkham, ...).
    subseries: List[SubseriesOut] = []


# --- /api/characters (list) ------------------------------------------------

class CharacterSummaryOut(BaseModel):
    id: int
    name: str
    category: str
    subseries: Optional[str] = None
    tier_label: Optional[str] = None  # highest-tier form's Tier, for the card badge
    tier_score: Optional[float] = None  # same form's numeric Tier - for sorting by strength
    # Tie-breakers for sorting by strength (see main._card_info), since
    # ~2,700 characters share only ~50 distinct Tier scores.
    tiebreak: List[Optional[float]] = []
    aliases: str = ""  # the full stored name/alias list - searchable, not displayed
    scorable: bool = False  # default form has 2+ scored stats (can get a verdict)
    form_count: int = 1
    is_multi_form: bool = False
    # Wiki CDN URL (size-free; the page picks the size) or an admin's
    # replacement (/api/character-images/<id>?v=...). None: letter tile.
    image_url: Optional[str] = None


class CharacterListOut(BaseModel):
    total: int
    characters: List[CharacterSummaryOut]


# --- /api/characters/{id} (detail) -----------------------------------------

class CharacterDetailOut(BaseModel):
    id: int
    name: str
    category: str
    subseries: Optional[str] = None
    source_url: str
    origin: Optional[str] = None
    classification: Optional[str] = None
    powers_and_abilities: List[str] = []
    weaknesses: Optional[str] = None
    image_url: Optional[str] = None
    image_replaced: bool = False  # an admin's upload, not the wiki's picture
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
    avatar_url: Optional[str] = None  # None: show the letter tile


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


class FavoriteOut(BaseModel):
    id: int
    name: str                        # short name, e.g. "Giorno Giovanna"
    image_url: Optional[str] = None  # same kind of URL as CharacterSummaryOut.image_url


class ProfileOut(BaseModel):
    username: str
    is_admin: bool
    avatar_url: Optional[str] = None
    bio: str = ""
    favorite: Optional[FavoriteOut] = None
    member_since: datetime
    post_count: int
    likes_received: Optional[int] = None  # only on your own profile
    record: Optional["RecordOut"] = None  # prediction duels


class ProfileIn(BaseModel):
    username: str = Field(..., max_length=40)
    bio: str = Field("", max_length=400)
    favorite_char_id: Optional[int] = None


class PasswordIn(BaseModel):
    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


class DeleteAccountIn(BaseModel):
    password: str = Field(..., max_length=200)


class RulingOut(BaseModel):
    """What an admin-overrule post ruled, as of when it was posted."""
    winner: str   # short name, e.g. "Dante"
    status: str   # "current", "changed" (since re-ruled) or "lifted" (overrule removed)


class PostOut(BaseModel):
    id: int
    parent_id: Optional[int] = None
    author: str
    author_is_admin: bool = False
    author_avatar: Optional[str] = None
    author_favorite: Optional[FavoriteOut] = None
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


# --- matchup comments ------------------------------------------------------------

class CommentIn(BaseModel):
    body: str = Field(..., max_length=2000)


class CommentOut(BaseModel):
    id: int
    author: str
    author_is_admin: bool = False
    author_avatar: Optional[str] = None
    author_favorite: Optional[FavoriteOut] = None
    body: str
    created_at: datetime
    can_delete: bool


class CommentListOut(BaseModel):
    comments: List[CommentOut]


# --- prediction duels ---------------------------------------------------------------

class RecordOut(BaseModel):
    wins: int = 0
    draws: int = 0
    losses: int = 0


class DuelMatchupIn(BaseModel):
    char_a: int
    char_b: int
    form_a: Optional[str] = None
    form_b: Optional[str] = None


class DuelCreateIn(BaseModel):
    format: str = "1v1"  # see duels.FORMATS
    invite: List[str] = Field([], max_length=5)  # usernames for a private game; empty: open to anyone
    matchups: List[DuelMatchupIn] = Field([], max_length=5)  # picked ones; the rest are random
    exclude: List[str] = Field([], max_length=100)  # series left out of the random rounds


class DuelJoinIn(BaseModel):
    team: Optional[int] = None  # None: whichever team has the most room


class DuelPlayerOut(BaseModel):
    username: str
    is_admin: bool = False
    avatar_url: Optional[str] = None
    team: int
    played: int = 0
    score: Optional[int] = None  # once done
    outcome: Optional[str] = None  # win | draw | loss, once done
    me: bool = False


class DuelSideOut(BaseModel):
    id: int
    name: str
    series: str
    form: Optional[str] = None  # only when it isn't the character's default form
    image_url: Optional[str] = None


class DuelRoundOut(BaseModel):
    """A round being played: the two sides, and the time left to pick."""
    game_id: int
    round_no: int
    total: int
    seconds_left: float
    a: DuelSideOut
    b: DuelSideOut


class DuelPickIn(BaseModel):
    round_no: int
    pick_id: int


class DuelPickOut(BaseModel):
    in_time: bool
    next: Optional[DuelRoundOut] = None  # the following round, already started; None when done


class DuelRoundPickOut(BaseModel):
    username: str
    team: int
    pick_id: Optional[int] = None
    correct: bool = False


class DuelResultRoundOut(BaseModel):
    round_no: int
    a: DuelSideOut
    b: DuelSideOut
    answer_id: int
    verdict: str  # what decided it: "Superman favored — Clear favorite" or an overrule
    picked: bool  # chosen by the challenger rather than drawn at random
    picks: List[DuelRoundPickOut]
    compare_url: str


class DuelOut(BaseModel):
    id: int
    status: str  # open | active | done | expired | declined | cancelled
    created_at: datetime
    format: str  # "1v1", "2v2", "1v1v1"...
    teams: int
    team_size: int
    creator: str
    players: List[DuelPlayerOut]
    invited: List[str] = []  # invited players who haven't joined yet
    private: bool = False
    seats_left: int = 0
    picked: Optional[int] = None  # rounds the creator chose; the rest are random (None: not recorded)
    excluded: List[str] = []  # series left out of the random rounds
    my_team: Optional[int] = None
    my_played: int = 0
    total: int = 5
    can_play: bool = False
    can_join: bool = False
    join_teams: List[int] = []  # teams with room, when you can join
    can_leave: bool = False
    can_decline: bool = False
    can_cancel: bool = False
    outcome: Optional[str] = None  # yours, once done
    team_scores: List[int] = []  # by team, once done
    rounds: List[DuelResultRoundOut] = []  # once done


class DuelListOut(BaseModel):
    mine: List[DuelOut]
    open: List[DuelOut]  # others' open games with a free seat


class LeaderboardRowOut(BaseModel):
    username: str
    is_admin: bool = False
    avatar_url: Optional[str] = None
    wins: int
    draws: int
    losses: int


class LeaderboardOut(BaseModel):
    rows: List[LeaderboardRowOut]


# Names a model defined further down this file.
ProfileOut.model_rebuild()
