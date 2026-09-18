"""Streamlit UI: pick 2-4 characters from the DB and compare their
normalized stats side by side (radar/bar chart + raw stat text +
abilities/weaknesses panels), plus a "Who would win?" verdict between
any two of the selected characters (Phase 5's calculator.py, wired in
here).

Multi-form follow-up: a character's normalized data always has a
`forms` list (>= 1 entry - see normalizer.py). Everything in this file
reads through `forms[selected_index]` uniformly rather than the
top-level flat fields, since `forms` is the canonical source of truth
(the flat fields are kept on the data model for backward compatibility,
not because anything here needs them). For an ordinary single-"Base"-
form character this is identical to reading the flat fields; for a
multi-form character (Genos, Vegeta, Goku, and ~5 others in the current
DB) it lets the sidebar's per-character form picker actually take
effect. Form choice is *not* persisted across sessions - see
`_prune_stale_form_selections` below for why that's deliberate.

Who-would-win wiring: the verdict section below deliberately does NOT
get its own form picker. It reuses whichever form is already active
for each character in the sidebar (the same `rows[i]["form"]` the
chart/table already read from), so what you see plotted is exactly
what the verdict is computed from - never a second, silently different
form choice. This means the *default* form used here is index 0 (the
app's own convention, see `selected_form_index` below), not
calculator.py's own standalone default of "highest Tier" - a
deliberate divergence for this app specifically, not an oversight.

Run with: ./venv/bin/streamlit run app.py
"""

import json

import plotly.graph_objects as go
import streamlit as st

import calculator
import db
import normalizer
import parser as parser_module
import scraper

st.set_page_config(page_title="Powerscale Comparator", layout="wide")

# Every other entry point (batch_scrape.py, main.py's data/ output) either
# creates powerscale.db's schema itself or doesn't touch the DB at all - this
# app never did, silently relying on the file already existing from a prior
# batch_scrape.py run. That's invisible on a dev machine where it always has,
# but breaks on a fresh checkout (e.g. Streamlit Cloud cloning the repo,
# where powerscale.db is deliberately gitignored - see README) with "no such
# table: characters". init_db()'s CREATE TABLE IF NOT EXISTS is a no-op when
# the schema already exists, so this is safe to call unconditionally here.
db.init_db()

MAX_CHARACTERS = 4
MIN_CHARACTERS = 2
STAT_AXES = ["Tier/AP", "Durability", "Speed"]

# A handful of acronyms that str.title() mangles (e.g. "Massively Ftl+")
# - purely cosmetic, doesn't touch normalizer.py's actual matching logic.
_ACRONYM_FIXUPS = {"Ftl": "FTL"}


# --- data access (cached) ---------------------------------------------------

@st.cache_data
def load_characters():
    return db.get_all_characters()


@st.cache_data
def load_form_counts():
    return db.get_form_counts()


def option_label(row: dict, form_counts: dict) -> str:
    base = f"{row['name']} ({row['category'] or 'Uncategorized'})"
    count = form_counts.get(row["id"], 1)
    return f"{base} · {count} forms" if count > 1 else base


def form_select_key(char_id: int) -> str:
    return f"form_select_{char_id}"


def _prune_stale_form_selections(selected_ids: list) -> None:
    """Form choice is deliberately NOT remembered across a character
    being removed and re-added: each selectbox below is keyed per
    character id, and Streamlit would otherwise silently resurrect
    whatever form was picked last time under that same key. Dropping
    the key here forces a fresh default (form 0) on re-add, per the
    "no stale state leaking" requirement."""
    active_keys = {form_select_key(cid) for cid in selected_ids}
    for key in list(st.session_state.keys()):
        if key.startswith("form_select_") and key not in active_keys:
            del st.session_state[key]


def selected_form_index(char_id: int, num_forms: int) -> int:
    idx = st.session_state.get(form_select_key(char_id), 0)
    return idx if 0 <= idx < num_forms else 0


# --- formatting helpers ------------------------------------------------------

