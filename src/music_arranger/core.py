"""Deterministic propose/critique/revise arranging over notation-preserving streams.

All musical decisions use sounding pitch. Conversion to written bass pitch happens
only at the export boundary. The audit trail records every changed measure/chord.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

from music21 import (
    articulations,
    bar,
    chord,
    converter,
    dynamics,
    expressions,
    instrument,
    interval,
    key,
    metadata,
    note,
    spanner,
    stream,
    tempo,
    text,
)
from music21.duration import Duration
from music21_mcp.tools.transpose_midi import transpose_midi


def transpose_notation(score, semitones: int):
    """Use upstream transposition, correcting ambiguous chromatic key spelling.

    music21's stream transpose(-2) can spell G minor as E-sharp minor.
    An explicitly named diatonic interval preserves the expected F-minor key.
    """
    result = transpose_midi(score, semitones=semitones)
    named = interval.Interval(semitones).directedName
    for old, new in zip(
        score.recurse().getElementsByClass(key.KeySignature),
        result.recurse().getElementsByClass(key.KeySignature),
    ):
        new.activeSite.replace(new, old.transpose(named))
    return result


@dataclass(frozen=True)
class Profile:
    name: str
    low: int
    high: int
    center: int
    preferred_low: int | None = None
    preferred_high: int | None = None


CELLO = Profile("Cello", 36, 81, 57, 48, 69)  # Prefer C3–A4 within advanced C2–A5.
BASS = Profile("Double bass", 28, 67, 43, 28, 50)  # Prefer E1–D3, not solo E1–G4.
PROFILES = (CELLO, BASS)
VARIANTS = {
    "reference": {
        "interval": 0,
        "repair": False,
        "reduce": False,
        "description": "G minor; both source parts one octave lower, original chords retained",
    },
    "chamber": {
        "interval": 0,
        "repair": True,
        "reduce": True,
        "description": "G minor; register optimization and single-note bass line",
    },
    "joint-chamber": {
        "interval": 0,
        "repair": True,
        "reduce": True,
        "joint": True,
        "description": "G minor; paired register search with spacing and source-contour costs",
    },
    "literature-chamber": {
        "interval": 0,
        "repair": True,
        "reduce": True,
        "joint": True,
        "literature": True,
        "description": "G minor; cited spectral/streaming models and necessary double-stop string constraints",
    },
    "study-d-minor": {
        "interval": -5,
        "repair": True,
        "reduce": True,
        "description": "D minor; outer-voice reduction in both parts and register optimization",
    },
}


def load_score(path: str | Path, expected_parts: int | None = 2) -> stream.Score:
    path = Path(path)
    if path.suffix.lower() not in {".xml", ".musicxml", ".mxl", ".mid", ".midi"}:
        raise ValueError(
            "Use MusicXML/MXL (preferred) or MIDI. Scanned PDFs need OMR first."
        )
    score = converter.parse(path)
    if (
        not isinstance(score, stream.Score)
        or not score.parts
        or (expected_parts is not None and len(score.parts) != expected_parts)
    ):
        raise ValueError(
            "This duo arranger requires exactly two parts, upper then lower."
            if expected_parts == 2
            else f"Expected a score with {expected_parts or 'one or more'} parts."
        )
    if path.suffix.lower() in {".xml", ".musicxml", ".mxl"}:
        from .ingest import restore_sound_tempos

        restore_sound_tempos(score, path)
    score = score.toSoundingPitch(inPlace=False)
    for part in score.parts:
        if not part.getElementsByClass(stream.Measure):
            part.makeMeasures(inPlace=True)
        # MusicXML may omit silent padding inside secondary voices. Make those
        # gaps explicit and hidden so export does not silently introduce rests.
        for measure in part.getElementsByClass(stream.Measure):
            for voice in measure.voices:
                voice.makeRests(
                    refStreamOrTimeRange=[0, measure.highestTime],
                    fillGaps=True,
                    hideRests=True,
                    inPlace=True,
                )
    if len({len(p.getElementsByClass(stream.Measure)) for p in score.parts}) != 1:
        raise ValueError(
            "Source parts have different measure counts; repair the source first."
        )
    timelines = [
        [
            (m.number, m.offset, m.highestTime)
            for m in p.getElementsByClass(stream.Measure)
        ]
        for p in score.parts
    ]
    if any(t != timelines[0] for t in timelines[1:]):
        mismatches = [
            items for items in zip(*timelines) if any(x != items[0] for x in items[1:])
        ]
        raise ValueError(f"Source parts have unaligned measures: {mismatches[:5]}")
    return score


def events(part):
    return list(part.recurse().notes)


def pitches(element):
    return list(element.pitches)


def has_outgoing_tie(element):
    components = element.notes if isinstance(element, chord.Chord) else [element]
    return any(n.tie and n.tie.type in ("start", "continue") for n in components)


def inspect_score(score: stream.Score) -> dict:
    result = []
    for p in score.parts:
        ns = events(p)
        ps = [q.midi for n in ns for q in pitches(n)]
        result.append(
            {
                "name": p.partName,
                "measures": len(p.getElementsByClass(stream.Measure)),
                "events": len(ns),
                "pitches": len(ps),
                "range_midi": [min(ps), max(ps)] if ps else [],
                "quarter_length": float(p.highestTime),
                "chords": sum(isinstance(n, chord.Chord) for n in ns),
            }
        )
    return {"parts": result, "sounding_pitch": score.atSoundingPitch}


def critique(score: stream.Score) -> dict:
    issues = []
    for part, profile in zip(score.parts, PROFILES):
        previous = None
        for m in part.getElementsByClass(stream.Measure):
            for n in m.recurse().notes:
                ps = pitches(n)
                outside = [
                    p.nameWithOctave
                    for p in ps
                    if not profile.low <= p.midi <= profile.high
                ]
                if outside:
                    issues.append(
                        {
                            "kind": "range",
                            "part": profile.name,
                            "measure": m.number,
                            "pitches": outside,
                        }
                    )
                if isinstance(n, chord.Chord) and (len(ps) > 2 or profile == BASS):
                    issues.append(
                        {
                            "kind": "double_stop_review",
                            "part": profile.name,
                            "measure": m.number,
                            "pitches": [p.nameWithOctave for p in ps],
                        }
                    )
                lead = (
                    max(p.midi for p in ps)
                    if profile == CELLO
                    else min(p.midi for p in ps)
                )
                if previous and abs(lead - previous[0]) > 12 and previous[1] <= 0.5:
                    issues.append(
                        {
                            "kind": "fast_leap",
                            "part": profile.name,
                            "measure": m.number,
                            "semitones": abs(lead - previous[0]),
                        }
                    )
                previous = (lead, float(n.quarterLength))
    counts = {
        k: sum(i["kind"] == k for i in issues)
        for k in ("range", "double_stop_review", "fast_leap")
    }
    return {
        "counts": counts,
        "issues": issues,
        "limitations": "Range and leap checks are heuristics, not fingering, bowing or performance certification.",
    }


def reduce_chords(part, role: int, changes: list):
    for m in part.getElementsByClass(stream.Measure):
        voices = list(m.voices)
        if len(voices) > 1:

            def voice_priority(v):
                ns = list(v.recurse().notes)
                coverage = sum(n.quarterLength for n in ns)
                ps = [p.midi for n in ns for p in n.pitches]
                center = sum(ps) / len(ps) if ps else 0
                return coverage, center if role == 0 else -center

            # Keep a complete musical strand rather than switching on every
            # crossing. When equally complete, select the outer register.
            selected_voice = max(voices, key=voice_priority)
            retained = list(selected_voice.recurse().notes)
            for voice in voices:
                if voice is selected_voice:
                    continue
                for old in voice.recurse().notes:
                    if retained:
                        target = min(retained, key=lambda n: abs(n.offset - old.offset))
                        for sp in old.getSpannerSites():
                            sp.replaceSpannedElement(old, target)
                changes.append(
                    {
                        "action": "reduce_independent_voice",
                        "part": role,
                        "measure": m.number,
                        "removed_voice": str(voice.id),
                        "kept_voice": str(selected_voice.id),
                        "removed_pitches": sum(
                            len(n.pitches) for n in voice.recurse().notes
                        ),
                    }
                )
                m.remove(voice)
        for c in list(m.recurse().getElementsByClass(chord.Chord)):
            parent = c.activeSite
            selected = (
                max(c.notes, key=lambda n: n.pitch.midi)
                if role == 0
                else min(c.notes, key=lambda n: n.pitch.midi)
            )
            n = copy.deepcopy(selected)
            n.duration = copy.deepcopy(c.duration)
            n.beams = copy.deepcopy(c.beams)
            n.articulations = copy.deepcopy(c.articulations)
            n.expressions = copy.deepcopy(c.expressions)
            n.style = copy.deepcopy(c.style)
            for sp in c.getSpannerSites():
                sp.replaceSpannedElement(c, n)
            changes.append(
                {
                    "action": "reduce_chord",
                    "part": role,
                    "measure": m.number,
                    "before": [p.nameWithOctave for p in c.pitches],
                    "after": n.pitch.nameWithOctave,
                }
            )
            parent.replace(c, n)


def _octave_options(ps, profile):
    """Every octave placing some source pitch in the destination range."""
    if not ps:
        return (0,)
    return tuple(
        12 * octave
        for octave in range(
            math.ceil((profile.low - max(ps)) / 12),
            math.floor((profile.high - min(ps)) / 12) + 1,
        )
    )


def _register_cost(pitch, duration, profile):
    """Absolute destination tessitura, independent of the source's octave."""
    low = profile.low if profile.preferred_low is None else profile.preferred_low
    high = profile.high if profile.preferred_high is None else profile.preferred_high
    physical = max(profile.low - pitch, 0, pitch - profile.high)
    preferred = max(low - pitch, 0, pitch - high)
    return max(float(duration), 0.125) * (
        1000 * physical + 2 * preferred**2 + 0.04 * abs(pitch - profile.center)
    )


