"""Build and independently compare three full alto/cello/bass arrangements."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from music_arranger.core import TRIO_VARIANTS, arrange_trio, export, load_score
from music_arranger.trio_judge import _material, evaluate_trio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("sources/handel-halvorsen.musicxml")
    )
    parser.add_argument("--output", type=Path, default=Path("outputs/alto-trio"))
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--title", help="Verified work title for the derived editions")
    parser.add_argument(
        "--composer", help="Verified composer credit for the derived editions"
    )
    parser.add_argument(
        "--source-credit", help="Additional verified source attribution"
    )
    args = parser.parse_args()
    source_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    source = load_score(args.input, expected_parts=None)
    results = {}
    control = None
    for variant in TRIO_VARIANTS:
        score, report = arrange_trio(source, variant)
        corrections = {}
        if args.title:
            score.metadata.title = f"{args.title} — alto trio ({variant})"
            score.metadata.movementName = score.metadata.title
            corrections["title"] = args.title
        if args.composer:
            score.metadata.composer = args.composer
            corrections["composer"] = args.composer
        if args.source_credit:
            score.metadata.add("otherContributor", args.source_credit)
            corrections["source_credit"] = args.source_credit
        if corrections:
            report["editorial_metadata"] = corrections
        if control is None:
            control = _material(score)
        assert _material(score) == control, "Candidate changed the comparison material"
        export(score, report, args.output / variant, render=not args.no_render)
        evaluation = evaluate_trio(args.input, args.output / variant / "score.musicxml")
        if not evaluation["eligible"]:
            raise ValueError(f"{variant} failed integrity gates: {evaluation['gates']}")
        (args.output / variant / "judge.json").write_text(
            json.dumps(evaluation, indent=2) + "\n"
        )
        results[variant] = {
            "metrics": evaluation["metrics"],
            "eligible": evaluation["eligible"],
            "evaluator_sha256": evaluation["evaluator_sha256"],
            "rubric_sha256": evaluation["rubric_sha256"],
            "same_pitch_class_onset_duration_material_as_control": True,
        }
        print(variant, json.dumps(results[variant]), flush=True)
    assert source_hash == hashlib.sha256(args.input.read_bytes()).hexdigest()
    # A screening recommendation, not a universal musical quality ranking.
    selected = min(
        results,
        key=lambda name: (
            results[name]["metrics"]["tessitura_needs_review"],
            results[name]["metrics"]["max_wind_run_seconds"],
            results[name]["metrics"]["fast_leaps"],
        ),
    )
    comparison = {
        "source_sha256": source_hash,
        "source_measures_per_part": [
            len(p.getElementsByClass("Measure")) for p in source.parts
        ],
        "results": results,
        "recommended_starting_draft": selected,
        "selection_policy": "Among eligible same-material candidates, prefer no tessitura exposure warning, then shortest maximum uninterrupted saxophone passage, then fewest flagged fast leaps. Does not evaluate listening quality.",
        "limitations": "Ordinary monophonic alto; no multiphonics or circular breathing assumed. Source chord/voice reductions and octave folds are audited. No perceptual comparison to the duo.",
    }
    (args.output / "comparison.json").write_text(
        json.dumps(comparison, indent=2) + "\n"
    )
    with ZipFile(args.output.with_suffix(".zip"), "w", ZIP_DEFLATED) as archive:
        for p in sorted(args.output.rglob("*")):
            if p.is_file() and p.suffix in {".json", ".musicxml", ".pdf", ".mid"}:
                archive.write(p, p.relative_to(args.output))
        for name in (args.input.name, "provenance.json", "pdmx-record.json"):
            p = args.input.parent / name
            if p.exists():
                archive.write(p, "source/" + name)
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