def _prettify_label(label):
    if not label:
        return None
    text = label.title()
    for wrong, right in _ACRONYM_FIXUPS.items():
        text = text.replace(wrong, right)
    return text


def format_stat_cell(range_dict: dict) -> str:
    if not range_dict or range_dict.get("baseline") is None:
        return "Unknown"

    def fmt(label, qualifier):
        label = _prettify_label(label)
        return f"{label} ({qualifier})" if qualifier else label

    baseline_str = fmt(range_dict.get("baseline_label"), range_dict.get("baseline_qualifier"))
    peak_str = fmt(range_dict.get("peak_label"), range_dict.get("peak_qualifier"))
    if range_dict["baseline"] == range_dict["peak"]:
        return baseline_str
    return f"{baseline_str} → {peak_str}"


def format_speed_cell(range_dict: dict, is_omnipresent: bool) -> str:
    """Omnipresence is a separate claim from a stated combat speed, not
    a replacement for one (a character can have both, e.g. a real
    "Massively Hypersonic+" speed AND a separate "exists everywhere as
    a concept" claim) - so this appends rather than overrides."""
    base = format_stat_cell(range_dict)
    if not is_omnipresent:
        return base
    return f"{base} + Omnipresent" if base != "Unknown" else "Omnipresent (unscored)"


def extract_plot_value(normalized: dict, axis: str, mode: str):
    """mode is 'peak' or 'baseline'. Returns (value_or_None, unscored_reason_or_None)."""
    if axis == "Tier/AP":
        ap = (normalized.get("attack_potency") or {}).get(mode)
        if ap is not None:
            return ap, None
        tier = (normalized.get("tier") or {}).get(mode)
        if tier is not None:
            return tier, None
        return None, "no recognizable Tier/AP value"
    if axis == "Durability":
        val = (normalized.get("durability") or {}).get(mode)
        return (val, None) if val is not None else (None, "no recognizable Durability value")
    if axis == "Speed":
        val = (normalized.get("speed") or {}).get(mode)
        if val is not None:
            return val, None
        return None, "no recognizable Speed value"
    raise ValueError(f"unknown axis {axis!r}")


def build_comparison_rows(selected_rows: list, mode: str):
    """One dict per character, built from whichever form is currently
    selected for it (session_state, defaulting to form 0 = "Base" for
    ordinary characters). `forms` is always non-empty on normalized
    data (see normalizer.py), so this never needs an "if multi-form"
    branch - it always indexes into forms."""
    out = []
    for row in selected_rows:
        normalized = json.loads(row["normalized_json"])
        all_forms = normalized.get("forms") or []
        idx = selected_form_index(row["id"], len(all_forms))
        form = all_forms[idx]
        is_multi_form = len(all_forms) > 1
        display_name = row["name"] if form["name"] == "Base" else f"{row['name']} ({form['name']})"

        values = {}
        notes = []
        for axis in STAT_AXES:
            val, reason = extract_plot_value(form, axis, mode)
            values[axis] = val
            if reason:
                notes.append(f"{axis}: {reason}")
        if form.get("is_omnipresent"):
            # Independent of whether Speed also has a real numeric value
            # (a character can have both a stated combat speed AND a
            # separate "exists everywhere" claim) - always surfaced.
            notes.append("Speed: also tagged Omnipresent (a categorically different claim, not a number)")

        out.append({
            "char_id": row["id"],
            "name": row["name"],
            "display_name": display_name,
            "category": row["category"] or "Uncategorized",
            "form": form,
            "all_forms": all_forms,
            "is_multi_form": is_multi_form,
            "raw": json.loads(row["raw_json"]),
            "values": values,
            "notes": notes,
        })
    return out


# --- charts -------------------------------------------------------------