def optimize_register(part, profile: Profile, changes: list):
    """Dynamic programming: one octave choice per bar, constrained across ties.

    This preserves every interval inside a bar. Costs penalize range violations,
    large boundary leaps, register extremes and unnecessary octave changes.
    """
    measures = list(part.getElementsByClass(stream.Measure))
    options = _octave_options(
        [p.midi for n in part.recurse().notes for p in n.pitches], profile
    )
    states = {shift: (0.0, []) for shift in options}
    last_notes = []
    for m in measures:
        ns = list(m.recurse().notes)
        ps = [p.midi for n in ns for p in n.pitches]
        tied = any(has_outgoing_tie(n) for n in last_notes[-1:])
        new = {}
        for shift in options:
            if ps and min(ps) + shift < profile.low:
                continue  # Physical open-string floor is a hard constraint.
            local = sum(
                _register_cost(p.midi + shift, n.quarterLength, profile)
                for n in ns
                for p in n.pitches
            )
            best = None
            for prev, (cost, history) in states.items():
                if tied and prev != shift:
                    continue
                boundary = 0
                if ns and last_notes:
                    a = (
                        sum(p.midi for p in last_notes[-1].pitches)
                        / len(last_notes[-1].pitches)
                        + prev
                    )
                    b = sum(p.midi for p in ns[0].pitches) / len(ns[0].pitches) + shift
                    boundary = max(0, abs(b - a) - 7) * 2
                candidate = (
                    cost + local + boundary + (4 if prev != shift else 0),
                    history + [shift],
                )
                if best is None or candidate[0] < best[0]:
                    best = candidate
            if best:
                new[shift] = best
        states = new
        if not states:
            raise ValueError(
                f"No register choice respects the lowest string and ties in {profile.name}, measure {m.number}"
            )
        last_notes = ns
    shifts = min(states.values(), key=lambda x: x[0])[1]
    for m, shift in zip(measures, shifts):
        if shift:
            for n in m.recurse().notes:
                n.transpose(shift, inPlace=True)
            changes.append(
                {
                    "action": "octave_register",
                    "part": profile.name,
                    "measure": m.number,
                    "semitones": shift,
                }
            )


def _timed_notes(measure):
    return [
        (float(n.getOffsetInHierarchy(measure)), float(n.quarterLength), n)
        for n in measure.recurse().notes
    ]


def _spacing_cost(upper, lower, shifts):
    """Register-sensitive voicing preference, not an acoustic roughness model.

    Evaluate sounding overlap, including held notes against moving notes. Preserve
    source crossings: only a change in their direction gets a crossing penalty.
    Low thirds/seconds need more separation than fifths; a tenth is not folded
    back into a third before evaluating spacing. Grace notes have no exposure.
    """
    cost = 0.0
    for start_a, duration_a, a in upper:
        for start_b, duration_b, b in lower:
            overlap = min(start_a + duration_a, start_b + duration_b) - max(
                start_a, start_b
            )
            if overlap <= 0:
                continue
            for pa in a.pitches:
                for pb in b.pitches:
                    original = pa.midi - pb.midi
                    gap = original + shifts[0] - shifts[1]
                    bottom = min(pa.midi + shifts[0], pb.midi + shifts[1])
                    distance = abs(gap)
                    # These are editable arranging preferences, not universal
                    # lower-interval limits or a double-bass technique model.
                    floor = (
                        50 if distance in (1, 2) else 48 if distance in (3, 4) else 43
                    )
                    crowding = max(0, floor - bottom) / 12 if 0 < distance < 12 else 0
                    wide = max(0, distance - 24) / 12
                    crossing = 2 if original * gap < 0 else 0
                    cost += overlap * (crowding * 6 + wide * 2 + crossing * 6)
    return cost


