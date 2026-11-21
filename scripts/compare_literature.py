"""Generate the cited-rule candidate and compare with fixed existing controls."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from music_arranger.core import arrange, export, fingerprint, inspect_score, load_score
from music_arranger.experiment import material_signature
from music_arranger.judge import evaluate
from music_arranger.rules import audit_rules, rule_catalog, rule_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("sources/handel-halvorsen.musicxml")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/literature-passacaglia")
    )
    parser.add_argument(
        "--control",
        type=Path,
        default=Path("outputs/passacaglia/chamber/score.musicxml"),
    )
    parser.add_argument(
        "--previous",
        type=Path,
        default=Path("outputs/judged-passacaglia/iteration-1/score.musicxml"),
    )
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument(
        "--reuse-candidate",
        action="store_true",
        help="Reaudit an existing candidate without regenerating its notation",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    original_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    source = load_score(args.input)
    candidate_path = args.output / "candidate/score.musicxml"
    if args.reuse_candidate:
        candidate = load_score(candidate_path)
        report_path = args.output / "candidate/report.json"
        report = json.loads(report_path.read_text())
        if not report.get("roundtrip_verified"):
            raise ValueError("Existing candidate lacks round-trip verification")
        report["literature_rules"] = audit_rules(candidate)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    else:
        candidate, report = arrange(source, "literature-chamber")
        export(candidate, report, args.output / "candidate", render=not args.no_render)
    paths = {
        "chamber": args.control,
        "previous_selected": args.previous,
        "literature": candidate_path,
    }
    results = {}
    control_signature = material_signature(args.control)
    for name, path in paths.items():
        score = load_score(path)
        judged = evaluate(args.input, path)
        rules = audit_rules(score)
        unchanged_material = material_signature(path) == control_signature
        if not judged["eligible"] or not unchanged_material:
            raise ValueError(f"Integrity or same-material guard failed for {name}")
        results[name] = {
            "score_path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "independent_heuristic_score": judged["heuristic_score"],
            "independent_metrics": judged["metrics"],
            "rule_metrics": rules["metrics"],
            "same_material_as_chamber": unchanged_material,
        }
        (args.output / f"{name}-judge.json").write_text(
            json.dumps(judged, indent=2) + "\n"
        )
        (args.output / f"{name}-rules.json").write_text(
            json.dumps(rules, indent=2) + "\n"
        )
        print(name, json.dumps(results[name]), flush=True)
    comparison = {
        "source_sha256": original_hash,
        "candidate_generated_this_run": not args.reuse_candidate,
        "source": inspect_score(source),
        "rulebook_sha256": rule_hash(),
        "results": results,
        "candidate_sounding_events_equal_previous_selected": fingerprint(candidate)
        == fingerprint(load_score(args.previous)),
        "independent_judge_unchanged": "Existing judge.py is evaluated separately; its prior rubric is not rewritten to favor the new model.",
        "interpretation": "Published formulas/facts plus explicitly labelled implementation assumptions. Similar or identical output is not evidence of universal optimality; no musician/listening validation.",
    }
    assert original_hash == hashlib.sha256(args.input.read_bytes()).hexdigest()
    (args.output / "comparison.json").write_text(
        json.dumps(comparison, indent=2) + "\n"
    )
    (args.output / "rulebook.json").write_text(
        json.dumps(rule_catalog(), indent=2) + "\n"
    )
    with ZipFile(args.output.with_suffix(".zip"), "w", ZIP_DEFLATED) as archive:
        for path in args.output.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".musicxml", ".pdf", ".mid"}:
                archive.write(path, path.relative_to(args.output))
        archive.write(args.input, "source/" + args.input.name)
        for name in ("provenance.json", "pdmx-record.json"):
            path = args.input.parent / name
            if path.exists():
                archive.write(path, "source/" + name)
    print(json.dumps(comparison, indent=2))


if __name__ == "__main__":
    main()