def render_radar(rows: list):
    fig = go.Figure()
    for r in rows:
        vals = [r["values"][axis] for axis in STAT_AXES]
        vals_closed = vals + [vals[0]]
        axes_closed = STAT_AXES + [STAT_AXES[0]]
        fig.add_trace(go.Scatterpolar(
            r=vals_closed,
            theta=axes_closed,
            name=r["display_name"],
            connectgaps=False,
            fill="toself",
            opacity=0.6,
        ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True)),
        showlegend=True,
        margin=dict(t=30, b=30),
    )
    return fig


def render_verdict(verdict: "calculator.Verdict") -> None:
    """Renders a calculator.Verdict. Mirrors calculator.format_verdict()'s
    structure (verdict line, disclaimer, stat breakdown, notes, ability
    flags) as Streamlit widgets instead of a plain-text block."""
    if verdict.composite is None:
        st.warning(
            f"**{verdict.label}** — fewer than {calculator.MIN_AXES_FOR_VERDICT} stats are "
            f"comparable between these two ({verdict.axes_used}/{len(calculator.AXES)} usable). "
            f"No meaningful verdict."
        )
    else:
        who = verdict.favored or "Neither side"
        partial = "  _(based on partial data - see breakdown below)_" if verdict.partial_data else ""
        st.markdown(f"**Verdict: {who} favored — {verdict.label} ({verdict.confidence_hint})**{partial}")
        st.caption("Heuristic estimate from normalized stats - not a calibrated win probability.")

    # Column headers deliberately stay short ("A"/"B") rather than the full
    # (often long, alias-heavy) display names - two verbose names as
    # side-by-side dataframe columns pushed the second one past the visible
    # width with no scroll indication, found by testing this live. The full
    # names are already spelled out in the "Character A/B" pickers and the
    # verdict line right above, so nothing is lost.
    st.caption(f"A = {verdict.character_a}  |  B = {verdict.character_b}")
    axis_rows = []
    for c in verdict.axis_comparisons:
        a_str = "Unscored" if c.a_value is None else f"{c.a_value:.2f} ({c.a_source})"
        b_str = "Unscored" if c.b_value is None else f"{c.b_value:.2f} ({c.b_source})"
        axis_rows.append({
            "Axis": c.axis.replace("_", " ").title(),
            "A": a_str,
            "B": b_str,
            "Advantage (A)": f"{c.advantage:+.2f}" if c.advantage is not None else "excluded",
            "Weight used": f"{c.weight_used:.2f}" if c.weight_used is not None else "—",
        })
    st.dataframe(axis_rows, use_container_width=True, hide_index=True)

    if verdict.notes:
        for n in verdict.notes:
            st.caption(f"- {n}")

    if verdict.ability_flags:
        st.markdown("**Ability flags** _(not factored into the verdict above - read before trusting it)_:")
        for f in verdict.ability_flags:
            st.caption(f"- **{f.tag}**: {', '.join(f.characters)}")


def render_bar(rows: list):
    fig = go.Figure()
    for r in rows:
        fig.add_trace(go.Bar(
            x=STAT_AXES,
            y=[r["values"][axis] for axis in STAT_AXES],
            name=r["display_name"],
        ))
    fig.update_layout(
        barmode="group",
        yaxis_title="log10-scale normalized score",
        margin=dict(t=30, b=30),
    )
    return fig


# --- sidebar: add a character -----------------------------------------------

st.sidebar.header("Add a character")
with st.sidebar.form("add_character_form", clear_on_submit=True):
    new_char_input = st.text_input("Character name or wiki URL", placeholder="e.g. Genos")
    submitted = st.form_submit_button("Fetch")