def optimize_joint_register(
    score,
    changes: list,
    continuity: float = 1.0,
    rapid_motion: float = 0.0,
    literature: bool = False,
):
    """Search paired bar registers; never consult the independent judge.

    Nine paired choices per measure, with hard lowest-string and tie constraints.
    The melodic cost compares boundary movement to the original strand rather
    than treating every small final leap as an improvement. A second iteration
    can increase continuity without altering the evaluator's frozen rubric.
    """
    from itertools import product

    from .rules import (
        CONFIG,
        ScoreClock,
        bowing_modes,
        harmonic_notation,
        proximity_cost,
        sonority_cost,
        string_assignments,
    )

    bars = [list(p.getElementsByClass(stream.Measure)) for p in score.parts]
    if len(bars) != 2 or len(bars[0]) != len(bars[1]):
        raise ValueError("Paired register search requires aligned duo measures")
    states = {(0, 0): (0.0, [])}
    previous = [[], []]
    previous_offsets = (0.0, 0.0)
    clock = ScoreClock(score) if literature else None
    modes = [bowing_modes(part) for part in score.parts] if literature else None
    for pair in zip(*bars):
        timed = [_timed_notes(m) for m in pair]
        pitch_sets = [[p.midi for _, _, n in ns for p in n.pitches] for ns in timed]
        next_states = {}
        for shifts in product((-12, 0, 12), repeat=2):
            if any(
                ps and min(ps) + shift < profile.low
                for ps, shift, profile in zip(pitch_sets, shifts, PROFILES)
            ):
                continue
            if literature:
                # Necessary compatibility for ordinary simultaneous double
                # stops. Rolled 3/4-note chords and harmonic notation are review
                # items, never blindly classified as impossible here.
                if any(
                    len(n.pitches) == 2
                    and not harmonic_notation(n)
                    and not string_assignments(
                        [p.midi + shifts[role] for p in n.pitches],
                        role,
                        adjacent=modes[role][id(n)] == "arco",
                    )
                    for role, ns in enumerate(timed)
                    for _, _, n in ns
                ):
                    continue
                local = sonority_cost(timed, shifts)
                # Crossing/contour weights remain OUR arranging preferences.
                # Do not smuggle the old register thresholds into this model.
                for sa, da, a in timed[0]:
                    for sb, db, b in timed[1]:
                        overlap = min(sa + da, sb + db) - max(sa, sb)
                        if overlap > 0:
                            old = max(p.midi for p in a.pitches) - min(
                                p.midi for p in b.pitches
                            )
                            if old * (old + shifts[0] - shifts[1]) < 0:
                                local += overlap * 12
            else:
                local = _spacing_cost(*timed, shifts)
            for ns, shift, profile in zip(timed, shifts, PROFILES):
                for _, duration, n in ns:
                    for p in n.pitches:
                        local += max(duration, 0.125) * (
                            1000 * max(0, p.midi + shift - profile.high)
                            + 0.025 * abs(p.midi + shift - profile.center)
                        )
                local += abs(shift) * 0.15
            for prev_shifts, (cost, history) in states.items():
                transition = 0.0
                legal = True
                for role, (before, now) in enumerate(zip(previous, timed)):
                    delta = shifts[role] - prev_shifts[role]
                    # Check every voice at the boundary, including partially
                    # tied chords; an outgoing tie in an earlier voice matters.
                    if before:
                        end = max(t + d for t, d, _ in before)
                        tied = any(
                            abs(t + d - end) < 1e-7 and has_outgoing_tie(n)
                            for t, d, n in before
                        )
                        if tied and delta:
                            legal = False
                            break
                    if before and now:
                        pick = max if role == 0 else min
                        # Use timed notes for speed; zero-duration ornaments
                        # cannot supply a meaningful movement-time estimate.
                        timed_before = (
                            ([x for x in before if x[1] > 0] or before)
                            if rapid_motion or literature
                            else before
                        )
                        timed_now = (
                            ([x for x in now if x[1] > 0] or now)
                            if rapid_motion or literature
                            else now
                        )
                        last = max(timed_before, key=lambda x: x[0])
                        first = min(timed_now, key=lambda x: x[0])
                        original = pick(p.midi for p in first[2].pitches) - pick(
                            p.midi for p in last[2].pitches
                        )
                        moved = original + delta
                        if literature:
                            previous_start = previous_offsets[role] + last[0]
                            previous_end = previous_start + last[1]
                            next_start = float(pair[role].offset) + first[0]
                            local_duration = clock.seconds(previous_start, previous_end)
                            silence = clock.seconds(previous_end, next_start)
                            transition += CONFIG.proximity_weight * proximity_cost(
                                moved, local_duration, silence
                            )
                        if last[1] <= 0.5:
                            transition += rapid_motion * max(0, abs(moved) - 7)
                        if delta:
                            transition += continuity * (
                                abs(delta) * 0.5
                                + (8 if original * moved < 0 else 0)
                                + max(0, abs(moved) - 7) * 2
                            )
                    if delta:
                        transition += continuity * 4
                if not legal:
                    continue
                candidate = (cost + local + transition, history + [shifts])
                if shifts not in next_states or candidate[0] < next_states[shifts][0]:
                    next_states[shifts] = candidate
        if not next_states:
            raise ValueError(
                f"No paired register choice respects floors/ties at bar {pair[0].number}"
            )
        states = next_states
        previous = timed
        previous_offsets = tuple(float(m.offset) for m in pair)
    objective, history = min(states.values(), key=lambda x: x[0])
    for pair, shifts in zip(zip(*bars), history):
        timed = [_timed_notes(m) for m in pair]
        before = (
            sonority_cost(timed, (0, 0))
            if literature
            else _spacing_cost(*timed, (0, 0))
        )
        after = (
            sonority_cost(timed, shifts)
            if literature
            else _spacing_cost(*timed, shifts)
        )
        for m, profile, shift in zip(pair, PROFILES, shifts):
            if shift:
                for n in m.recurse().notes:
                    n.transpose(shift, inPlace=True)
                changes.append(
                    {
                        "action": "octave_register",
                        "optimizer": "joint",
                        "spacing_model": "sethares-six-harmonics"
                        if literature
                        else "heuristic-v1",
                        "part": profile.name,
                        "measure": m.number,
                        "semitones": shift,
                        "paired_shifts": list(shifts),
                        "spacing_cost_before": round(before, 6),
                        "spacing_cost_after": round(after, 6),
                    }
                )
    return {
        "objective": objective,
        "continuity_weight": continuity,
        "rapid_motion_weight": rapid_motion,
        "states_per_bar": 9,
        "literature_rules": literature,
    }


def set_instruments(score):
    cleanup = []
    for idx, part in enumerate(score.parts):
        for obj in list(part.recurse().getElementsByClass(instrument.Instrument)):
            obj.activeSite.remove(obj)
        inst = instrument.Violoncello() if idx == 0 else instrument.Contrabass()
        part.insert(0, inst)
        part.partName = inst.instrumentName = PROFILES[idx].name
        part.partAbbreviation = "Vc." if idx == 0 else "Cb."
        part.atSoundingPitch = True
        # Source fingerings/string numbers cannot transfer safely between tunings.
        for n in part.recurse().notes:
            removed = [
                type(a).__name__
                for a in n.articulations
                if isinstance(
                    a, (articulations.Fingering, articulations.StringIndication)
                )
            ]
            if removed:
                cleanup.append(
                    {
                        "action": "remove_instrument_specific_marks",
                        "part": idx,
                        "measure": n.measureNumber,
                        "marks": removed,
                    }
                )
            n.articulations = [
                a
                for a in n.articulations
                if not isinstance(
                    a, (articulations.Fingering, articulations.StringIndication)
                )
            ]
    score.atSoundingPitch = True
    return cleanup


def arrange(
    source: stream.Score,
    variant: str,
    *,
    continuity: float = 1.0,
    rapid_motion: float = 0.0,
) -> tuple[stream.Score, dict]:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant {variant}; choose {list(VARIANTS)}")
    policy = VARIANTS[variant]
    base_title = (
        (source.metadata.title or source.metadata.movementName or "Untitled")
        if source.metadata
        else "Untitled"
    )
    old_composer = source.metadata.composer if source.metadata else None
    composer = (
        "G. F. Handel / Johan Halvorsen"
        if base_title.lower() == "passacaglia"
        else old_composer or "Unknown composer"
    )
    title = f"{base_title} — {variant}"
    score = transpose_notation(source, semitones=-12 + policy["interval"])
    cleanup = set_instruments(score)
    changes = [
        {
            "action": "global_transpose",
            "semitones": -12 + policy["interval"],
            "tool": "music21_mcp.transpose_midi",
        }
    ]
    changes.extend(cleanup)
    trace = [{"stage": "propose", "critique": critique(score)}]
    if policy["reduce"]:
        reduce_chords(score.parts[1], 1, changes)
        if variant == "study-d-minor":
            reduce_chords(score.parts[0], 0, changes)
        trace.append({"stage": "reduce_chords", "critique": critique(score)})
    if policy["repair"]:
        if policy.get("joint"):
            search = optimize_joint_register(
                score,
                changes,
                continuity=continuity,
                rapid_motion=rapid_motion,
                literature=policy.get("literature", False),
            )
            trace.append(
                {
                    "stage": "optimize_joint_register",
                    "search": search,
                    "critique": critique(score),
                }
            )
        else:
            for part, profile in zip(score.parts, PROFILES):
                optimize_register(part, profile, changes)
            trace.append({"stage": "optimize_register", "critique": critique(score)})
    # Voice reduction can collapse a slur/hairpin onto one event or remove an
    # endpoint. Drop only those now-meaningless spans, explicitly in the audit.
    positions = {
        id(n): (i, n.getOffsetInHierarchy(part))
        for i, part in enumerate(score.parts)
        for n in part.recurse().notesAndRests
    }
    for span in list(score.spannerBundle):
        if not isinstance(span, (spanner.Slur, dynamics.DynamicWedge)):
            continue
        endpoints = list(span.getSpannedElements())
        locs = [positions.get(id(n)) for n in endpoints]
        invalid = (
            None in locs
            or not locs
            or (isinstance(span, spanner.Slur) and len(locs) < 2)
            or (len(locs) >= 2 and locs[0] > locs[-1])
        )
        if invalid:
            changes.append(
                {
                    "action": "remove_collapsed_span",
                    "type": type(span).__name__,
                    "locations": [str(x) for x in locs],
                }
            )
            if span.activeSite is not None:
                span.activeSite.remove(span)
    score.metadata = metadata.Metadata()
    for box in list(score.recurse().getElementsByClass(text.TextBox)):
        if box.content == base_title:
            box.content = title
        elif (old_composer and box.content == old_composer) or (
            base_title.lower() == "passacaglia" and "Handel" in box.content
        ):
            box.content = composer
    score.metadata.title = title
    score.metadata.composer = composer
    score.metadata.movementName = title
    score.metadata.add(
        "otherContributor", "Computational arrangement; source transcription via PDMX"
    )
    score.parts[1].getElementsByClass(stream.Measure).first().insert(
        0,
        expressions.TextExpression("Double bass sounds one octave below written pitch"),
    )
    report = {
        "variant": variant,
        "policy": policy,
        "profiles_sounding": [asdict(p) for p in PROFILES],
        "source": inspect_score(source),
        "result": inspect_score(score),
        "iterations": trace,
        "changes": changes,
        "source_tempo_repairs": source.editorial.get("sound_tempo_repairs", []),
        "engraving": "Destination clefs and automatic stems; intermediate dynamics in bars with 3+ dynamic marks omitted from printed MusicXML for legibility (MIDI playback retained).",
    }
    if policy.get("literature"):
        from .rules import audit_rules

        report["literature_rules"] = audit_rules(score)
    source_path = getattr(source.metadata, "filePath", None)
    if source_path and Path(source_path).is_file():
        report["input"] = {
            "path": str(Path(source_path).resolve()),
            "sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
        }
    return score, report


