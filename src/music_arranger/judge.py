"""Independent, deterministic arrangement judge; no optimizer imports.

Rubric v1 is frozen before evaluating revisions. This is a conservative proxy
for score review, not a perceptual model or a substitute for string players.
All calculations use sounding pitch and written (unexpanded) measures. Grace
notes have zero exposure; repeats therefore do not multiply metric weights.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from music21 import bar, chord, converter, meter, stream

RUBRIC = {
    "version": "independent-duo-v1",
    "weights": {
        "range": 30,
        "low_spacing": 15,
        "wide_spacing": 10,
        "new_crossing": 10,
        "contour_change": 15,
        "fast_motion": 10,
        "multiple_stops": 10,
    },
    "range_midi": [[36, 84], [28, 67]],
    "low_spacing_bass_threshold": 48,
    "close_spacing_semitones": 12,
    "wide_spacing_semitones": 28,
    "fast_duration_quarters": 0.5,
    "fast_leap_semitones": 9,
    "minimum_retained_pitched_event_fraction": 0.75,
}
RUBRIC_HASH = hashlib.sha256(json.dumps(RUBRIC, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Event:
    start: float
    end: float
    measure: int
    offset: float
    pitches: tuple[int, ...]
    ties: tuple[str | None, ...]

    @property
    def duration(self):
        return self.end - self.start


def _load(value):
    score = converter.parse(value) if isinstance(value, (str, Path)) else value
    if not isinstance(score, stream.Score) or len(score.parts) != 2:
        raise ValueError("Judge requires exactly two score parts, cello then bass.")
    return score.toSoundingPitch(inPlace=False)


def _events(part):
    result = []
    for m in part.getElementsByClass(stream.Measure):
        for n in m.recurse().notes:
            offset = float(n.getOffsetInHierarchy(m))
            start = float(m.offset) + offset
            components = n.notes if isinstance(n, chord.Chord) else [n]
            result.append(
                Event(
                    start,
                    start + float(n.quarterLength),
                    m.number,
                    offset,
                    tuple(p.midi for p in n.pitches),
                    tuple(c.tie.type if c.tie else None for c in components),
                )
            )
    return sorted(result, key=lambda e: (e.start, e.end, e.pitches))


def spacing_cost(upper: float, lower: float) -> float:
    """Register-dependent close-spacing exposure, NOT a consonance ranking."""
    gap = abs(upper - lower)
    lowness = min(1.0, max(0.0, (48 - min(upper, lower)) / 20))
    closeness = max(0.0, (12 - gap) / 12)
    return lowness * closeness


def _measure_signature(part):
    result = []
    for m in part.getElementsByClass(stream.Measure):
        repeats = []
        for side, boundary in (("left", m.leftBarline), ("right", m.rightBarline)):
            if isinstance(boundary, bar.Repeat):
                repeats.append((side, boundary.direction, boundary.times))
        signatures = [
            (float(t.offset), t.ratioString)
            for t in m.getElementsByClass(meter.TimeSignature)
        ]
        result.append(
            (
                m.number,
                round(float(m.offset), 6),
                round(float(m.highestTime), 6),
                repeats,
                signatures,
            )
        )
    return result


def _key(e):
    return e.measure, round(e.offset, 6), round(e.duration, 6)


def _integrity(source, candidate, source_events, candidate_events):
    issues = []
    coverage = []
    for role in range(2):
        source_count = len(source_events[role])
        candidate_count = len(candidate_events[role])
        fraction = candidate_count / source_count if source_count else 1.0
        coverage.append(
            {
                "part": role,
                "source_pitched_events": source_count,
                "candidate_pitched_events": candidate_count,
                "retained_event_fraction": round(fraction, 6),
                "source_pitch_count": sum(len(e.pitches) for e in source_events[role]),
                "candidate_pitch_count": sum(
                    len(e.pitches) for e in candidate_events[role]
                ),
            }
        )
        if fraction < RUBRIC["minimum_retained_pitched_event_fraction"]:
            issues.append(
                {
                    "part": role,
                    "kind": "excessive_event_removal",
                    "retained_event_fraction": round(fraction, 6),
                }
            )
        if _measure_signature(source.parts[role]) != _measure_signature(
            candidate.parts[role]
        ):
            issues.append({"part": role, "kind": "measure_repeat_or_meter_changed"})
        available = Counter(_key(e) for e in source_events[role])
        requested = Counter(_key(e) for e in candidate_events[role])
        for timing, count in (requested - available).items():
            issues.append(
                {
                    "part": role,
                    "kind": "introduced_or_retimed_event",
                    "timing": timing,
                    "count": count,
                }
            )
        # Candidate may remove voices or chord tones. Each retained event's tie
        # types must occur in some source event with the same onset and duration.
        source_by_key = {}
        for event in source_events[role]:
            source_by_key.setdefault(_key(event), []).append(Counter(event.ties))
        for event in candidate_events[role]:
            options = source_by_key.get(_key(event), [])
            if options and not any(
                not (Counter(event.ties) - option) for option in options
            ):
                issues.append(
                    {
                        "part": role,
                        "kind": "retained_tie_type_changed",
                        "measure": event.measure,
                        "offset": event.offset,
                    }
                )
    return {
        "passed": not issues,
        "issues": issues,
        "coverage": coverage,
        "coverage_policy": "Reject removal of more than 25% of pitched events in either part; this safeguard does not establish that smaller removals are justified. Compare coverage against the control when selecting a revision.",
        "checked": [
            "measure boundaries and duration",
            "repeat barlines and counts",
            "explicit time signatures",
            "retained pitched-event timing",
            "retained tie-type subsets",
        ],
        "not_certified": [
            "omitted voices/chord tones are musically justified",
            "tie pitch continuity and enharmonic spelling",
            "rest spelling",
            "repeat endings and text instructions",
            "slurs, hairpins and other spanner endpoints",
            "notation legibility; requires rendered-page review",
        ],
    }


def _at(events, time, role):
    active = [p for e in events if e.start <= time < e.end for p in e.pitches]
    return (max(active) if role == 0 else min(active)) if active else None


def evaluate(
    source: str | Path | stream.Score, candidate: str | Path | stream.Score
) -> dict:
    """Return independent metrics, structural gates and measure-level findings."""
    src, dst = _load(source), _load(candidate)
    se = [_events(p) for p in src.parts]
    ce = [_events(p) for p in dst.parts]
    structural = _integrity(src, dst, se, ce)
    sums = {name: 0.0 for name in RUBRIC["weights"]}
    denominators = dict(sums)
    issues = []

    def add(kind, cost, weight, **location):
        sums[kind] += cost * weight
        denominators[kind] += weight
        if cost > 0:
            issues.append(
                {
                    "kind": kind,
                    "cost": round(cost, 6),
                    "exposure_quarters": round(weight, 6),
                    **location,
                }
            )

    boundaries = sorted(
        {
            round(t, 6)
            for parts in (se, ce)
            for events in parts
            for e in events
            for t in (e.start, e.end)
        }
    )
    overlap = 0.0
    for start, end in itertools.pairwise(boundaries):
        mid = (start + end) / 2
        upper, lower = _at(ce[0], mid, 0), _at(ce[1], mid, 1)
        if upper is None or lower is None:
            continue
        duration = end - start
        overlap += duration
        measure = next(e.measure for e in ce[0] if e.start <= mid < e.end)
        loc = {
            "measure": measure,
            "start": round(start, 6),
            "upper_midi": upper,
            "lower_midi": lower,
        }
        add("low_spacing", spacing_cost(upper, lower), duration, **loc)
        add("wide_spacing", min(1, max(0, (upper - lower - 28) / 12)), duration, **loc)
        old_upper, old_lower = _at(se[0], mid, 0), _at(se[1], mid, 1)
        if old_upper is not None and old_lower is not None:
            new_crossing = upper < lower and old_upper >= old_lower
            add("new_crossing", float(new_crossing), duration, **loc)

    for role, events in enumerate(ce):
        low, high = RUBRIC["range_midi"][role]
        # Compare the sounding outer voice rather than pairing arbitrary voice
        # events with equal onsets. Uniform transposition must have zero change.
        previous_skyline = None
        for start, end in itertools.pairwise(boundaries):
            mid = (start + end) / 2
            current = _at(events, mid, role)
            original = _at(se[role], mid, role)
            if current is None or original is None:
                previous_skyline = None
                continue
            if previous_skyline is not None:
                prev_current, prev_original = previous_skyline
                change = abs((current - prev_current) - (original - prev_original))
                measure = next(e.measure for e in events if e.start <= mid < e.end)
                add(
                    "contour_change",
                    min(1, change / 12),
                    end - start,
                    part=role,
                    measure=measure,
                    start=round(start, 6),
                )
            previous_skyline = current, original
        previous = None
        for e in events:
            if e.duration <= 0:
                continue
            lead = max(e.pitches) if role == 0 else min(e.pitches)
            loc = {"part": role, "measure": e.measure, "offset": round(e.offset, 6)}
            outside = sum(p < low or p > high for p in e.pitches) / len(e.pitches)
            add("range", outside, e.duration, **loc)
            stops = (len(e.pitches) > 2) if role == 0 else (len(e.pitches) > 1)
            add("multiple_stops", float(stops), e.duration, **loc)
            if previous:
                prev, prev_lead = previous
                motion = abs(lead - prev_lead)
                fast = max(0, min(1, (motion - 9) / 12)) if prev.duration <= 0.5 else 0
                add("fast_motion", fast, e.duration, **loc)
            previous = e, lead
    metrics = {
        name: {
            "penalty": round(sums[name] / denominators[name], 8)
            if denominators[name]
            else None,
            "exposure_quarters": round(denominators[name], 6),
            "weight": RUBRIC["weights"][name],
        }
        for name in sums
    }
    grade = 100 - sum((v["penalty"] or 0) * v["weight"] for v in metrics.values())
    return {
        "rubric": RUBRIC,
        "rubric_sha256": RUBRIC_HASH,
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest()
        if isinstance(source, (str, Path))
        else None,
        "candidate_sha256": hashlib.sha256(Path(candidate).read_bytes()).hexdigest()
        if isinstance(candidate, (str, Path))
        else None,
        "heuristic_score": round(grade, 4),
        "eligible": structural["passed"],
        "structural_integrity": structural,
        "metrics": metrics,
        "issues": issues,
        "simultaneous_exposure_quarters": round(overlap, 6),
        "source_measures": [
            len(p.getElementsByClass(stream.Measure)) for p in src.parts
        ],
        "candidate_measures": [
            len(p.getElementsByClass(stream.Measure)) for p in dst.parts
        ],
        "limitations": [
            "Independent implementation; shares music21 parser, not arranger scoring code.",
            "Rubric constants are declared review preferences, not measured acoustic thresholds.",
            "Outer voices approximate musical roles; chord interiors and source voice selection can confuse contour metrics.",
            "Vertical spacing uses the highest cello and lowest bass sounding tones; close spacing involving interior chord tones is not measured.",
            "Ranges assume standard four-string bass; upper limits are review thresholds, not physical limits.",
            "No tempo-aware fingering, bowing, timbre, dynamics, listening or musician assessment.",
            "Fast-motion exposure uses previous note duration, not actual elapsed time including rests; concurrent voices may also produce conservative false positives.",
            "Zero-duration grace notes and performed repeat weighting are excluded.",
            "Any grade must be read alongside integrity gates and omitted-material audit; deleting difficult material can game metrics.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.source, args.candidate)
    encoded = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
