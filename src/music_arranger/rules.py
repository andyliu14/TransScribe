"""Cited deterministic models; parameters that are ours are labelled explicitly.

This module is NOT the independent judge. It supplies arranging rules and a
separate diagnostic audit, leaving the earlier judge and its weights frozen.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from music21 import chord, expressions, stream, tempo

SOURCES = {
    "sethares": {
        "title": "William Sethares: Relating Tuning and Timbre (1993), accompanying dissonance model",
        "url": "https://sethares.engr.wisc.edu/consemi.html",
        "formula_url": "https://sethares.engr.wisc.edu/comprog.html",
        "locator": "How to Calculate Dissonance Curves; dissmeasure coefficients",
    },
    "huron": {
        "title": "David Huron (2001), Tone and Voice, Music Perception 19(1), 1–64",
        "url": "https://doi.org/10.1525/mp.2001.19.1.1",
        "read_url": "https://www.musanim.com/articles/Huron.ToneAndVoice.2001.pdf",
        "locator": "pp. 12, 18, 24: temporal continuity, minimum masking, pitch proximity",
    },
    "vsl_cello": {
        "title": "Vienna Symphonic Library Academy: Cello",
        "url": "https://www.vsl.co.at/academy/strings/cello",
        "locator": "Fingering; Playing Techniques (left hand), double/triple/quadruple stops",
    },
    "vsl_bass": {
        "title": "Vienna Symphonic Library Academy: Double bass",
        "url": "https://www.vsl.co.at/academy/strings/double-bass",
        "locator": "Brief description; Fingering system; Double stops; Harmonics",
    },
}


@dataclass(frozen=True)
class RuleConfig:
    version: str = "literature-rules-v1"
    # Our spectral approximation, not measured cello/bass spectra:
    partials: int = 6
    amplitude_rolloff: float = 1.0  # amplitude of harmonic k = 1/k**rolloff
    # Huron's approximate perceptual context, not performance speed limits:
    continuity_silence_seconds: float = 0.8
    proximity_duration_seconds: float = 0.7
    proximity_semitones: float = 2.0
    # Our optimization weights; neither author supplies these:
    roughness_weight: float = 1.0
    proximity_weight: float = 1.0
    default_quarter_bpm: float = 120.0


CONFIG = RuleConfig()
OPEN_STRINGS = ((36, 43, 50, 57), (28, 33, 38, 43))


def rule_catalog():
    return {
        "config": asdict(CONFIG),
        "open_strings_midi": OPEN_STRINGS,
        "published_roughness_coefficients": {
            "Dstar": 0.24,
            "S1": 0.0207,
            "S2": 18.96,
            "C1": 5,
            "C2": -5,
            "A1": -3.51,
            "A2": -5.75,
        },
        "sources": SOURCES,
        "sources_accessed": "2026-10-19",
        "rules": [
            {
                "id": "spectral_roughness",
                "type": "soft_model",
                "source": "sethares",
                "application": "Full harmonic spectra of every simultaneous pitch, including chord interiors; no interval-class folding. Our partial envelope and objective weight are explicit assumptions.",
            },
            {
                "id": "stream_proximity",
                "type": "soft_adaptation",
                "source": "huron",
                "application": "Short-note leaps receive a linear excess-distance cost; real tempo and intervening silence are considered. The linear cost is ours, not a fitted perceptual law.",
            },
            {
                "id": "stream_gap",
                "type": "diagnostic",
                "source": "huron",
                "application": "Silence of at least 0.8 s marks a possible stream break; no hard claim about musical phrasing.",
            },
            {
                "id": "string_floor",
                "type": "hard_constraint",
                "source": ["vsl_cello", "vsl_bass"],
                "application": "Standard cello and four-string orchestral bass; no extensions, scordatura or solo tuning.",
            },
            {
                "id": "double_stop_assignment",
                "type": "conditional_necessary_constraint",
                "source": ["vsl_cello", "vsl_bass"],
                "application": "Two ordinary bowed pitches require distinct adjacent strings, each pitch at or above its open string. Explicit pizzicato removes adjacency, not distinctness. A possible assignment does not establish hand reach. Harmonic/ambiguous circle notation is excluded.",
            },
            {
                "id": "multiple_stop_review",
                "type": "diagnostic",
                "source": ["vsl_cello", "vsl_bass"],
                "application": "Three-plus notes require execution review; never reject all rolled chords as impossible.",
            },
        ],
    }


def rule_hash():
    return hashlib.sha256(
        json.dumps(rule_catalog(), sort_keys=True).encode()
    ).hexdigest()


def partial_roughness(f1, a1, f2, a2):
    """Sethares' published sum-of-exponentials model, independently implemented."""
    distance = abs(f1 - f2)
    scale = 0.24 / (0.0207 * min(f1, f2) + 18.96)
    x = scale * distance
    return min(a1, a2) * 5 * (math.exp(-3.51 * x) - math.exp(-5.75 * x))


