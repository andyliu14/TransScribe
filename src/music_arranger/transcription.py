"""Compare an OMR candidate with independently reviewed symbolic notation.

Written measure numbers may differ at pickups, so comparisons use measure order.
This diagnostic never marks a PDF transcription reviewed automatically.
"""

from collections import Counter
from pathlib import Path

from music21 import bar, chord, converter, stream


def _read(path):
    return converter.parse(Path(path)).toSoundingPitch(inPlace=False)


def _inventory(score):
    notes, rests, structure = Counter(), Counter(), []
    for pi, part in enumerate(score.parts):
        part_structure = []
        for mi, measure in enumerate(part.getElementsByClass(stream.Measure)):
            part_structure.append(
                {
                    "quarters": float(measure.highestTime),
                    "meters": [
                        (float(t.offset), t.ratioString)
                        for t in measure.getElementsByClass("TimeSignature")
                    ],
                    "repeats": [
                        (side, mark.direction, mark.times)
                        for side, mark in (
                            ("left", measure.leftBarline),
                            ("right", measure.rightBarline),
                        )
                        if isinstance(mark, bar.Repeat)
                    ],
                }
            )
            for event in measure.recurse().notesAndRests:
                position = (
                    pi + 1,
                    mi + 1,
                    round(float(event.getOffsetInHierarchy(measure)), 6),
                    round(float(event.quarterLength), 6),
                )
                if event.isRest:
                    rests[position] += 1
                else:
                    for n in event.notes if isinstance(event, chord.Chord) else [event]:
                        if n.pitches:
                            notes[
                                (
                                    *position,
                                    n.pitch.nameWithOctave,
                                    n.tie.type if n.tie else None,
                                )
                            ] += 1
        structure.append(part_structure)
    return notes, rests, structure


def compare_transcription(reference, candidate):
    """Report event differences without requiring already aligned OMR measures."""
    expected, actual = _read(reference), _read(candidate)
    en, er, es = _inventory(expected)
    an, ar, acs = _inventory(actual)

    def differences(counter):
        return [
            {
                "part": event[0],
                "measure_index": event[1],
                "offset": event[2],
                "duration": event[3],
                "pitch": event[4] if len(event) > 4 else None,
                "tie": event[5] if len(event) > 5 else None,
                "count": count,
            }
            for event, count in counter.items()
        ]

    gates = {
        "same_part_count": len(expected.parts) == len(actual.parts),
        "same_measure_counts": [len(p) for p in es] == [len(p) for p in acs],
        "same_durations_meters_repeats": es == acs,
        "exact_pitches_onsets_durations_ties": en == an,
        "exact_rest_events": er == ar,
    }
    return {
        "reference": str(Path(reference).resolve()),
        "candidate": str(Path(candidate).resolve()),
        "matches_checked_notation": all(gates.values()),
        "review_required": True,
        "checks": gates,
        "reference_measures_per_part": [len(p) for p in es],
        "candidate_measures_per_part": [len(p) for p in acs],
        "reference_note_events": en.total(),
        "candidate_note_events": an.total(),
        "matched_note_events": (en & an).total(),
        "missing_or_changed_notes": differences(en - an),
        "extra_or_changed_notes": differences(an - en),
        "missing_or_changed_rests": differences(er - ar),
        "extra_or_changed_rests": differences(ar - er),
        "limitations": [
            "The reference must be independently reviewed; agreement alone is not proof of accuracy.",
            "Parts are matched by staff order. Measures are matched by ordinal, not printed number.",
            "Dynamics, articulations, text, slurs, wedges and repeat-ending brackets still require visual review.",
        ],
    }
