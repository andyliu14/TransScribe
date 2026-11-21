"""Independent trio diagnostics with fixed musical thresholds.

No imports from the arranger or its cost functions. No overall quality grade:
wind phrase length, material retention and difficult motion are tradeoffs.
Version 3 accepts arbitrary source ensembles. Pitch retention is a review
diagnostic, since a dense sextet cannot generally fit three monophonic players.
"""

import hashlib
import itertools
import json
from collections import Counter
from pathlib import Path

from music21 import bar, chord, converter, meter, repeat, spanner, stream, tempo

from .ingest import restore_sound_tempos

RUBRIC = {
    "version": "independent-alto-trio-v4-tessitura",
    "sounding_ranges": [[49, 80], [36, 81], [28, 67]],
    "preferred_sounding_ranges": [[55, 74], [48, 69], [28, 50]],
    "register_exposure_review_fraction": 0.10,
    "breath_gap_seconds": 0.25,
    "breath_warning_seconds": 12.0,
    "fast_note_quarters": 0.5,
    "fast_leap_semitones": 9,
    "pitch_retention_review_threshold": 0.70,
    "assumptions": "Physical ranges are advanced limits, not comfortable tessituras. Preferred bands and the 10% exposure flag are editorial chamber-arrangement settings, not universal player limits. Breath/fast-leap thresholds are screening settings. Repeats are expanded for wind endurance.",
}


def _load(value):
    s = converter.parse(value) if isinstance(value, (str, Path)) else value
    if isinstance(value, (str, Path)) and Path(value).suffix.lower() in {
        ".xml",
        ".musicxml",
        ".mxl",
    }:
        restore_sound_tempos(s, value)
    return s.toSoundingPitch(inPlace=False)


def _events(part):
    return sorted(
        [
            (float(n.getOffsetInHierarchy(part)), float(n.quarterLength), n)
            for n in part.recurse().notes
        ],
        key=lambda x: (x[0], x[1]),
    )


def _material(score):
    return Counter(
        (
            m.number,
            round(float(n.getOffsetInHierarchy(m)), 6),
            round(float(n.quarterLength), 6),
            p.pitchClass,
        )
        for part in score.parts
        for m in part.getElementsByClass(stream.Measure)
        for n in m.recurse().notes
        for p in n.pitches
    )


def _structure(part):
    return [
        (
            m.number,
            round(float(m.offset), 6),
            round(float(m.highestTime), 6),
            [
                (side, b.direction, b.times)
                for side, b in (("left", m.leftBarline), ("right", m.rightBarline))
                if isinstance(b, bar.Repeat)
            ],
            [
                (float(t.offset), t.ratioString)
                for t in m.getElementsByClass(meter.TimeSignature)
            ],
        )
        for m in part.getElementsByClass(stream.Measure)
    ]


def _ensemble_structure(score):
    """Combine staff-local declarations without assuming the first staff leads."""
    structures = [_structure(p) for p in score.parts]
    if len({len(s) for s in structures}) != 1:
        return None
    result = []
    for aligned in zip(*structures):
        if any(s[:3] != aligned[0][:3] for s in aligned[1:]):
            return None
        repeats = sorted({tuple(r) for s in aligned for r in s[3]})
        meters = sorted({tuple(t) for s in aligned for t in s[4]})
        if len({r[0] for r in repeats}) != len(repeats) or len(
            {t[0] for t in meters}
        ) != len(meters):
            return None
        result.append((*aligned[0][:3], repeats, meters))
    return result


def _endings(part):
    ms = list(part.getElementsByClass(stream.Measure))
    indices = {id(m): i for i, m in enumerate(ms)}
    return {
        (str(s.number), tuple(indices.get(id(m), -1) for m in s.getSpannedElements()))
        for s in part.spannerBundle.getByClass(spanner.RepeatBracket)
    }


def _wind_runs(score):
    part = score.expandRepeats().parts[0]
    changes = {0.0: 120.0}
    for t in part.recurse().getElementsByClass(tempo.MetronomeMark):
        bpm = t.getQuarterBPM()
        if bpm:
            changes[float(t.getOffsetInHierarchy(part))] = bpm

    def seconds(a, b):
        points = [a] + sorted(t for t in changes if a < t < b) + [b]
        return sum(
            (v - u) * 60 / changes[max(t for t in changes if t <= u)]
            for u, v in itertools.pairwise(points)
        )

    runs, start, end, first_measure = [], None, None, None
    for offset, duration, n in _events(part):
        if not duration:
            continue
        if end is not None and seconds(end, offset) >= RUBRIC["breath_gap_seconds"]:
            runs.append({"measure": first_measure, "seconds": seconds(start, end)})
            start = None
        if start is None:
            start, first_measure = offset, n.measureNumber
        end = offset + duration
    if start is not None:
        runs.append({"measure": first_measure, "seconds": seconds(start, end)})
    return runs