def written_score(score):
    from .render import prepare_written_notation

    out = score.toWrittenPitch(inPlace=False)
    prepare_written_notation(out)
    return out


def fingerprint(score):
    """Sounding pitches, rhythms and ties, independent of engraving IDs."""
    return [
        [
            (
                m.number,
                float(n.getOffsetInHierarchy(m)),
                float(n.quarterLength),
                tuple((p.nameWithOctave) for p in n.pitches),
                tuple(
                    (c.tie.type if c.tie else None)
                    for c in (n.notes if isinstance(n, chord.Chord) else [n])
                ),
            )
            for m in part.getElementsByClass(stream.Measure)
            for n in m.recurse().notesAndRests
        ]
        for part in score.parts
    ]


def extract_part(written, index):
    """Standalone part, with score credits and shared tempo cues copied over."""
    single = stream.Score()
    single.metadata = copy.deepcopy(written.metadata)
    part = copy.deepcopy(written.parts[index])
    single.insert(0, part)
    single.atSoundingPitch = False
    for box in written.getElementsByClass(text.TextBox):
        single.insert(0, copy.deepcopy(box))
    if index:
        for measure in written.parts[0].getElementsByClass(stream.Measure):
            target = part.measure(measure.number)
            for cue in measure.recurse().getElementsByClass(tempo.TempoIndication):
                offset = cue.getOffsetInHierarchy(measure)
                existing = list(target.recurse().getElementsByClass(type(cue)))
                if not any(c.getOffsetInHierarchy(target) == offset for c in existing):
                    target.insert(offset, copy.deepcopy(cue))
    return single


def prepare_midi_playback(score):
    """Realize grace notes on a separate, repeat-expanded performance copy.

    Editorial audition policy, not a claim about historical ornament practice:
    play each grace for up to 1/8 quarter, spending at most 1/4 quarter and
    half the anchor's duration on the group. Delay/shorten the anchor so its
    end and every subsequent beat stay fixed. Never modify printed notation.
    Unsupported unanchored or incoming-tie ornaments fail explicitly.
    """
    playback = score.expandRepeats()
    changes = []
    for part_index, part in enumerate(playback.parts):
        for bar_index, measure in enumerate(part.getElementsByClass(stream.Measure)):
            containers = [measure, *measure.recurse().getElementsByClass(stream.Voice)]
            for container in containers:
                events = list(container.notesAndRests)
                groups = {}
                for n in events:
                    if n.duration.isGrace:
                        groups.setdefault(n.offset, []).append(n)
                    elif n.quarterLength <= 0:
                        raise ValueError(
                            "Cannot export a non-grace event with zero duration"
                        )
                for onset, graces in groups.items():
                    anchors = [
                        n for n in events if n.offset == onset and n.quarterLength > 0
                    ]
                    context = (
                        f"part {part_index + 1}, bar {measure.number}, offset {onset}"
                    )
                    if len(anchors) != 1:
                        raise ValueError(
                            f"Grace playback needs one same-voice anchor: {context}"
                        )
                    anchor = anchors[0]
                    tones = (
                        anchor.notes if isinstance(anchor, chord.Chord) else [anchor]
                    )
                    if any(n.tie and n.tie.type in {"stop", "continue"} for n in tones):
                        raise ValueError(
                            f"Grace playback cannot interrupt an incoming tie: {context}"
                        )
                    window = min(
                        Fraction(1, 8) * len(graces),
                        Fraction(1, 4),
                        Fraction(anchor.quarterLength) / 2,
                    )
                    step = window / len(graces)
                    anchor.quarterLength -= window
                    container.setElementOffset(anchor, onset + window)
                    for index, n in enumerate(graces):
                        n.duration = Duration(step)
                        container.setElementOffset(n, onset + index * step)
                    changes.append(
                        {
                            "part": part_index + 1,
                            "measure": measure.number,
                            "expanded_bar": bar_index + 1,
                            "offset": float(onset),
                            "grace_notes": len(graces),
                            "grace_pitches": [
                                [p.nameWithOctave for p in n.pitches] for n in graces
                            ],
                            "borrowed_quarters": float(window),
                        }
                    )
    return playback, changes


def interpret_playback_tempos(playback, mode="encoded"):
    """Optionally interpolate encoded ritardando anchors, never invent rubato.

    The smooth-rit preview uses linear seconds/quarter between numeric anchors
    in the same bar, sampled every 1/32 quarter. This is an editorial audition
    policy; numeric anchor values and printed notation remain authoritative.
    """
    if mode not in {"encoded", "smooth-rit"}:
        raise ValueError("Tempo mode must be encoded or smooth-rit")
    part = playback.parts[0]
    marks = sorted(
        (float(m.getOffsetInHierarchy(part)), m.getQuarterBPM())
        for m in part.recurse().getElementsByClass(tempo.MetronomeMark)
        if m.getQuarterBPM() is not None
    )
    audit = []
    for bar_index, measure in enumerate(part.getElementsByClass(stream.Measure)):
        seen = set()
        for text_mark in list(
            measure.recurse().getElementsByClass(expressions.TextExpression)
        ):
            content = text_mark.content
            rit = bool(
                re.match(r"\s*(rit\b|ritard|rall\b|rallent)", content, re.IGNORECASE)
            )
            expressive = rit or re.search(
                r"rubato|ad\.?\s*lib|and\s+lib|accel|fermata", content, re.IGNORECASE
            )
            if not expressive:
                continue
            start = float(text_mark.getOffsetInHierarchy(part))
            if (start, content) in seen:
                continue
            seen.add((start, content))
            entry = {
                "measure": measure.number,
                "expanded_bar": bar_index + 1,
                "offset": float(text_mark.getOffsetInHierarchy(measure)),
                "text": content,
                "status": "uninterpreted_text",
            }
            previous = [bpm for at, bpm in marks if at <= start + 1e-7]
            end = float(measure.offset + measure.highestTime)
            anchors = [(at, bpm) for at, bpm in marks if start + 1e-7 < at < end - 1e-7]
            if rit and previous and anchors:
                points = [(start, previous[-1]), *anchors]
                slowing = (
                    all(b <= a for (_, a), (_, b) in pairwise(points))
                    and points[-1][1] < points[0][1]
                )
                if slowing:
                    entry["status"] = (
                        "encoded_steps"
                        if mode == "encoded"
                        else "interpolated_numeric_anchors"
                    )
                    entry["anchors"] = [
                        {"offset": at - float(measure.offset), "quarter_bpm": bpm}
                        for at, bpm in points
                    ]
                    if mode == "smooth-rit":
                        for (left, bpm_left), (right, bpm_right) in pairwise(points):
                            at = left + 1 / 32
                            while at < right - 1e-7:
                                fraction = (at - left) / (right - left)
                                bpm = 60 / (
                                    (1 - fraction) * 60 / bpm_left
                                    + fraction * 60 / bpm_right
                                )
                                measure.insert(
                                    at - float(measure.offset),
                                    tempo.MetronomeMark(numberSounding=bpm),
                                )
                                at += 1 / 32
            # A numeric change exactly at a rit marking is already encoded,
            # but gives no interval over which to infer a continuous curve.
            if (
                rit
                and entry["status"] == "uninterpreted_text"
                and any(abs(at - start) < 1e-7 for at, _ in marks)
            ):
                entry["status"] = "numeric_change_at_text_only"
            audit.append(entry)
    for part_index, part in enumerate(playback.parts):
        for bar_index, measure in enumerate(part.getElementsByClass(stream.Measure)):
            for event in measure.recurse().notesAndRests:
                unperformed = [
                    type(x).__name__
                    for x in [*event.expressions, *event.articulations]
                    if isinstance(
                        x,
                        (
                            expressions.Fermata,
                            articulations.Caesura,
                            articulations.BreathMark,
                        ),
                    )
                ]
                for name in unperformed:
                    audit.append(
                        {
                            "part": part_index + 1,
                            "measure": measure.number,
                            "expanded_bar": bar_index + 1,
                            "offset": float(event.getOffsetInHierarchy(measure)),
                            "text": name,
                            "status": "uninterpreted_hold_or_pause",
                        }
                    )
    return {
        "mode": mode,
        "directions": audit,
        "limitations": "Unspecified rubato, ad libitum, fermata holds and bow/tongue phrasing are not synthesized. No arbitrary slowing of fast tuplets.",
    }