if submitted:
    query = new_char_input.strip()
    if not query:
        st.sidebar.warning("Enter a character name or URL first.")
    else:
        with st.spinner(f"Fetching {query}..."):
            try:
                html = scraper.fetch_page(query)
                source_url = scraper.page_url(query)
                stats = parser_module.parse_character(html, source=source_url)
                normalized = normalizer.normalize_character(stats)
                category = stats.origin or "Uncategorized"
                db.upsert_character(
                    name=stats.name or query,
                    source_url=source_url,
                    category=category,
                    raw=stats.to_dict(),
                    normalized=normalized.to_dict(),
                )
                # Clearing the cache changes the multiselect's `options`
                # list on this rerun. Even with an explicit widget `key`,
                # Streamlit drops the current selection when `options`
                # changes under it - so re-assert the same ids back into
                # session_state before the widget re-renders below. The
                # ids themselves stay valid (upsert never changes an
                # existing row's id), so this is safe to restore verbatim.
                previous_selection = st.session_state.get("character_selector")
                load_characters.clear()
                load_form_counts.clear()
                if previous_selection:
                    st.session_state["character_selector"] = previous_selection
                st.sidebar.success(f"Added {stats.name or query} ({category}).")
            except (scraper.FetchError, Exception) as exc:  # noqa: BLE001
                st.sidebar.error(f"Couldn't fetch {query!r}: {exc}")

st.sidebar.divider()

# --- sidebar: character picker + view controls ------------------------------

st.sidebar.header("Compare")

if st.sidebar.button("Refresh character list"):
    # Same cache-invalidation the "Add a character" flow above already
    # does automatically - exposed here on demand for anything that
    # changes the DB outside this UI (batch_scrape.py runs, one-off
    # scripts), which a long-running Streamlit process would otherwise
    # never notice since st.cache_data's cache lives in-process.
    previous_selection = st.session_state.get("character_selector")
    load_characters.clear()
    load_form_counts.clear()
    if previous_selection:
        st.session_state["character_selector"] = previous_selection
    st.sidebar.success("Character list refreshed.")

characters = load_characters()
form_counts = load_form_counts()

if not characters:
    st.sidebar.info("No characters in the database yet - add one above, or run batch_scrape.py.")