def evaluate_trio(source, candidate):
    src, dst = _load(source), _load(candidate)
    if not src.parts or len(dst.parts) != 3:
        raise ValueError("Expected a source ensemble and alto/cello/bass candidate")
    source_material, candidate_material = _material(src), _material(dst)
    novel = candidate_material - source_material
    retained = sum((source_material & candidate_material).values()) / max(
        1, sum(source_material.values())
    )
    issues = []
    ranges = []
    register_usage = []
    fast = 0
    overlap = 0
    ties = 0
    for i, part in enumerate(dst.parts):
        ns = _events(part)
        all_pitches = [p.midi for _, _, n in ns for p in n.pitches]
        ranges.append([min(all_pitches), max(all_pitches)] if all_pitches else [])
        low, high = RUBRIC["sounding_ranges"][i]
        preferred_low, preferred_high = RUBRIC["preferred_sounding_ranges"][i]
        total_quarters = sum(duration * len(n.pitches) for _, duration, n in ns)
        low_quarters = high_quarters = 0.0
        for _, duration, n in ns:
            for p in n.pitches:
                low_quarters += duration if p.midi < preferred_low else 0
                high_quarters += duration if p.midi > preferred_high else 0
                if not preferred_low <= p.midi <= preferred_high:
                    issues.append(
                        {
                            "rule": "tessitura_review",
                            "part": i,
                            "measure": n.measureNumber,
                            "offset": float(n.offset),
                            "sounding_pitch": p.nameWithOctave,
                            "duration_quarters": duration,
                        }
                    )
        outside_fraction = (
            (low_quarters + high_quarters) / total_quarters if total_quarters else 0
        )
        register_usage.append(
            {
                "part": i,
                "preferred_sounding_range": [preferred_low, preferred_high],
                "pitched_quarters": total_quarters,
                "below_preferred_quarters": low_quarters,
                "above_preferred_quarters": high_quarters,
                "outside_preferred_fraction": outside_fraction,
                "needs_review": outside_fraction
                > RUBRIC["register_exposure_review_fraction"],
            }
        )
        previous, open_ties = None, {}
        for offset, duration, n in ns:
            loc = {"part": i, "measure": n.measureNumber, "offset": float(n.offset)}
            if any(not low <= p.midi <= high for p in n.pitches):
                issues.append({"rule": "range", **loc})
            if len(n.pitches) != 1:
                issues.append({"rule": "monophony", **loc})
            if duration and previous:
                old_offset, old_duration, old_n = previous
                if offset < old_offset + old_duration - 1e-6:
                    overlap += 1
                    issues.append({"rule": "overlap", **loc})
                leap = abs(n.pitches[0].midi - old_n.pitches[0].midi)
                if (
                    old_duration <= RUBRIC["fast_note_quarters"]
                    and leap > RUBRIC["fast_leap_semitones"]
                    and offset <= old_offset + old_duration + 1e-6
                ):
                    fast += 1
                    issues.append({"rule": "fast_leap", "semitones": leap, **loc})
            components = n.notes if isinstance(n, chord.Chord) else [n]
            for c in components:
                pitch, tie = c.pitch.midi, c.tie.type if c.tie else None
                if tie in ("stop", "continue"):
                    if pitch not in open_ties or abs(open_ties[pitch] - offset) > 1e-6:
                        ties += 1
                        issues.append({"rule": "orphan_tie_stop", **loc})
                    open_ties.pop(pitch, None)
                if tie in ("start", "continue"):
                    open_ties[pitch] = offset + duration
            if duration:
                previous = offset, duration, n
        ties += len(open_ties)
        for pitch in open_ties:
            issues.append({"rule": "orphan_tie_start", "part": i, "pitch": pitch})
    positions = {id(n) for n in dst.recurse()}
    broken_spans = sum(
        any(id(e) not in positions for e in sp.getSpannedElements())
        for sp in dst.spannerBundle
    )
    repeats_expandable = True
    try:
        runs = _wind_runs(dst)
    except repeat.ExpanderException as exc:
        repeats_expandable = False
        runs = []
        issues.append({"rule": "invalid_repeat_expansion", "detail": str(exc)})
    long_runs = [r for r in runs if r["seconds"] > RUBRIC["breath_warning_seconds"]]
    expected_structure = _ensemble_structure(src)
    structural = expected_structure is not None and all(
        _structure(p) == expected_structure for p in dst.parts
    )
    expected_endings = set().union(*(_endings(p) for p in src.parts))
    endings_preserved = all(_endings(p) == expected_endings for p in dst.parts)
    range_errors = sum(x["rule"] == "range" for x in issues)
    mono_errors = sum(x["rule"] == "monophony" for x in issues)
    gates = {
        "measure_rhythm_repeat_structure": structural,
        "repeat_endings_preserved": endings_preserved,
        "repeats_expandable": repeats_expandable,
        "no_new_pitch_class_onset_duration": not novel,
        "pitched_material_present": bool(candidate_material) or not source_material,
        "in_range": not range_errors,
        "monophonic": not (overlap or mono_errors),
        "ties_connected": not ties,
        "spanner_endpoints_present": not broken_spans,
    }
    return {
        "rubric": RUBRIC,
        "rubric_sha256": hashlib.sha256(
            json.dumps(RUBRIC, sort_keys=True).encode()
        ).hexdigest(),
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "eligible": all(gates.values()),
        "performance_ready": False,
        "gates": gates,
        "metrics": {
            "source_pitch_retention_fraction": retained,
            "retained_pitches": sum(candidate_material.values()),
            "source_pitches": sum(source_material.values()),
            "pitch_retention_needs_review": retained
            < RUBRIC["pitch_retention_review_threshold"],
            "sounding_ranges": ranges,
            "register_usage": register_usage,
            "tessitura_needs_review": any(r["needs_review"] for r in register_usage),
            "fast_leaps": fast,
            "max_wind_run_seconds": max((r["seconds"] for r in runs), default=0)
            if repeats_expandable
            else None,
            "wind_runs_over_12_seconds": len(long_runs) if repeats_expandable else None,
            "wind_breathing_gaps": max(0, len(runs) - 1)
            if repeats_expandable
            else None,
            "tie_errors": ties,
            "broken_spans": broken_spans,
        },
        "long_wind_runs": long_runs,
        "issues": issues,
        "limitations": [
            "No score directly comparable to the earlier duo grade.",
            "No acoustic balance, measured timbre, fingering or performer listening evaluation.",
            "Material comparison allows octave changes and redistribution, never changed onset/duration or added pitch classes.",
        ],
    }
