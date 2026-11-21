"""Create a portable score bundle and a short comparison from verified outputs."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs/passacaglia"
variants = ["reference", "chamber", "study-d-minor"]
lines = [
    "# Passacaglia for cello and double bass",
    "",
    "Complete computational rehearsal editions after the violin–viola transcription.",
    "",
    "| Edition | Sounding cello MIDI range | Sounding bass MIDI range | Range warnings | Chord-review events | Fast leaps | Score pages |",
    "| --- | --- | --- | --- | --- | --- | --- |",
]
for name in variants:
    report = json.loads((OUTPUT / name / "report.json").read_text())
    if not report["roundtrip_verified"]:
        raise ValueError(f"{name} is not verified")
    parts = report["result"]["parts"]
    counts = report["iterations"][-1]["critique"]["counts"]
    lines.append(
        f"| {name} | {parts[0]['range_midi']} | {parts[1]['range_midi']} | {counts['range']} | {counts['double_stop_review']} | {counts['fast_leap']} | {report['rendering']['score']['pages']} |"
    )
lines += [
    "",
    "Start with `chamber/score.pdf` for the G-minor reduced-bass edition. The reference retains original multiple stops; the D-minor study edition reduces both parts to single notes. Separate parts, editable MusicXML, and a MIDI audition accompany each score.",
    "",
    "Double bass uses standard orchestral tuning and sounds an octave below its notation. MIDI is at sounding pitch and expands printed repeats. These are advanced rehearsal drafts; range checks do not establish fingering feasibility or performance readiness. Source fingerings/string numbers are removed. Original fast rhythms remain.",
    "",
    "A few hairpins in extracted reduced parts may be omitted by the exporter/renderer; see report.json review items. Notes, rhythms and bass pitch conventions pass the round-trip checks.",
    "",
    "Each report.json contains the source/result counts, transformation history and measure-specific review items. The source is the PDMX CC0-marked violin–viola transcription (record 2231131), with license_conflict=False. See the bundled provenance.json for retrieval details and checksum.",
    "",
]
(OUTPUT / "README.md").write_text("\n".join(lines))
bundle = ROOT / "outputs/passacaglia-arrangements.zip"
with ZipFile(bundle, "w", ZIP_DEFLATED) as z:
    for path in OUTPUT.rglob("*"):
        if path.is_file() and path.suffix in {
            ".pdf",
            ".musicxml",
            ".mid",
            ".json",
            ".md",
        }:
            z.write(path, path.relative_to(OUTPUT))
    for name in (
        "provenance.json",
        "pdmx-record.json",
        "handel-halvorsen-violin-viola.mxl",
        "handel-halvorsen.musicxml",
        "handel-halvorsen.repairs.json",
    ):
        z.write(ROOT / "sources" / name, "source/" + name)
print(bundle)