def export(score, report, directory: Path, render=True):
    from .playback import write_midi_preview
    from .render import render_pdf

    part_files = report.get("part_files", ("cello", "double-bass"))
    input_path = report.get("input", {}).get("path")
    if input_path and Path(input_path).resolve() in {
        (directory / name).resolve()
        for name in ("score.musicxml", *(f"{name}.musicxml" for name in part_files))
    }:
        raise ValueError(
            "Choose a separate output directory; this would overwrite the input score."
        )
    directory.mkdir(parents=True, exist_ok=True)
    written = written_score(score)
    xml = directory / "score.musicxml"
    written.write("musicxml", fp=xml)
    reloaded = load_score(xml, expected_parts=len(score.parts))
    if fingerprint(score) != fingerprint(reloaded):
        raise ValueError(f"MusicXML sounding-pitch/rhythm/tie round trip failed: {xml}")
    report["roundtrip_verified"] = True
    for original_part, loaded_part in zip(score.parts, reloaded.parts):
        original_marks = list(
            original_part.recurse().getElementsByClass(tempo.MetronomeMark)
        )
        loaded_marks = list(
            loaded_part.recurse().getElementsByClass(tempo.MetronomeMark)
        )
        if len(original_marks) != len(loaded_marks) or any(
            abs(
                float(
                    a.getOffsetInHierarchy(original_part)
                    - b.getOffsetInHierarchy(loaded_part)
                )
            )
            > 1e-3
            or abs(a.getQuarterBPM() - b.getQuarterBPM()) > 1e-6
            for a, b in zip(original_marks, loaded_marks)
            if a.getQuarterBPM() is not None and b.getQuarterBPM() is not None
        ):
            raise ValueError(f"MusicXML playback tempo round trip failed: {xml}")
    report["tempo_roundtrip_verified"] = True
    report["musicxml_sha256"] = hashlib.sha256(xml.read_bytes()).hexdigest()
    # MIDI is an audition aid; expand printed repeats for a complete performance.
    report["midi_playback"] = write_midi_preview(score, directory / "score.mid")
    files = [xml]
    for index, name in enumerate(part_files):
        single = extract_part(written, index)
        path = directory / f"{name}.musicxml"
        single.write("musicxml", fp=path)
        files.append(path)
    if render:
        report["rendering"] = {f.stem: render_pdf(f) for f in files}
    (directory / "report.json").write_text(json.dumps(report, indent=2))
    return {
        "directory": str(directory.resolve()),
        "files": [str(p.resolve()) for p in directory.iterdir() if p.is_file()],
        "final_critique": report["iterations"][-1]["critique"]["counts"],
    }


TRIO_VARIANTS = {
    "sax-led": "Control: alto carries the upper melody; cello takes available inner tones.",
    "relay-four": "Exchange upper melody and inner tones in nominal four-bar phrases.",
    "relay-two": "More frequent nominal two-bar exchanges for shorter wind phrases.",
}
ALTO = Profile("Alto saxophone", 49, 80, 65, 55, 74)  # Prefer G3–D5 sounding.


def wind_direction(content):
    """Editorial equivalents for source bow directions, not acoustic equivalence."""
    lower = content.lower()
    if any(word in lower for word in ("spiccato", "spicatto", "saltando")):
        return "lightly tongued"
    if "flantando" in lower or "flautando" in lower:
        return "airy tone"
    if any(word in lower for word in ("pizz", "arco", "sul ", "corda", "ponticello")):
        return None
    return content


def _fit_trio_line(part, profile, changes):
    """Hard range repair where a whole-bar octave cannot fit a wind line."""
    ns = list(part.recurse().notes)
    states = {0: (0.0, [])}
    previous = None
    for n in ns:
        pitch = n.pitch.midi
        next_states = {}
        for shift in _octave_options([pitch], profile):
            if not profile.low <= pitch + shift <= profile.high:
                continue
            for old_shift, (cost, path) in states.items():
                if (
                    previous is not None
                    and has_outgoing_tie(previous)
                    and old_shift != shift
                ):
                    continue
                movement = 0
                if previous is not None:
                    original = pitch - previous.pitch.midi
                    actual = original + shift - old_shift
                    movement = (
                        2 * max(0, abs(actual) - 7) ** 2
                        + 0.5 * abs(shift - old_shift)
                        + (8 if original * actual < 0 else 0)
                    )
                candidate = (
                    cost
                    + movement
                    + _register_cost(pitch + shift, n.quarterLength, profile),
                    path + [shift],
                )
                if shift not in next_states or candidate[0] < next_states[shift][0]:
                    next_states[shift] = candidate
        if not next_states:
            raise ValueError(
                f"No tied in-range pitch for {profile.name} at bar {n.measureNumber}"
            )
        states, previous = next_states, n
    for n, shift in zip(ns, min(states.values(), key=lambda value: value[0])[1]):
        if shift:
            before = n.pitch.nameWithOctave
            n.transpose(shift, inPlace=True)
            changes.append(
                {
                    "action": "line_octave_repair",
                    "part": profile.name,
                    "measure": n.measureNumber,
                    "offset": float(n.offset),
                    "before": before,
                    "after": n.pitch.nameWithOctave,
                    "semitones": shift,
                }
            )


def _trio_strand(source_part, kind, changes):
    """Keep source event durations and notation while extracting one strand."""
    part = copy.deepcopy(source_part)
    for m in part.getElementsByClass(stream.Measure):
        voices = list(m.voices)
        if len(voices) > 1:

            def priority(v):
                ns = list(v.recurse().notes)
                ps = [p.midi for n in ns for p in n.pitches]
                return sum(n.quarterLength for n in ns), (
                    sum(ps) / len(ps) if ps else 0
                ) * (-1 if kind == "bass" else 1)

            keep = max(voices, key=priority)
            for v in voices:
                if v is not keep:
                    m.remove(v)
        for old in list(m.recurse().notes):
            parent = old.activeSite
            components = sorted(
                old.notes if isinstance(old, chord.Chord) else [old],
                key=lambda n: n.pitch.midi,
            )
            selected = (
                components[-1]
                if kind == "lead"
                else components[0]
                if kind == "bass" or len(components) > 1
                else None
            )
            new = copy.deepcopy(selected) if selected else note.Rest()
            new.duration = copy.deepcopy(old.duration)
            new.expressions = copy.deepcopy(old.expressions)
            if selected:
                new.beams = copy.deepcopy(old.beams)
                new.articulations = copy.deepcopy(old.articulations)
                new.style = copy.deepcopy(old.style)
            for span in old.getSpannerSites():
                span.replaceSpannedElement(old, new)
            parent.replace(old, new)
    before_rests = len(part.recurse().getElementsByClass(note.Rest))
    stream.makeNotation.consolidateCompletedTuplets(part, recurse=True)
    after_rests = len(part.recurse().getElementsByClass(note.Rest))
    if before_rests != after_rests:
        changes.append(
            {
                "action": "consolidate_generated_tuplet_rests",
                "strand": kind,
                "before": before_rests,
                "after": after_rests,
                "silence_duration_unchanged": True,
            }
        )
    # Preserve only meaningful spans whose endpoints survived voice extraction.
    positions = {id(n) for n in part.recurse().notesAndRests}
    measures = {id(m) for m in part.getElementsByClass(stream.Measure)}
    for span in list(part.spannerBundle):
        endpoints = list(span.getSpannedElements())
        valid = bool(endpoints) and all(
            id(n) in positions | measures for n in endpoints
        )
        if isinstance(span, spanner.Slur):
            valid = (
                valid
                and len(endpoints) >= 2
                and all(n.isNote or n.isChord for n in endpoints)
            )
        # Ottavas are source engraving; retain already resolved sounding notes.
        if isinstance(span, (spanner.Ottava, spanner.Line)):
            valid = False
        if not valid:
            changes.append(
                {
                    "action": "omit_inapplicable_source_span",
                    "strand": kind,
                    "type": type(span).__name__,
                    "endpoint_measures": [
                        getattr(n, "measureNumber", None) for n in endpoints
                    ],
                }
            )
            if span.activeSite is not None:
                span.activeSite.remove(span)
    return part


