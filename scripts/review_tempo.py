"""Restore source playback tempos in the existing trio and compare rit policies.

Keeps the old arrangement/audio as the control. Does not rearrange any notes.
Run: uv run python scripts/review_tempo.py
"""

import argparse
import hashlib
import json
from pathlib import Path

from music21 import tempo

from music_arranger.core import fingerprint, load_score, written_score
from music_arranger.playback import write_midi_preview
from music_arranger.rules import ScoreClock
from music_arranger.trio_judge import evaluate_trio


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("sources/handel-halvorsen.musicxml")
    )
    parser.add_argument(
        "--arrangement",
        type=Path,
        default=Path("outputs/alto-trio/relay-two/score.musicxml"),
    )
    parser.add_argument("--output", type=Path, default=Path("outputs/tempo-review"))
    args = parser.parse_args()
    originals = {p: sha(p) for p in (args.source, args.arrangement)}
    source = load_score(args.source)
    score = load_score(args.arrangement, expected_parts=3)
    before = fingerprint(score)
    source_measures = [m.number for m in source.parts[0].getElementsByClass("Measure")]
    if any(
        [m.number for m in p.getElementsByClass("Measure")] != source_measures
        for p in score.parts
    ):
        raise ValueError("Tempo transfer requires matching source/arrangement bars")
    # This review transfers global upper-staff tempo instructions to their
    # retained copies in the arranged score; it does not infer new tempos.
    changes = []
    for repair in source.editorial["sound_tempo_repairs"]:
        if repair["part"] != 1:
            raise ValueError(
                "Review script only transfers upper-staff global tempo cues"
            )
        matched = 0
        for part_index, part in enumerate(score.parts):
            measure = part.measure(repair["measure"])
            for mark in measure.recurse().getElementsByClass(tempo.MetronomeMark):
                if (
                    abs(float(mark.getOffsetInHierarchy(measure)) - repair["offset"])
                    < 1e-3
                ):
                    if mark.number != repair["printed_number"]:
                        raise ValueError("Printed metronome differs from the source")
                    old = mark.getQuarterBPM()
                    mark.numberSounding = repair["sound_quarter_bpm"] / float(
                        mark.referent.quarterLength
                    )
                    changes.append(
                        {
                            **repair,
                            "arranged_part": part_index + 1,
                            "old_arrangement_quarter_bpm": old,
                        }
                    )
                    matched += 1
        if not matched:
            raise ValueError(f"Could not locate original tempo: {repair}")
    assert fingerprint(score) == before
    args.output.mkdir(parents=True, exist_ok=True)
    xml = args.output / "score.musicxml"
    if xml.resolve() in {p.resolve() for p in originals}:
        raise ValueError("Choose a separate output directory")
    written_score(score).write("musicxml", fp=xml)
    reloaded = load_score(xml, expected_parts=3)
    assert fingerprint(reloaded) == before
    for part, other in zip(score.parts, reloaded.parts):
        assert [
            t.getQuarterBPM()
            for t in part.recurse().getElementsByClass(tempo.MetronomeMark)
        ] == [
            t.getQuarterBPM()
            for t in other.recurse().getElementsByClass(tempo.MetronomeMark)
        ]
    evaluation = evaluate_trio(args.source, xml)
    assert evaluation["eligible"], evaluation["gates"]
    results = {}
    for mode in ("encoded", "smooth-rit"):
        directory = args.output / mode
        report = write_midi_preview(score, directory / "score.mid", tempo_mode=mode)
        assert all(t["max_active_notes"] == 1 for t in report["audit"]["tracks"])
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        results[mode] = report
    expanded = score.expandRepeats()
    clock = ScoreClock(expanded)
    timeline = [
        {
            "expanded_bar": index + 1,
            "measure": m.number,
            "start_seconds": clock.seconds(0, float(m.offset)),
            "end_seconds": clock.seconds(0, float(m.offset + m.highestTime)),
        }
        for index, m in enumerate(expanded.parts[0].getElementsByClass("Measure"))
    ]
    m29 = next(r for r in timeline if r["measure"] == 29)
    assert abs(m29["start_seconds"] - 82.909090909) < 1e-4
    summary = {
        "source_sha256": originals[args.source],
        "control_xml_sha256": originals[args.arrangement],
        "input_instruments": [p.partName for p in source.parts],
        "written_bars": len(source_measures),
        "notation_pitches_rhythms_ties_unchanged": True,
        "sound_tempo_repairs": changes,
        "score_musicxml_sha256": sha(xml),
        "independent_evaluation": evaluation,
        "encoded_timeline": timeline,
        "at_1m23": "Bar 29 starts at 82.909 seconds, still quarter=110. No tempo mark here; grace notes and quintuplets increase surface activity. Rubato text starts in bar 33 at 91.636 seconds and has no specified timing curve.",
        "modes": {mode: r["tempo_interpretation"] for mode, r in results.items()},
        "source_tempo_semantics": "https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/sound/",
    }
    assert all(sha(p) == original for p, original in originals.items())
    (args.output / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "restored_source_changes": len(source.editorial["sound_tempo_repairs"]),
                "at_1m23": summary["at_1m23"],
                "independent_eligible": evaluation["eligible"],
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
