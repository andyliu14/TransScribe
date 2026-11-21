---
name: transscribe
description: Find PDMX score editions and prepare rehearsal editions from symbolic scores using the music arranger CLI or MCP. Use for cello/bass duos, alto/cello/bass reductions, part extraction in these editions, and instrument-specific review.
---

Work from the harness checkout or use its registered MCP server. Check the requested target, edition, player level and available source. Preserve the original and its credits.

1. When given a title or composer, use `search_pdmx_scores` or `uv run --locked transscribe pdmx-search "TITLE INSTRUMENTS"`. Compare edition metadata and license flags, then pass the selected `pdmx:` source ID to ingestion. For native scores, URLs or PDFs use `ingest_score_source` or `uv run --locked transscribe ingest SOURCE --output BUNDLE`. Inspect the manifest. A PDF or OMR candidate needs transcription review before arranging. For local MusicXML/MXL, pass the file directly.
2. Use `inspect_score_file` on the symbolic file. Check part order and written measure counts. A duo expects two source parts with the upper role first. The trio can reduce aligned pitched ensembles. Record the cost of omitted voices.
3. Use `build_rehearsal_packet` with path, new output_dir and ensemble, or `uv run --locked transscribe packet INPUT --ensemble trio --output NEW_DIRECTORY`. It generates the control first. Work at sounding pitch. Export writes transposing parts.
4. Read both evaluation.json files, report.json edits and manifest.json. Follow specific issues to the bars. Compare onsets, tie chains, returns after repeats, phrase handoffs and exposed register shifts. Listen to the MIDI and inspect the score and extracted part PDFs for clef changes, collisions, breathing opportunities and page turns. Report skipped checks and unresolved bars.
5. Return the rehearsal.zip and the review findings. If revising, select a fresh packet directory and compare with the previous edition.

Read the relevant player guide when reviewing a line or advising on manual adaptations. Automated targets are the duo and trio described above.

- [Saxophones](references/saxophone.md)
- [Cello](references/cello.md)
- [Double bass](references/double-bass.md)
- [Violin](references/violin.md)
- [Clarinet](references/clarinet.md)
- [Flute](references/flute.md)
- [Piano](references/piano.md)
- [Guitar](references/guitar.md)
