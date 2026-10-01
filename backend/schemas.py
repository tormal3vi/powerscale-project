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


class TitleOut(BaseModel):
    key: str
    name: str
    color: str  # a CSS class suffix: t-<color>


class NextTitleOut(TitleOut):
    have: int
    need: int
    what: str  # "duel wins", "approved overrule suggestions"


class TitleIn(BaseModel):
    key: Optional[str] = Field(None, max_length=32)  # None: the rarest automatically; "none": no title


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
    # The linked Discord account: always on your own profile, on anyone
    # else's only if they show it.
    discord: Optional[str] = None
    discord_id: Optional[str] = None
    discord_shown: Optional[bool] = None  # your own profile only: the "show it" setting
    challenge_button: bool = True  # a Challenge button on their public profile
    title: Optional[TitleOut] = None  # the one shown next to their name
    titles: List[TitleOut] = []  # every one earned, rarest first
    next_titles: List[NextTitleOut] = []  # the next ones to work towards
    title_choice: Optional[str] = None  # your own profile only: what you picked (None: automatic)


class DiscordLinkIn(BaseModel):
    code: str = Field(..., max_length=64)


class DiscordShownIn(BaseModel):
    shown: bool


class ChallengeButtonIn(BaseModel):
    shown: bool


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
    credit: Optional[str] = None  # on an overrule made from a ticket: who sent it
    author: str
    author_is_admin: bool = False
    author_title: Optional[TitleOut] = None
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
    author_title: Optional[TitleOut] = None
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
    mode: str = "predict"  # predict | draft
    link_only: bool = False  # open, but not listed or posted: only people with the link join
    # Gauntlet games: where opponents come from, and up to five challengers.
    gauntlet_source: str = "random"  # random | series | custom
    gauntlet_series: Optional[str] = Field(None, max_length=120)
    gauntlet_opponents: List[int] = Field([], max_length=10)
    challengers: List[int] = Field([], max_length=5)


class DuelJoinIn(BaseModel):
    team: Optional[int] = None  # None: whichever team has the most room


class DuelPlayerOut(BaseModel):
    username: str
    is_admin: bool = False
    title: Optional[TitleOut] = None
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
    form: Optional[str] = None  # None for a character with a single form
    image_url: Optional[str] = None


class DuelRoundOut(BaseModel):
    """A round being played: the two sides (or, in a draft, your hand), and
    the time left to pick."""
    game_id: int
    round_no: int
    total: int
    seconds_left: float
    mode: str = "predict"
    a: Optional[DuelSideOut] = None
    b: Optional[DuelSideOut] = None
    hand: List[DuelSideOut] = []
    ladder: List[DuelSideOut] = []  # old gauntlet rounds: the opponents, lowest rung first (a is the challenger)
    # Gauntlet calls: does a (the challenger) beat b (the next opponent)?
    gauntlet_no: Optional[int] = None
    gauntlets: Optional[int] = None
    rung: Optional[int] = None
    rungs: Optional[int] = None
    trail: List[DuelSideOut] = []  # the opponents it has beaten so far in this gauntlet


class DuelPickIn(BaseModel):
    round_no: int
    pick_id: int


class CallRevealOut(BaseModel):
    """A gauntlet call's result, shown right after it's made."""
    beat: bool
    verdict: str
    correct: bool
    ended: bool  # this gauntlet is over: it lost, or it cleared the ladder
    climbed: int
    rungs: int
    last_call: bool
    points: int = 0  # what this call earned: 1 for a right "beats them", 3 for calling the knockout


class DuelPickOut(BaseModel):
    in_time: bool
    reveal: Optional[CallRevealOut] = None  # gauntlet calls
    next: Optional[DuelRoundOut] = None  # the following round, already started; None when done


class DuelRoundPickOut(BaseModel):
    username: str
    team: int
    pick_id: Optional[int] = None
    correct: bool = False
    # Draft: the hand they were dealt, the head-to-head wins their pick
    # earned, and the hand's highest-tier character.
    hand: List[DuelSideOut] = []
    points: int = 0
    best_id: Optional[int] = None
    calls: List[Optional[bool]] = []  # gauntlet: each call right (True), wrong (False) or missed (None)


class DuelBoutOut(BaseModel):
    """Draft: one head-to-head of a round - two players' picks (None: no
    pick in time) and who won it, if anyone."""
    user_a: str
    user_b: str
    a: Optional[DuelSideOut] = None
    b: Optional[DuelSideOut] = None
    winner: Optional[str] = None  # a username
    by_speed: bool = False  # dead even: the faster pick won
    compare_url: Optional[str] = None