def _trio_boundaries(parts):
    """Boundaries across which a retained tie or span must not change player."""
    blocked = set()
    for part in parts:
        measures = list(part.getElementsByClass(stream.Measure))
        positions = {
            id(obj): i for i, m in enumerate(measures) for obj in [m, *m.recurse()]
        }
        for span in part.spannerBundle:
            indices = [
                positions[id(e)]
                for e in span.getSpannedElements()
                if id(e) in positions
            ]
            if indices:
                blocked.update(range(min(indices) + 1, max(indices) + 1))
        for i, m in enumerate(measures):
            # Only ties leaving this bar block a handoff, not intra-bar ties.
            if any(
                has_outgoing_tie(n)
                and n.getOffsetInHierarchy(m) + n.quarterLength >= m.highestTime
                for n in m.recurse().notes
            ):
                blocked.add(i + 1)
    return blocked


def _ensemble_lanes(source, changes):
    """Partition pitched source events into disjoint monophonic voice/chord lanes.

    Incoming ties keep their previous lane even when chord pitch order changes.
    No event is shortened, split, reharmonized or duplicated to fill the trio.
    """
    lanes = []
    for part_index, source_part in enumerate(source.parts):
        measures = list(source_part.getElementsByClass(stream.Measure))
        voice_ids = list(dict.fromkeys(str(v.id) for m in measures for v in m.voices))
        default_voice = voice_ids[0] if voice_ids else None
        entries = {}
        for mi, m in enumerate(measures):
            for ei, event in enumerate(m.recurse().notesAndRests):
                voice = (
                    str(event.activeSite.id)
                    if isinstance(event.activeSite, stream.Voice)
                    else None
                    if m.voices
                    else default_voice
                )
                entries[(mi, ei)] = voice
        for voice in dict.fromkeys(entries.values()):
            source_events = [
                (mi, ei, n)
                for mi, m in enumerate(measures)
                for ei, n in enumerate(m.recurse().notesAndRests)
                if entries[(mi, ei)] == voice and n.pitches
            ]
            width = max((len(n.pitches) for _, _, n in source_events), default=0)
            assignments, pending = {}, {}
            end = -1
            for mi, ei, event in source_events:
                at = float(event.getOffsetInHierarchy(source_part))
                if event.quarterLength and at < end - 1e-6:
                    raise ValueError(
                        f"Overlapping notes without separate voices in source part "
                        f"{part_index + 1}, measure {measures[mi].number}"
                    )
                if event.quarterLength:
                    end = at + float(event.quarterLength)
                components = sorted(
                    event.notes if isinstance(event, chord.Chord) else [event],
                    key=lambda n: n.pitch.midi,
                    reverse=True,
                )
                used, selected = set(), {}
                for ci, component in enumerate(components):
                    old = pending.get(component.pitch.nameWithOctave)
                    if (
                        component.tie
                        and component.tie.type in ("continue", "stop")
                        and old
                        and abs(old[1] - at) < 1e-6
                    ):
                        selected[ci] = old[0]
                        used.add(old[0])
                for ci in range(len(components)):
                    if ci not in selected:
                        rank = next(r for r in range(width) if r not in used)
                        selected[ci] = rank
                        used.add(rank)
                pending = {}
                for ci, rank in selected.items():
                    component = components[ci]
                    assignments[(mi, ei, rank)] = component
                    if component.tie and component.tie.type in ("start", "continue"):
                        pending[component.pitch.nameWithOctave] = (
                            rank,
                            at + float(event.quarterLength),
                        )
            for rank in range(width):
                part = copy.deepcopy(source_part)
                for mi, m in enumerate(part.getElementsByClass(stream.Measure)):
                    duration = measures[mi].highestTime
                    for ei, old in enumerate(list(m.recurse().notesAndRests)):
                        parent = old.activeSite
                        if entries[(mi, ei)] != voice:
                            parent.remove(old)
                            continue
                        if old.isRest:
                            continue
                        selected = assignments.get((mi, ei, rank))
                        new = copy.deepcopy(selected) if selected else note.Rest()
                        new.duration = copy.deepcopy(old.duration)
                        new.expressions = copy.deepcopy(old.expressions)
                        if selected:
                            new.beams = copy.deepcopy(old.beams)
                            new.articulations = copy.deepcopy(old.articulations)
                            new.style = copy.deepcopy(old.style)
                            new.editorial["source_event"] = {
                                "part": part_index + 1,
                                "part_name": source_part.partName,
                                "voice": voice,
                                "measure": m.number,
                                "offset": float(old.getOffsetInHierarchy(m)),
                                "duration": float(old.quarterLength),
                                "pitch": selected.pitch.nameWithOctave,
                            }
                        for span in old.getSpannerSites():
                            span.replaceSpannedElement(old, new)
                        parent.replace(old, new)
                    # A lane has one voice; remove now-empty voice containers and
                    # flatten the retained one without changing its event offsets.
                    for v in list(m.voices):
                        if str(v.id) != voice:
                            m.remove(v)
                            continue
                        elements = [(e, e.getOffsetInHierarchy(m)) for e in v]
                        m.remove(v)
                        for e, at in elements:
                            m.insert(at, e)
                    m.makeRests(
                        refStreamOrTimeRange=[0, duration],
                        fillGaps=True,
                        hideRests=True,
                        inPlace=True,
                    )
                before_rests = len(part.recurse().getElementsByClass(note.Rest))
                stream.makeNotation.consolidateCompletedTuplets(part, recurse=True)
                after_rests = len(part.recurse().getElementsByClass(note.Rest))
                if before_rests != after_rests:
                    changes.append(
                        {
                            "action": "consolidate_generated_tuplet_rests",
                            "source_part": part_index + 1,
                            "voice": voice,
                            "lane": rank,
                            "before": before_rests,
                            "after": after_rests,
                            "silence_duration_unchanged": True,
                        }
                    )
                positions = {id(e) for e in part.recurse()}
                for span in list(part.spannerBundle):
                    ends = list(span.getSpannedElements())
                    valid = bool(ends) and all(id(e) in positions for e in ends)
                    if isinstance(span, spanner.Slur):
                        valid = (
                            valid
                            and len(ends) >= 2
                            and all(isinstance(e, note.Note) for e in ends)
                        )
                    if isinstance(span, (spanner.Ottava, spanner.Line)):
                        valid = False
                    if not valid:
                        changes.append(
                            {
                                "action": "omit_inapplicable_source_span",
                                "source_part": part_index + 1,
                                "voice": voice,
                                "lane": rank,
                                "type": type(span).__name__,
                                "endpoint_measures": [
                                    getattr(e, "measureNumber", None) for e in ends
                                ],
                            }
                        )
                        if span.activeSite is not None:
                            span.activeSite.remove(span)
                lanes.append(part)
        for m in measures:
            for n in m.recurse().notes:
                if not n.pitches:
                    changes.append(
                        {
                            "action": "omit_unpitched_event",
                            "source_part": part_index + 1,
                            "measure": m.number,
                            "offset": float(n.getOffsetInHierarchy(m)),
                            "duration": float(n.quarterLength),
                            "reason": "The target trio has no unpitched percussion role",
                        }
                    )
    if not lanes:
        raise ValueError("Trio arrangement requires at least one pitched source voice")
    return lanes


