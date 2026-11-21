"""Reproducible proposal/evaluation loop with a frozen, separate judge.

The recipes replay the independently reviewed experiment; they do not purport
to discover musical policies with an LLM. New recipes require separate review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from music21 import chord, converter, stream

from .core import arrange, export, load_score
from .judge import RUBRIC, RUBRIC_HASH, evaluate
from .render import render_pdf

RECIPES = {
    "reference": ("reference", 0.0),
    "chamber": ("chamber", 0.0),
    "iteration-1": ("joint-chamber", 0.0),
    "iteration-2": ("joint-chamber", 3.0),
    "iteration-3": ("joint-chamber", 12.0),
}


def bundle_results(output):
    bundle = output.with_suffix(".zip")
    with ZipFile(bundle, "w", ZIP_DEFLATED) as archive:
        for path in output.rglob("*"):
            if path.is_file() and path.suffix in {
                ".musicxml",
                ".pdf",
                ".json",
                ".md",
                ".mid",
            }:
                archive.write(path, path.relative_to(output))
        selection = output / "selection.json"
        if selection.exists():
            source = Path(json.loads(selection.read_text())["source"])
            if source.is_file():
                archive.write(source, "source/" + source.name)
                for name in ("provenance.json", "pdmx-record.json"):
                    companion = source.parent / name
                    if companion.is_file():
                        archive.write(companion, "source/" + name)
    return bundle


def material_signature(path):
    """Reject improvements achieved by removing, retiming or reharmonizing notes.

    Pitch classes are kept in component order; only octave relocations are free.
    Includes rests and per-component ties. Source reduction is measured against
    the existing chamber edition, so every revision must retain its material.
    """
    score = converter.parse(path).toSoundingPitch(inPlace=False)
    return [
        [
            (
                m.number,
                round(float(n.getOffsetInHierarchy(m)), 7),
                round(float(n.quarterLength), 7),
                tuple(p.midi % 12 for p in n.pitches),
                tuple(
                    c.tie.type if c.tie else None
                    for c in (n.notes if isinstance(n, chord.Chord) else [n])
                ),
            )
            for m in part.getElementsByClass(stream.Measure)
            for n in m.recurse().notesAndRests
        ]
        for part in score.parts
    ]


def compare(source: Path, output: Path, *, generate=True, render=True):
    source = source.resolve()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    (output / "rubric.json").write_text(
        json.dumps({"rubric": RUBRIC, "sha256": RUBRIC_HASH}, indent=2) + "\n"
    )
    score = load_score(source) if generate else None
    results = {}
    baseline_signature = None
    for name, (variant, rapid_motion) in RECIPES.items():
        directory = output / name
        if generate:
            candidate, report = arrange(score, variant, rapid_motion=rapid_motion)
            export(candidate, report, directory, render=False)
        xml = directory / "score.musicxml"
        report = json.loads((directory / "report.json").read_text())
        if not report.get("roundtrip_verified"):
            raise ValueError(f"Unverified export: {name}")
        judged = evaluate(source, xml)
        if judged["rubric_sha256"] != RUBRIC_HASH:
            raise ValueError("Evaluator rubric changed during experiment")
        signature = material_signature(xml)
        if name == "chamber":
            baseline_signature = signature
        same_material = name == "reference" or signature == baseline_signature
        judged["same_material_as_chamber"] = (
            same_material if name != "reference" else None
        )
        judged["selection_eligible"] = judged["eligible"] and same_material
        (output / f"judge-{name}.json").write_text(json.dumps(judged, indent=2) + "\n")
        results[name] = judged
        print(
            f"{name}: {judged['heuristic_score']:.4f}; eligible={judged['selection_eligible']}",
            flush=True,
        )
    baseline = results["chamber"]
    # Never trade range or multiple-stop exposure for a better aggregate score.
    eligible = [
        name
        for name, result in results.items()
        if name != "reference"
        and result["selection_eligible"]
        and all(
            (result["metrics"][metric]["penalty"] or 0)
            <= (baseline["metrics"][metric]["penalty"] or 0) + 1e-8
            for metric in ("range", "multiple_stops")
        )
    ]
    if not eligible:
        raise ValueError("No candidate passed the independent and material gates")
    chosen = max(eligible, key=lambda name: results[name]["heuristic_score"])
    regressions = {
        metric: {
            "baseline": baseline["metrics"][metric]["penalty"],
            "selected": results[chosen]["metrics"][metric]["penalty"],
        }
        for metric in RUBRIC["weights"]
        if (results[chosen]["metrics"][metric]["penalty"] or 0)
        > (baseline["metrics"][metric]["penalty"] or 0) + 1e-8
    }
    selection = {
        "selected": chosen,
        "reason": "Highest frozen heuristic score among integrity/material/range/stop eligible candidates; not a musician endorsement.",
        "regressions_against_chamber": regressions,
        "recipes": {
            name: {"variant": v, "rapid_motion": r} for name, (v, r) in RECIPES.items()
        },
        "source": str(source),
        "source_sha256": source_hash,
        "rubric_sha256": RUBRIC_HASH,
        "evaluator_sha256": results[chosen]["evaluator_sha256"],
        "optimizer_sha256": hashlib.sha256(
            Path(__file__).with_name("core.py").read_bytes()
        ).hexdigest(),
        "renderer_sha256": hashlib.sha256(
            Path(__file__).with_name("render.py").read_bytes()
        ).hexdigest(),
        "experiment_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "generated_this_run": generate,
        "limitations": "Same-piece development comparison, no held-out corpus or blind musician/listening test. Score weights are subjective; inspect metric tradeoffs and PDFs.",
    }
    if render:
        # Render each proposal's full score; the selected one also gets parts.
        selection["score_rendering"] = {
            name: render_pdf(output / name / "score.musicxml") for name in RECIPES
        }
        selection["rendering"] = {
            name: render_pdf(output / chosen / f"{name}.musicxml")
            for name in ("cello", "double-bass")
        }
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("Input changed during experiment")
    (output / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    lines = [
        "# Independently judged Passacaglia revisions",
        "",
        f"Selected computational draft: **{chosen}**. [Full score]({chosen}/score.pdf), [cello]({chosen}/cello.pdf), [double bass]({chosen}/double-bass.pdf).",
        "",
        "The separate judge does not import optimizer scoring. Its rubric is frozen and hashed; source and candidate file hashes accompany evaluations. Numbers below are weighted diagnostic penalties (lower is better), not perceived-quality ratings.",
        "",
        "| Proposal | Heuristic score ↑ | Low spacing ↓ | Wide spacing ↓ | New crossing ↓ | Contour change ↓ | Fast motion ↓ | Gates |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, result in results.items():
        values = " | ".join(
            f"{result['metrics'][m]['penalty']:.6f}"
            for m in (
                "low_spacing",
                "wide_spacing",
                "new_crossing",
                "contour_change",
                "fast_motion",
            )
        )
        lines.append(
            f"| {name} | {result['heuristic_score']:.4f} | {values} | {'pass' if result['selection_eligible'] else 'fail'} |"
        )
    lines += [
        "",
        "Iteration 1 adds paired register search. The independent review found spacing/crossing gains with a rapid-movement tradeoff. Iteration 2 adds a boundary-speed cost of 3; iteration 3 tests a stronger cost of 12. The evaluator's weights remain unchanged. All attempts are retained, including rejected ones.",
        "",
        f"Selected candidate's metric regressions versus chamber: `{json.dumps(regressions)}`.",
        "",
        "All revisions must keep the chamber edition's pitches modulo octaves, event timing, rests and tie types. Thus the loop cannot improve its score by deleting difficult notes. Range and multiple-stop exposure may not increase at selection. Source-level repeat/meter/timing gates are checked separately.",
        "",
        "The source has 98 written measures for violin and viola. Work is at sounding pitch; bass export sounds an octave below written. Source chords are reduced by the existing chamber policy. Remaining high cello passages, multiple stops, grace notes, source harmonics and bowings require musician review. Some extracted hairpins may be omitted by the existing exporter. The judge uses outer voices, excludes grace-note exposure, and does not model timbre or actual fingering; its fast-motion proxy ignores intervening rests. See independent-review.md and the per-measure JSON findings.",
        "",
        "This is a development comparison on one piece. It does not establish generalization or performance readiness.",
        "",
    ]
    (output / "README.md").write_text("\n".join(lines))
    bundle_results(output)
    return selection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=Path("sources/handel-halvorsen.musicxml")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/judged-passacaglia")
    )
    parser.add_argument(
        "--evaluate-existing",
        action="store_true",
        help="Rejudge existing exports; do not regenerate proposals",
    )
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    result = compare(
        args.input,
        args.output,
        generate=not args.evaluate_existing,
        render=not args.no_render,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
