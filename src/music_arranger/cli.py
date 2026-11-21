"""Command-line entry points; all operations also work as Python functions."""

import argparse
import json
import sys
from pathlib import Path

from music_arranger.core import transpose_notation

from .core import (
    TRIO_VARIANTS,
    VARIANTS,
    arrange,
    arrange_trio,
    export,
    inspect_score,
    load_score,
)


def main():
    parser = argparse.ArgumentParser(
        description="TransScribe chamber music workbench"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("input", type=Path)
    ingest = sub.add_parser(
        "ingest", help="Acquire a source bundle with original files and provenance"
    )
    ingest.add_argument(
        "source",
        help="Local notation file, direct URL, MuseScore score URL or pdmx: edition ID",
    )
    ingest.add_argument("--output", type=Path, required=True)
    ingest.add_argument(
        "--transcribe-pdf",
        action="store_true",
        help="Run optional Audiveris OMR; candidates require review",
    )
    ingest.add_argument(
        "--provider",
        choices=("auto", "local", "url", "musescore", "pdmx"),
        default="auto",
    )
    sub.add_parser(
        "source-providers", help="List source adapters and format capabilities"
    )
    compare = sub.add_parser(
        "compare-transcription",
        help="Compare OMR notation with a reviewed symbolic reference",
    )
    compare.add_argument("reference", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", type=Path)
    transpose = sub.add_parser("transpose")
    transpose.add_argument("input", type=Path)
    transpose.add_argument("output", type=Path)
    transpose.add_argument("--semitones", type=int, required=True)
    run = sub.add_parser("arrange")
    run.add_argument("input", type=Path)
    run.add_argument("--variant", choices=VARIANTS, default="literature-chamber")
    run.add_argument("--output", type=Path, default=Path("outputs/custom"))
    run.add_argument("--no-render", action="store_true")
    trio = sub.add_parser(
        "trio",
        help="Reduce any source ensemble to alto saxophone, cello and double bass",
    )
    trio.add_argument("input", type=Path)
    trio.add_argument("--variant", choices=TRIO_VARIANTS, default="relay-two")
    trio.add_argument("--output", type=Path, default=Path("outputs/alto-trio/custom"))
    trio.add_argument("--no-render", action="store_true")
    demo = sub.add_parser("demo")
    demo.add_argument(
        "--input", type=Path, default=Path("sources/handel-halvorsen.musicxml")
    )
    demo.add_argument("--output", type=Path, default=Path("outputs/passacaglia"))
    demo.add_argument("--no-render", action="store_true")
    sub.add_parser("serve")
    rules = sub.add_parser(
        "rules", help="Cited deterministic rule catalog or score audit"
    )
    rules.add_argument("input", type=Path, nargs="?")
    from .workflow import ENSEMBLES, build_packet

    packet = sub.add_parser(
        "packet",
        help="Export and evaluate a control and candidate in a new rehearsal packet",
    )
    packet.add_argument(
        "input", type=Path, help="Symbolic score or ready ingest bundle"
    )
    packet.add_argument("--output", type=Path, required=True)
    packet.add_argument("--ensemble", choices=ENSEMBLES, default="duo")
    packet.add_argument("--variant")
    packet.add_argument("--no-render", action="store_true")
    pdmx = sub.add_parser(
        "pdmx-search",
        help="Find downloadable editions by title, composer and instrument",
    )
    pdmx.add_argument("query", nargs="?", default="")
    pdmx.add_argument("--composer", default="")
    pdmx.add_argument("--limit", type=int, default=10)
    pdmx.add_argument("--offset", type=int, default=0)
    ui = sub.add_parser("ui", help="Open the local arranging workbench in your browser")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--workspace", type=Path, default=Path("runs/workbench"))
    ui.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(sys.argv[1:] or ["ui"])
    if args.command == "ui":
        from .workbench import launch

        launch(args.workspace, args.port, not args.no_browser)
        return
    if args.command == "pdmx-search":
        from .acquisition.pdmx import search

        try:
            result = search(args.query, args.composer, args.limit, args.offset)
        except (ValueError, OSError, RuntimeError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
        return
    if args.command == "packet":
        try:
            result = build_packet(
                args.input, args.output, args.ensemble, args.variant, not args.no_render
            )
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
        return
    if args.command == "compare-transcription":
        from .transcription import compare_transcription

        result = compare_transcription(args.reference, args.candidate)
        if args.output:
            if args.output.resolve() in {
                args.reference.resolve(),
                args.candidate.resolve(),
            }:
                parser.error("Choose a separate comparison report path.")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return
    if args.command in {"ingest", "source-providers"}:
        from .acquisition import ingest_source, provider_catalog

        try:
            result = (
                provider_catalog()
                if args.command == "source-providers"
                else ingest_source(
                    args.source, args.output, args.provider, args.transcribe_pdf
                )
            )
        except (ValueError, OSError, RuntimeError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
        return
    if args.command == "serve":
        from .server import main as serve

        serve()
        return
    if args.command == "rules":
        from .rules import audit_rules, rule_catalog

        result = audit_rules(load_score(args.input)) if args.input else rule_catalog()
        print(json.dumps(result, indent=2))
        return
    source = load_score(
        args.input,
        expected_parts=None if args.command in {"inspect", "trio", "transpose"} else 2,
    )
    if args.command == "inspect":
        result = inspect_score(source)
    elif args.command == "transpose":
        if args.output.resolve() == args.input.resolve():
            parser.error("Choose an output path different from the source.")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        transpose_notation(source, semitones=args.semitones).write(
            "musicxml", fp=args.output
        )
        result = {"output": str(args.output.resolve()), "semitones": args.semitones}
    elif args.command == "arrange":
        score, report = arrange(source, args.variant)
        result = export(score, report, args.output, render=not args.no_render)
    elif args.command == "trio":
        score, report = arrange_trio(source, args.variant)
        result = export(score, report, args.output, render=not args.no_render)
    else:
        result = []
        for variant in VARIANTS:
            score, report = arrange(source, variant)
            result.append(
                export(score, report, args.output / variant, render=not args.no_render)
            )
        (args.output / "manifest.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