def _ensemble_strands(source, changes):
    """Select three distinct lanes per phrase, using register and activity.

    These are explicit heuristics, not automatic recognition of the composer's
    melody. Phrase boundaries are deferred across source ties and retained spans.
    """
    lanes = _ensemble_lanes(source, changes)
    bars = [list(p.getElementsByClass(stream.Measure)) for p in lanes]
    count = len(bars[0])
    blocked = _trio_boundaries(lanes)
    boundaries = [0]
    for i in range(1, count):
        if i - boundaries[-1] >= 4 and i not in blocked:
            boundaries.append(i)
    boundaries.append(count)
    choices = []
    previous = [None, None, None]
    for start, stop in pairwise(boundaries):
        stats = {}
        duration = sum(float(m.highestTime) for m in bars[0][start:stop])
        for i, ms in enumerate(bars):
            ns = [n for m in ms[start:stop] for n in m.recurse().notes]
            weight = sum(float(n.quarterLength) or 0.125 for n in ns)
            if weight:
                mean = (
                    sum(n.pitch.midi * (float(n.quarterLength) or 0.125) for n in ns)
                    / weight
                )
                stats[i] = (mean, min(1.0, weight / max(duration, 0.125)))
        remaining = set(stats)
        selected = [None, None, None]
        # Highest active line supplies melody; lowest remaining line supplies
        # bass. The third line favors activity and the space between those two.
        for role in (0, 2, 1):
            if not remaining:
                break

            def merit(i, stats=stats, selected=selected, role=role, previous=previous):
                pitch, coverage = stats[i]
                middle = sum(stats[j][0] for j in selected if j is not None)
                middle /= max(1, sum(j is not None for j in selected))
                register = (
                    pitch
                    if role == 0
                    else -pitch
                    if role == 2
                    else -abs(pitch - middle)
                )
                return register + 12 * coverage + (3 if previous[role] == i else 0), -i

            selected[role] = max(remaining, key=merit)
            remaining.remove(selected[role])
        choices.extend([selected] * (stop - start))
        previous = selected
    strands = [stream.Part() for _ in range(3)]
    location = {}
    for mi, selected in enumerate(choices):
        for role, lane in enumerate(selected):
            if lane is None:
                m = copy.deepcopy(bars[0][mi])
                for n in list(m.recurse().notesAndRests):
                    n.activeSite.remove(n)
                m.insert(0, note.Rest(quarterLength=bars[0][mi].highestTime))
            else:
                m = bars[lane][mi]
            strands[role].insert(bars[0][mi].offset, m)
            for e in [m, *m.recurse()]:
                location[id(e)] = role
            changes.append(
                {
                    "action": "select_source_lane",
                    "measure": m.number,
                    "role": ("lead", "inner", "bass")[role],
                    "lane": lane,
                    "source": next(
                        (n.editorial.get("source_event") for n in m.recurse().notes),
                        None,
                    ),
                }
            )
    for lane in lanes:
        for span in lane.spannerBundle:
            ends = list(span.getSpannedElements())
            destinations = {location.get(id(e)) for e in ends}
            if len(destinations) == 1 and None not in destinations:
                strands[destinations.pop()].insert(0, span)
            elif any(id(e) in location for e in ends):
                changes.append(
                    {
                        "action": "omit_span_after_ensemble_reduction",
                        "type": type(span).__name__,
                        "endpoint_measures": [
                            getattr(e, "measureNumber", None) for e in ends
                        ],
                    }
                )
    # Global tempo and structural instructions may live on an unselected staff.
    # Conflicting simultaneous tempi require an editorial decision, not guessing.
    tempos = {}
    for p in source.parts:
        for mark in p.recurse().getElementsByClass(tempo.MetronomeMark):
            at = mark.getOffsetInHierarchy(p)
            bpm = mark.getQuarterBPM()
            if bpm is None:
                continue
            if at in tempos and abs(tempos[at].getQuarterBPM() - bpm) > 1e-6:
                raise ValueError(f"Conflicting source tempos at quarter offset {at}")
            tempos[at] = mark
    source_bars = [list(p.getElementsByClass(stream.Measure)) for p in source.parts]
    structural = source_bars[0]
    common_meters, common_repeats = [], []
    for aligned in zip(*source_bars):
        meters, repeats = {}, {}
        for original in aligned:
            for mark in original.getElementsByClass("TimeSignature"):
                if (
                    mark.offset in meters
                    and meters[mark.offset].ratioString != mark.ratioString
                ):
                    raise ValueError(
                        f"Conflicting source meters in measure {original.number}"
                    )
                meters[mark.offset] = mark
            for side in ("leftBarline", "rightBarline"):
                mark = getattr(original, side)
                if isinstance(mark, bar.Repeat):
                    if side in repeats and (
                        repeats[side].direction,
                        repeats[side].times,
                    ) != (mark.direction, mark.times):
                        raise ValueError(
                            f"Conflicting source repeats in measure {original.number}"
                        )
                    repeats[side] = mark
        common_meters.append(meters)
        common_repeats.append(repeats)
    endings = {}
    for p, ms in zip(source.parts, source_bars):
        indices = {id(m): i for i, m in enumerate(ms)}
        for ending in p.spannerBundle.getByClass(spanner.RepeatBracket):
            endpoint_indices = tuple(
                indices[id(m)] for m in ending.getSpannedElements()
            )
            endings[(str(ending.number), endpoint_indices)] = ending
    for part in strands:
        for mark in list(part.recurse().getElementsByClass(tempo.MetronomeMark)):
            mark.activeSite.remove(mark)
        for ending in list(part.spannerBundle.getByClass(spanner.RepeatBracket)):
            ending.activeSite.remove(ending)
        target_measures = list(part.getElementsByClass(stream.Measure))
        for mi, (original, m) in enumerate(zip(structural, target_measures)):
            # MusicXML permits meter/repeats to be declared on only one staff.
            for cls in ("TimeSignature",):
                for old in list(m.getElementsByClass(cls)):
                    m.remove(old)
                for obj in common_meters[mi].values():
                    m.insert(obj.offset, copy.deepcopy(obj))
            m.leftBarline = copy.deepcopy(original.leftBarline)
            m.rightBarline = copy.deepcopy(original.rightBarline)
            for side, mark in common_repeats[mi].items():
                setattr(m, side, copy.deepcopy(mark))
            for at, mark in tempos.items():
                if m.offset <= at < m.offset + m.highestTime:
                    m.insert(at - m.offset, copy.deepcopy(mark))
        for (number, indices), ending in endings.items():
            replacement = spanner.RepeatBracket(
                [target_measures[i] for i in indices], number=number
            )
            replacement.style = copy.deepcopy(ending.style)
            part.insert(0, replacement)
    return strands