@lru_cache(maxsize=32768)
def spectral_roughness(midi_pitches: tuple, partials=6, rolloff=1.0):
    if partials < 1 or rolloff <= 0:
        raise ValueError("Use at least one partial and positive amplitude rolloff")
    spectrum = [
        (440 * 2 ** ((p - 69) / 12) * k, k ** (-rolloff))
        for p in midi_pitches
        for k in range(1, partials + 1)
    ]
    return sum(
        partial_roughness(*a, *b) for a, b in itertools.combinations(spectrum, 2)
    )


def string_assignments(pitches, role, *, adjacent=True):
    """Necessary pitch/string compatibility only; no upper-position restriction."""
    choices = [
        [i for i, open_pitch in enumerate(OPEN_STRINGS[role]) if pitch >= open_pitch]
        for pitch in pitches
    ]
    return [
        assignment
        for assignment in itertools.product(*choices)
        if len(set(assignment)) == len(pitches)
        and (
            not adjacent
            or not assignment
            or max(assignment) - min(assignment) == len(assignment) - 1
        )
    ]


def harmonic_notation(n):
    components = n.notes if isinstance(n, chord.Chord) else [n]
    return any(
        type(a).__name__ in {"Harmonic", "StringHarmonic", "OpenString"}
        for a in n.articulations
    ) or any(
        c.notehead == "diamond"
        or any("Harmonic" in type(a).__name__ for a in c.articulations)
        for c in components
    )


def bowing_modes(part):
    """Track explicit pizz./arco text; ordinary arco is the stated default."""
    marks = []
    for expression in part.recurse().getElementsByClass(expressions.TextExpression):
        content = expression.content.lower()
        mode = (
            "pizzicato"
            if re.search(r"\bpizz", content)
            else "arco"
            if re.search(r"\barco\b", content)
            else None
        )
        if mode:
            marks.append((float(expression.getOffsetInHierarchy(part)), mode))
    marks.sort(key=lambda x: x[0])
    modes = {}
    for n in part.recurse().notes:
        offset = float(n.getOffsetInHierarchy(part))
        preceding = [mode for time, mode in marks if time <= offset]
        mode = preceding[-1] if preceding else "arco"
        if any("Pizzicato" in type(a).__name__ for a in n.articulations):
            mode = "pizzicato"
        modes[id(n)] = mode
    return modes


class ScoreClock:
    """Integrate explicit upper-part metronome markings, including dotted units."""

    def __init__(self, score):
        self.changes = {0.0: CONFIG.default_quarter_bpm}
        self.explicit = []
        part = score.parts[0]
        for mark in part.recurse().getElementsByClass(tempo.MetronomeMark):
            bpm = mark.getQuarterBPM()
            if bpm is not None and bpm > 0:
                offset = float(mark.getOffsetInHierarchy(part))
                self.changes[offset] = float(bpm)
                self.explicit.append(offset)

    def seconds(self, start, end):
        if end <= start:
            return 0.0
        boundaries = (
            [start] + sorted(t for t in self.changes if start < t < end) + [end]
        )
        return sum(
            (b - a) * 60 / self.changes[max(t for t in self.changes if t <= a)]
            for a, b in itertools.pairwise(boundaries)
        )


def proximity_cost(semitones, duration_seconds, silence_seconds):
    if silence_seconds >= CONFIG.continuity_silence_seconds:
        return 0.0
    duration_factor = max(0, 1 - duration_seconds / CONFIG.proximity_duration_seconds)
    return max(0, abs(semitones) - CONFIG.proximity_semitones) * duration_factor


def sonority_cost(timed_parts, shifts):
    boundaries = sorted(
        {
            t
            for ns in timed_parts
            for start, duration, _ in ns
            for t in (start, start + duration)
        }
    )
    total = 0.0
    for start, end in itertools.pairwise(boundaries):
        mid = (start + end) / 2
        sounding = tuple(
            sorted(
                p.midi + shift
                for ns, shift in zip(timed_parts, shifts)
                for offset, duration, n in ns
                if offset <= mid < offset + duration
                for p in n.pitches
            )
        )
        if sounding:
            total += (end - start) * spectral_roughness(
                sounding, CONFIG.partials, CONFIG.amplitude_rolloff
            )
    return total * CONFIG.roughness_weight


