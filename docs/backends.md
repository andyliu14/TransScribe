# Backend contracts

The local browser workbench, CLI and stdio MCP are interfaces for the same Python functions. A local client can call `music_arranger.workflow.build_packet(source, output, ensemble, variant, render)` directly. The return value contains the published directory, ZIP and manifest paths, plus each edition’s eligibility. Each packet uses a fresh destination. If export or evaluation raises, its temporary files are discarded.

| Stage | Implementation | Handoff |
| --- | --- | --- |
| Acquire | Local, direct URL, MuseScore or selected PDMX edition | Files and provenance in a staged source bundle |
| Normalize | MusicXML/MXL reader, optional LibreScore converter | Original bytes, normalized score, transformation log and hashes |
| Transcribe | Optional Audiveris backend | Raw output, editable OMR project and review status. Correct and re-ingest the notation before building a packet. |
| Arrange | Ensemble policy and pinned upstream transposition | Sounding-pitch score and an edit report |
| Export | Music21, MIDI preview writer, optional Verovio/Cairo engraving | Written-pitch MusicXML and PDFs for score and players, page SVGs, checked MIDI |
| Evaluate | Duo or trio evaluator | Independent diagnostics from reading the exported score against the source |
| Package | Packet builder | Relative file inventory, review index, checksums and archive |

## Add a source provider

Implement `accepts(source)` and `acquire(source, output)` alongside the providers in `src/music_arranger/acquisition/providers.py`. Write acquired files beneath the supplied staging directory and return `Acquired(files, provenance)`. Add the provider to `PROVIDERS`, its capability description to `provider_catalog`, and its name to the CLI provider choices. The shared ingestion step selects the format, validates notation and publishes the result. If introducing a new format, also implement normalization there. Exercise the ordinary CLI entry point on a valid input and a failed acquisition.

## Add an ensemble

Register the target in `workflow.ENSEMBLES` with its accepted source part count, variant table, control, default, arrangement function and evaluator. The arrangement function receives the same source object for each edition. Work on a copy and return a sounding-pitch score plus the measure-level report. Include one stable, unique filename stem per player under `part_files` and final critique counts under `iterations[-1].critique.counts`.

Use the destination instruments’ transposition metadata when assigning roles. Check clefs and beaming in `render.prepare_written_notation` when adding a target. Existing engraving policies include fixed treble clef for saxophone and rebeaming of redistributed trios. Verify a written score and an extracted part by reloading them at concert pitch.

The independent evaluator accepts source and candidate file paths. Return a JSON-serializable object with an `eligible` boolean and enough measure-level evidence for a musician to investigate. Include a control, an input with different source instrumentation and a case where the candidate fails an integrity check. Document each target’s musical assumptions in the skill references.

## Reproduce an edition

Keep the acquisition bundle alongside the rehearsal ZIP. Its manifest preserves original source attribution, transformations and the normalized notation. Match the packet’s source digest before comparing revisions. The packet manifest records package versions and hashes of the delivered files. MusicXML identifiers and engraving metadata can vary across runs. Compare musical fingerprints, edit reports and evaluation results when deciding whether two editions differ musically.