def arrange_trio(source, variant="relay-two"):
    """Redistribute an ensemble to alto, cello and bass without new note material.

    Nominal phrase lengths are editorial settings, not physiological laws.
    Handoffs never cross retained ties or source slurs/hairpins. All three parts
    remain monophonic; original chord/secondary-voice omissions are audited.
    """
    if variant not in TRIO_VARIANTS:
        raise ValueError(f"Choose a trio policy from {list(TRIO_VARIANTS)}")
    if not source.parts:
        raise ValueError("Trio orchestration requires at least one source part")
    source = source.toSoundingPitch(inPlace=False)
    from collections import Counter

    changes = []
    grids = [
        [
            (m.number, m.offset, m.highestTime)
            for m in p.getElementsByClass(stream.Measure)
        ]
        for p in source.parts
    ]
    if not grids[0] or any(grid != grids[0] for grid in grids[1:]):
        raise ValueError(
            "Source ensemble needs aligned, nonempty measures before reduction"
        )
    part_spans = {id(s) for p in source.parts for s in p.spannerBundle}
    for span in list(source.spannerBundle):
        if id(span) in part_spans:
            continue
        ends = list(span.getSpannedElements())
        owners = [
            p
            for p in source.parts
            if all(id(e) in {id(o) for o in p.recurse()} for e in ends)
        ]
        if len(owners) == 1:
            source.remove(span)
            owners[0].insert(0, span)
        else:
            changes.append(
                {
                    "action": "omit_cross_part_source_span",
                    "type": type(span).__name__,
                    "endpoint_measures": [
                        getattr(e, "measureNumber", None) for e in ends
                    ],
                    "reason": "Cross-part notation has no unambiguous monophonic strand",
                }
            )
    strands = _ensemble_strands(source, changes)

    # Catalog every omitted pitch, including pitches in secondary voices.
    def material(parts):
        return Counter(
            (
                pi + 1,
                m.number,
                float(n.getOffsetInHierarchy(m)),
                float(n.quarterLength),
                p.nameWithOctave,
            )
            for pi, part in enumerate(parts)
            for m in part.getElementsByClass(stream.Measure)
            for n in m.recurse().notes
            for p in n.pitches
        )

    retained_source = Counter(
        tuple(
            n.editorial["source_event"][field]
            for field in ("part", "measure", "offset", "duration", "pitch")
        )
        for part in strands
        for n in part.recurse().notes
    )
    for (source_part, measure, offset, duration, pitch), count in (
        material(source.parts) - retained_source
    ).items():
        changes.append(
            {
                "action": "omit_source_pitch",
                "source_part": source_part,
                "measure": measure,
                "offset": offset,
                "duration": duration,
                "pitch": pitch,
                "count": count,
                "reason": "Three monophonic roles selected from source voices/chord lanes",
            }
        )
    bars = [list(p.getElementsByClass(stream.Measure)) for p in strands]
    blocked = _trio_boundaries(strands[:2])
    period = {"sax-led": None, "relay-four": 4, "relay-two": 2}[variant]
    routes = []
    state, elapsed = 0, 0
    for index, m in enumerate(bars[0]):
        if period and elapsed >= period and index not in blocked:
            state, elapsed = 1 - state, 0
        routes.append(state)
        elapsed += 1
    score = stream.Score()
    instruments = (
        instrument.AltoSaxophone(),
        instrument.Violoncello(),
        instrument.Contrabass(),
    )
    for i, inst in enumerate(instruments):
        part = stream.Part(id=("alto-saxophone", "cello", "double-bass")[i])
        part.partName = inst.instrumentName
        part.partAbbreviation = ("A. sax.", "Vc.", "Cb.")[i]
        part.insert(0, inst)
        part.atSoundingPitch = True
        score.insert(0, part)
    location = {}
    for index, (pair, route) in enumerate(zip(zip(*bars), routes)):
        for strand_index, m in enumerate(pair):
            destination = 2 if strand_index == 2 else (strand_index + route) % 2
            score.parts[destination].insert(m.offset, m)
            for obj in [m, *list(m.recurse())]:
                location[id(obj)] = destination
        changes.append(
            {
                "action": "route_phrase",
                "measure": pair[0].number,
                "melody": "alto-saxophone" if route == 0 else "cello",
                "inner": "cello" if route == 0 else "alto-saxophone",
            }
        )
    for part in strands:
        for span in list(part.spannerBundle):
            destinations = {location.get(id(n)) for n in span.getSpannedElements()}
            if len(destinations) != 1 or None in destinations:
                raise ValueError("A handoff would break a retained source span")
            score.parts[destinations.pop()].insert(0, span)
    # Choose register for the physical players, preserving each bar's intervals.
    score.atSoundingPitch = True
    for i, (part, profile) in enumerate(zip(score.parts, (ALTO, CELLO, BASS))):
        # Instruments in the copied source measures must not override the new one.
        for old in list(part.recurse().getElementsByClass(instrument.Instrument)):
            if old is not instruments[i]:
                old.activeSite.remove(old)
        for n in part.recurse().notes:
            removed = [
                type(a).__name__
                for a in n.articulations
                if isinstance(
                    a, (articulations.Fingering, articulations.StringIndication)
                )
                or i == 0
                and isinstance(
                    a,
                    (
                        articulations.UpBow,
                        articulations.DownBow,
                        articulations.OpenString,
                    ),
                )
            ]
            if removed:
                changes.append(
                    {
                        "action": "remove_instrument_specific_marks",
                        "part": i,
                        "measure": n.measureNumber,
                        "marks": removed,
                    }
                )
            n.articulations = [
                a for a in n.articulations if type(a).__name__ not in removed
            ]
        if i == 0:
            for mark in list(
                part.recurse().getElementsByClass(expressions.TextExpression)
            ):
                adapted = wind_direction(mark.content)
                if adapted != mark.content:
                    changes.append(
                        {
                            "action": "adapt_string_direction_on_sax",
                            "measure": mark.measureNumber,
                            "text": mark.content,
                            "replacement": adapted,
                        }
                    )
                    if adapted is None:
                        mark.activeSite.remove(mark)
                    else:
                        mark.content = adapted
        optimize_register(part, profile, changes)
        outside = [
            (n.measureNumber, p.nameWithOctave)
            for n in part.recurse().notes
            for p in n.pitches
            if not profile.low <= p.midi <= profile.high
        ]
        if outside:
            _fit_trio_line(part, profile, changes)
    title = (
        (source.metadata.title or source.metadata.movementName)
        if source.metadata
        else None
    ) or "Untitled"
    score.metadata = (
        copy.deepcopy(source.metadata) if source.metadata else metadata.Metadata()
    )
    score.metadata.title = f"{title} — alto trio ({variant})"
    if title.lower() == "passacaglia":
        score.metadata.composer = "G. F. Handel / Johan Halvorsen"
    score.metadata.movementName = score.metadata.title
    score.parts[0].getElementsByClass(stream.Measure).first().insert(
        0, expressions.TextExpression("Alto saxophone in E-flat; written pitch")
    )
    score.parts[2].getElementsByClass(stream.Measure).first().insert(
        0,
        expressions.TextExpression("Double bass sounds one octave below written pitch"),
    )
    from .trio_judge import evaluate_trio

    evaluation = evaluate_trio(source, score)
    report = {
        "variant": variant,
        "policy": TRIO_VARIANTS[variant],
        "source_tempo_repairs": source.editorial.get("sound_tempo_repairs", []),
        "part_files": ["alto-saxophone", "cello", "double-bass"],
        "profiles_sounding": [asdict(p) for p in (ALTO, CELLO, BASS)],
        "source": inspect_score(source),
        "result": inspect_score(score),
        "changes": changes,
        "reduction_policy": {
            "name": "ensemble-voice-lanes-v1",
            "description": "Partition explicit voices and chord tones; select high/inner/low active lanes in nominal four-bar source phrases, deferring across ties/spans. Target-player relay is a separate step.",
            "invented_accompaniment": False,
        },
        "source_assignments": [
            {
                **n.editorial["source_event"],
                "destination": part.id,
                "sounding_pitch": n.pitch.nameWithOctave,
                "semitones": n.pitch.midi
                - note.Note(n.editorial["source_event"]["pitch"]).pitch.midi,
            }
            for part in score.parts
            for n in part.recurse().notes
            if "source_event" in n.editorial
        ],
        "iterations": [
            {
                "stage": "independent_trio_review",
                "critique": {"counts": evaluation["metrics"]},
            }
        ],
        "independent_evaluation": evaluation,
        "limitations": [
            "Monophonic reduction; omitted source pitches and spans are audited.",
            "Phrase lengths and breath-warning thresholds are editorial assumptions, not measured performer limits.",
            "Saxophone/string balance, fingering, tonguing and breath pressure require musician review.",
        ],
        "sources": {
            "sax_transposition": "https://www.yamaha.com/en/musical_instrument_guide/saxophone/play/play003.html",
            "breathing": "https://www.yamaha.com/en/musical_instrument_guide/saxophone/play/play004.html",
            "sax_character": "https://philharmonia.co.uk/resources/instruments/saxophone/",
        },
    }
    source_path = getattr(source.metadata, "filePath", None)
    if source_path and Path(source_path).is_file():
        report["input"] = {
            "path": str(Path(source_path).resolve()),
            "sha256": hashlib.sha256(Path(source_path).read_bytes()).hexdigest(),
        }
    return score, report
