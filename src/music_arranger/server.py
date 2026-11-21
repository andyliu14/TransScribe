"""Local stdio MCP interface for external agents to inspect, arrange and revise."""

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from music_arranger.core import transpose_notation

from .core import (
    VARIANTS,
    arrange,
    arrange_trio,
    critique,
    export,
    inspect_score,
    load_score,
)

mcp = FastMCP("TransScribe")


@mcp.tool()
def score_source_providers() -> dict:
    """List acquisition adapters, format preferences and LibreScore capabilities."""
    from .acquisition import provider_catalog

    return provider_catalog()


@mcp.tool()
def search_pdmx_scores(
    query: str = "", composer: str = "", limit: int = 10, offset: int = 0
) -> dict:
    """Search PDMX titles, composers and instrument names. Pass a chosen source ID to ingest_score_source."""
    from .acquisition.pdmx import search

    return search(query, composer, limit, offset)


@mcp.tool()
def ingest_score_source(
    source: str, output_dir: str, provider: str = "auto", transcribe_pdf: bool = False
) -> dict:
    """Acquire local notation, a direct or MuseScore URL, or a pdmx: search result.

    Preserve original files/hashes and create a separate MusicXML normalization.
    MSCZ/MSCX conversion uses the pinned LibreScore engine. Current LibreScore
    URL downloads provide PDFs: return needs_transcription, never pretend MIDI
    is preserved notation. With transcribe_pdf=True, run pinned Audiveris and
    return MusicXML candidates with needs_review status, never automatic ready.
    Refuse to overwrite an existing nonempty bundle.
    """
    from .acquisition import ingest_source

    return ingest_source(source, output_dir, provider, transcribe_pdf)


@mcp.tool()
def compare_score_transcription(reference_path: str, candidate_path: str) -> dict:
    """Compare an OMR candidate with reviewed notation, reporting note/rhythm/tie differences.

    Preserves both inputs and tolerates unaligned candidate measures for diagnosis.
    Never claims that structural validity is equivalent to accurate transcription.
    """
    from .transcription import compare_transcription

    return compare_transcription(reference_path, candidate_path)


@mcp.tool()
def inspect_score_file(path: str) -> dict:
    """Read an aligned MusicXML/MXL/MIDI score; return counts and sounding ranges."""
    return inspect_score(load_score(path, expected_parts=None))


@mcp.tool()
def arrangement_policies() -> dict:
    """Available cello/bass policies. Use reference as control and compare critiques."""
    return VARIANTS


@mcp.tool()
def arrange_duo(
    path: str, output_dir: str, variant: str = "literature-chamber", render: bool = True
) -> dict:
    """Propose, critique, reduce chords and optimize registers; export score/parts/MIDI/audit.

    Advanced four-string bass in E–A–D–G orchestral tuning. Double-bass notation
    sounds an octave lower. Writes to output_dir; does not modify the input.
    """
    score, report = arrange(load_score(path), variant)
    return export(score, report, Path(output_dir), render=render)


@mcp.tool()
def critique_duo(path: str) -> dict:
    """Evaluate a cello/bass score at sounding pitch. Return actionable measure warnings."""
    return critique(load_score(path))


@mcp.tool()
def arrange_alto_trio(
    path: str, output_dir: str, variant: str = "relay-two", render: bool = True
) -> dict:
    """Reduce any pitched source ensemble to E-flat alto saxophone, cello and bass.

    Policies: sax-led (control), relay-four, relay-two. Keep a source-pitch audit,
    respect ties and retained phrase spans, and export written score/parts/MIDI.
    Accepts solo, grand-staff piano, duet, trio, quartet, sextet and larger scores.
    Source instruments need not match the target. Omitted voices are audited.
    Independent diagnostics include range, material and wind breathing gaps.
    """
    score, report = arrange_trio(load_score(path, expected_parts=None), variant)
    return export(score, report, Path(output_dir), render=render)


@mcp.tool()
def evaluate_alto_trio(source_path: str, candidate_path: str) -> dict:
    """Independent diagnostics for an alto/cello/bass trio; no overall quality grade."""
    from .trio_judge import evaluate_trio

    return evaluate_trio(source_path, candidate_path)


@mcp.tool()
def evaluate_arrangement(source_path: str, candidate_path: str) -> dict:
    """Independent frozen heuristic judge with source-relative integrity gates.

    Reads exported notation at sounding pitch. Does not use optimizer costs.
    Returns diagnostic scores and issues, not performance certification.
    """
    from .judge import evaluate

    return evaluate(source_path, candidate_path)


@mcp.tool()
def literature_rules(path: str | None = None) -> dict:
    """Cited deterministic rule catalog, or a score audit with rule witnesses.

    Distinguishes published formulas/facts from our weights and assumptions.
    This supplements rather than modifies the independent frozen judge.
    """
    from .rules import audit_rules, rule_catalog

    return rule_catalog() if path is None else audit_rules(load_score(path))


@mcp.tool()
def transpose_score(path: str, output_path: str, semitones: int) -> dict:
    """Transpose all source parts using the cloned music21 MCP tool."""
    output = Path(output_path)
    if output.resolve() == Path(path).resolve():
        raise ValueError("Choose an output path different from the source.")
    if output.suffix.lower() not in (".musicxml", ".xml"):
        raise ValueError("output_path must end in .musicxml or .xml")
    output.parent.mkdir(parents=True, exist_ok=True)
    score = transpose_notation(
        load_score(path, expected_parts=None), semitones=semitones
    )
    score.write("musicxml", fp=output)
    return {"output": str(output.resolve()), "analysis": inspect_score(score)}


@mcp.tool()
def build_rehearsal_packet(
    path: str,
    output_dir: str,
    ensemble: str = "duo",
    variant: str | None = None,
    render: bool = True,
) -> dict:
    """Build a control and candidate with independent evaluations and a checksummed ZIP inventory.

    Pass a symbolic score or ready acquisition bundle, a new output directory,
    and the duo or trio target. Returned eligibility flags require musician review.
    """
    from .workflow import build_packet

    return build_packet(path, output_dir, ensemble, variant, render)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