else:
    label_by_id = {c["id"]: option_label(c, form_counts) for c in characters}
    selected_ids = st.sidebar.multiselect(
        f"Pick {MIN_CHARACTERS}-{MAX_CHARACTERS} characters",
        options=list(label_by_id.keys()),
        format_func=lambda cid: label_by_id[cid],
        max_selections=MAX_CHARACTERS,
        # Explicit stable key: without one, Streamlit derives the
        # widget's identity partly from `options`, so adding a new
        # character (which changes the options list) would silently
        # reset the current selection on every rerun.
        key="character_selector",
    )
    _prune_stale_form_selections(selected_ids)

    # Per-character form picker - only rendered for characters that
    # actually have more than one form, so the common (single-"Base"-
    # form) case stays exactly as clean as it was before this existed.
    multi_form_selected = [cid for cid in selected_ids if form_counts.get(cid, 1) > 1]
    if multi_form_selected:
        st.sidebar.markdown("**Forms**")
        id_to_name = {c["id"]: c["name"] for c in characters}
        for cid in multi_form_selected:
            row = db.get_character_by_id(cid)
            forms = json.loads(row["normalized_json"]).get("forms") or []
            form_names = [f["name"] for f in forms]
            st.sidebar.selectbox(
                f"{id_to_name[cid]} — Form",
                options=range(len(form_names)),
                format_func=lambda i, names=form_names: names[i],
                key=form_select_key(cid),
            )

    chart_type = st.sidebar.radio("Chart type", ["Radar", "Bar"], horizontal=True)
    plot_mode = st.sidebar.radio(
        "Plot value", ["Peak", "Baseline"], horizontal=True,
        help="Peak = character's best stated capability. Baseline = their weakest/default stated form.",
    )

    # --- main area ---------------------------------------------------------

    st.title("Powerscale Comparator")

    if len(selected_ids) < MIN_CHARACTERS:
        st.info(f"Pick at least {MIN_CHARACTERS} characters from the sidebar to compare.")
    else:
        selected_rows = [db.get_character_by_id(cid) for cid in selected_ids]
        rows = build_comparison_rows(selected_rows, mode=plot_mode.lower())

        st.subheader(f"Comparing: {', '.join(r['display_name'] for r in rows)}")

        fig = render_radar(rows) if chart_type == "Radar" else render_bar(rows)
        st.plotly_chart(fig, use_container_width=True)

        unscored_notes = [f"**{r['display_name']}** — {'; '.join(r['notes'])}" for r in rows if r["notes"]]
        if unscored_notes:
            st.caption("⚠️ Unscored stats (shown as a gap in the chart, not zero):")
            for note in unscored_notes:
                st.caption(f"- {note}")

        st.markdown("##### Raw stats")
        table_rows = []
        for r in rows:
            form = r["form"]
            table_rows.append({
                "Character": r["display_name"],
                "Category": r["category"],
                "Tier": format_stat_cell(form.get("tier")),
                "Attack Potency": format_stat_cell(form.get("attack_potency")),
                "Durability": format_stat_cell(form.get("durability")),
                "Speed": format_speed_cell(form.get("speed"), form.get("is_omnipresent", False)),
            })
        st.dataframe(table_rows, use_container_width=True, hide_index=True)

        st.markdown("##### Details")
        detail_cols = st.columns(len(rows))
        for col, r in zip(detail_cols, rows):
            with col:
                st.markdown(f"**{r['name']}**")
                st.caption(r["category"])

                if r["is_multi_form"]:
                    with st.expander(f"All forms ({len(r['all_forms'])})"):
                        for f in r["all_forms"]:
                            marker = "→ " if f["name"] == r["form"]["name"] else ""
                            st.caption(f"{marker}**{f['name']}**: {format_stat_cell(f.get('tier'))}")

                abilities = r["raw"].get("powers_and_abilities") or []
                with st.expander(f"Powers and Abilities ({len(abilities)})", expanded=len(abilities) <= 5):
                    if abilities:
                        for a in abilities:
                            st.markdown(f"- {a}")
                    else:
                        st.caption("None listed.")

                weaknesses = r["raw"].get("weaknesses")
                st.markdown("**Weaknesses**")
                st.write(weaknesses if weaknesses else "_None listed._")

        st.divider()
        st.markdown("##### Who would win?")

        id_to_row = {r["char_id"]: r for r in rows}
        row_ids = list(id_to_row.keys())

        # Stale-selection guard, same pattern as _prune_stale_form_selections:
        # if the character comparison changed since the last rerun (one of
        # these two got deselected above), the stored id may no longer be a
        # valid option - drop it rather than let the selectbox raise.
        if st.session_state.get("matchup_a") not in row_ids:
            st.session_state.pop("matchup_a", None)

        col_a, col_b = st.columns(2)
        with col_a:
            matchup_a_id = st.selectbox(
                "Character A", options=row_ids,
                format_func=lambda cid: id_to_row[cid]["display_name"],
                key="matchup_a",
            )
        b_options = [cid for cid in row_ids if cid != matchup_a_id]
        if st.session_state.get("matchup_b") not in b_options:
            st.session_state.pop("matchup_b", None)
        with col_b:
            matchup_b_id = st.selectbox(
                "Character B", options=b_options,
                format_func=lambda cid: id_to_row[cid]["display_name"],
                key="matchup_b",
            )

        row_a, row_b = id_to_row[matchup_a_id], id_to_row[matchup_b_id]
        verdict = calculator.compare_forms(
            row_a["display_name"], row_a["form"], row_b["display_name"], row_b["form"]
        )
        verdict.ability_flags = calculator.ability_flags(row_a["raw"], row_b["raw"])
        if verdict.ability_flags:
            # Same caveat calculator.compare_characters() attaches - repeated
            # here since this app calls compare_forms()/ability_flags()
            # directly (it already has raw/normalized loaded via `rows`),
            # bypassing the wrapper that would otherwise add this note.
            verdict.notes.append(
                "Ability/weakness flags reflect each character's whole-page text, not specifically the "
                "form selected above - abilities aren't tracked per-form in the current data model."
            )
        render_verdict(verdict)