def audit_rules(score):
    """Model values and rule witnesses, without an invented overall quality grade."""
    score = score.toSoundingPitch(inPlace=False)
    clock = ScoreClock(score)
    issues = []
    skipped = 0
    roughness = 0.0
    exposure = 0.0
    proximity = 0.0
    string_failures = 0
    measures = [list(p.getElementsByClass(stream.Measure)) for p in score.parts]
    for pair in zip(*measures):
        timed = [
            [
                (float(n.getOffsetInHierarchy(m)), float(n.quarterLength), n)
                for n in m.recurse().notes
            ]
            for m in pair
        ]
        value = sonority_cost(timed, (0, 0)) / CONFIG.roughness_weight
        roughness += value
        exposure += float(pair[0].highestTime)
        if value:
            issues.append(
                {
                    "rule": "spectral_roughness",
                    "measure": pair[0].number,
                    "integrated_model_value": round(value, 6),
                }
            )
    for role, part in enumerate(score.parts):
        modes = bowing_modes(part)
        previous = None
        ns = sorted(
            part.recurse().notes, key=lambda n: float(n.getOffsetInHierarchy(part))
        )
        for n in ns:
            ps = [p.midi for p in n.pitches]
            location = {"part": role, "measure": n.measureNumber, "pitches_midi": ps}
            if min(ps) < OPEN_STRINGS[role][0]:
                issues.append({"rule": "string_floor", "severity": "error", **location})
            if harmonic_notation(n):
                skipped += 1
                issues.append(
                    {
                        "rule": "harmonic_manual_review",
                        "severity": "unknown",
                        **location,
                    }
                )
            elif len(ps) == 2:
                assignments = string_assignments(
                    ps, role, adjacent=modes[id(n)] == "arco"
                )
                if not assignments:
                    string_failures += 1
                    issues.append(
                        {
                            "rule": "double_stop_assignment",
                            "severity": "error_under_ordinary_string_assumption",
                            "bowing": modes[id(n)],
                            **location,
                        }
                    )
            elif len(ps) > 2:
                issues.append(
                    {"rule": "multiple_stop_review", "severity": "review", **location}
                )
            if not n.quarterLength:
                continue
            start = float(n.getOffsetInHierarchy(part))
            end = start + float(n.quarterLength)
            lead = max(ps) if role == 0 else min(ps)
            if previous and start >= previous[1] - 1e-6:
                prev_start, prev_end, prev_pitch = previous
                duration = clock.seconds(prev_start, prev_end)
                silence = clock.seconds(prev_end, start)
                cost = proximity_cost(lead - prev_pitch, duration, silence)
                proximity += cost
                if cost or silence >= CONFIG.continuity_silence_seconds:
                    issues.append(
                        {
                            "rule": "stream_gap"
                            if silence >= CONFIG.continuity_silence_seconds
                            else "stream_proximity",
                            "severity": "review",
                            "cost": round(cost, 6),
                            "silence_seconds": round(silence, 6),
                            "previous_duration_seconds": round(duration, 6),
                            **location,
                        }
                    )
            previous = start, end, lead
    return {
        "rulebook_sha256": rule_hash(),
        "implementation_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "catalog": rule_catalog(),
        "metrics": {
            "roughness_integral": roughness,
            "roughness_per_quarter": roughness / exposure if exposure else 0,
            "stream_proximity_sum": proximity,
            "unassignable_ordinary_double_stops": string_failures,
            "harmonic_events_requiring_review": skipped,
        },
        "tempo": {
            "explicit_mark_offsets": clock.explicit,
            "fallback_quarter_bpm": CONFIG.default_quarter_bpm,
            "fallback_used_at_start": 0.0 not in clock.explicit,
        },
        "issues": issues,
        "limitations": [
            "Six harmonic partials with 1/k amplitudes are assumed, not measured instrument spectra.",
            "No overall musical grade; roughness can be expressive and lower is not always preferable.",
            "Proximity is a perceptual streaming proxy, not a motor/fingering model; textual rubato and fermatas are not timed.",
            "String assignment is necessary, never sufficient; reach, bow pressure, positions and harmonics need separate modelling.",
            "Chord interiors enter the roughness calculation; melodic movement still uses outer tones and skips overlapping events.",
        ],
    }