class GauntletFightOut(BaseModel):
    rung: int
    opponent: DuelSideOut
    outcome: str  # win | loss | even (too close to call) | none (not enough stats)
    verdict: str
    reached: bool = True  # False: after the run had already ended
    compare_url: str = ""


class GauntletOut(BaseModel):
    """A solo gauntlet run (see backend/gauntlet.py)."""
    character: DuelSideOut
    source: str
    series: Optional[str] = None
    seed: Optional[int] = None  # random ladders: the same seed draws the same ladder
    climbed: int
    total: int
    fights: List[GauntletFightOut]


class DuelResultRoundOut(BaseModel):
    round_no: int
    a: Optional[DuelSideOut] = None  # predict rounds only
    b: Optional[DuelSideOut] = None
    answer_id: int
    verdict: str  # what decided it: "Superman favored — Clear favorite" or an overrule
    picked: bool  # chosen by the challenger rather than drawn at random
    picks: List[DuelRoundPickOut]
    compare_url: str
    bouts: List[DuelBoutOut] = []  # draft rounds only
    fights: List[GauntletFightOut] = []  # gauntlet rounds only (a is the challenger, answer_id its wins)


class DuelOut(BaseModel):
    id: int
    status: str  # open | active | done | expired | declined | cancelled
    created_at: datetime
    format: str  # "1v1", "2v2", "1v1v1"...
    mode: str = "predict"  # predict | draft
    teams: int
    team_size: int
    creator: str
    players: List[DuelPlayerOut]
    invited: List[str] = []  # invited players who haven't joined yet
    private: bool = False
    link_only: bool = False  # open to whoever has the link, not listed
    by_speed: bool = False  # gauntlet duels: level on calls, the faster side won
    team_seconds: List[float] = []  # gauntlet duels: each side's total answering time, for the tiebreak
    knockout_points: int = 1  # gauntlet duels: what a right call on the losing fight was worth
    gauntlet: Optional[str] = None  # gauntlet games: "random", "custom" or "series:<name>"
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
    title: Optional[TitleOut] = None
    avatar_url: Optional[str] = None
    wins: int
    draws: int
    losses: int


class LeaderboardOut(BaseModel):
    rows: List[LeaderboardRowOut]



# --- tickets ------------------------------------------------------------------------------

class TicketIn(BaseModel):
    char_a: int
    char_b: int
    form_a: Optional[str] = None
    form_b: Optional[str] = None
    winner_id: int
    reason: str = Field(..., max_length=4000)


class TicketAnswerIn(BaseModel):
    response: str = Field("", max_length=4000)


class TicketBanIn(BaseModel):
    username: str = Field(..., max_length=40)
    reason: str = Field("", max_length=600)


class TicketOut(BaseModel):
    id: int
    matchup: Optional[MatchupOut] = None  # None if a character was removed since
    winner_id: int
    winner: str  # who the user says wins
    reason: str
    status: str  # open | answered
    outcome: Optional[str] = None  # overruled | kept
    response: Optional[str] = None
    admin: Optional[str] = None
    created_at: datetime
    answered_at: Optional[datetime] = None
    author: str
    author_avatar: Optional[str] = None
    author_banned: bool = False  # admins only


class MyTicketOut(BaseModel):
    ticket: Optional[TicketOut] = None
    banned: bool = False
    ban_reason: Optional[str] = None


class TicketListOut(BaseModel):
    tickets: List[TicketOut]
    open_count: int


class TicketBanOut(BaseModel):
    username: str
    admin: str
    reason: str
    created_at: datetime



# --- public profile ---------------------------------------------------------------------

class ProfileCommentOut(BaseModel):
    id: int
    body: str
    created_at: datetime
    label_a: str
    label_b: str
    compare_url: str


class PublicProfileOut(BaseModel):
    profile: ProfileOut
    likes_received: int = 0
    comment_count: int = 0
    duel_rank: Optional[int] = None  # place on the duels leaderboard
    overrules_suggested: int = 0  # tickets that became overrules
    duels: List[DuelOut] = []  # latest finished, from their side
    posts: List[PostOut] = []
    credited: List[PostOut] = []  # overrule posts made from their tickets
    comments: List[ProfileCommentOut] = []
    tickets: List[TicketOut] = []  # only when an admin is looking
    ticket_banned: bool = False  # only when an admin is looking
    ticket_ban_reason: Optional[str] = None


# Names models defined further down this file.
ProfileOut.model_rebuild()
