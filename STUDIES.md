# Arranging studies and backend reference

The first project is Handel–Halvorsen's complete **Passacaglia for violin and viola**, using a machine-readable transcription from [PDMX](https://zenodo.org/records/15571083). The raw source, normalized MusicXML, historical reference PDF, repair log and dataset record are retained in `sources/`. The transcription contains 98 written measures (122 with repeats). Twenty-one rounded septuplet durations in violin bars 32–34 are normalized using their encoded 7:4 ratios; no pitches are altered during ingestion. This is the string duet, not the short piano adaptation commonly circulated under the same title.

## Reproduce the historical Passacaglia study

The following sections document the research checkout. Archived scores and outputs referenced here are separate from the source release.


```bash
# In a fresh checkout, restore the reviewed third-party clone first:
python3 scripts/bootstrap_vendor.py
uv sync --locked

# To reproduce the source download if needed:
uv run python scripts/fetch_source.py

uv run transscribe inspect sources/handel-halvorsen.musicxml
uv run transscribe demo
```

`demo` writes five complete editions to `outputs/passacaglia/`. Each contains `score.pdf`, separate `cello.pdf` and `double-bass.pdf`, editable MusicXML for all three, page SVGs, a MIDI audition, and `report.json` with every alteration and critique stage. The original three editions are retained; the independently judged revisions are in `outputs/judged-passacaglia/`, and the cited-rule comparison is in `outputs/literature-passacaglia/`. The source archive is about 1.9 GB; the downloader streams until the selected small score is found and does not unpack the dataset.

| Variant | Musical treatment |
| --- | --- |
| `reference` | G minor. Both original parts sound one octave lower; original chords retained. A fidelity/control edition with difficult bass multiple stops. |
| `chamber` | G minor. Bass chord/voice reduction; cello chords retained; measure-level register optimization. |
| `joint-chamber` | G minor. Same musical material as chamber, with paired register search accounting for simultaneous spacing, source crossings and contour continuity. |
| `literature-chamber` | Default for `arrange` and MCP. Same material, using cited spectral/streaming models and necessary string-assignment constraints. |
| `study-d-minor` | D minor. Both parts reduced to single musical strands, followed by register optimization. Original rhythmic difficulty remains. |

The assumed instruments are a standard cello and a four-string double bass in orchestral E–A–D–G tuning, for advanced players. Some extracted reduced parts still trigger a degenerate/unclosed hairpin warning in Music21/Verovio; that individual hairpin may be omitted. This is retained as a review item in the reports.

These are computational rehearsal editions: fast passages, original harmonics, cello multiple stops and bowings still need player review. The study version reduces texture, not tempo or rhythmic difficulty. Reports distinguish mechanical checks from musical judgment.

## Arrange your own score

Input: `.musicxml`, `.xml`, `.mxl`, or `.mid`/`.midi`. The duo command still requires two parts, upper instrument first; `trio` accepts any number of aligned source parts and selects roles by musical activity/register rather than staff order. MusicXML is preferred because it retains notation and expression. MIDI cannot recover source bowings, slurs or engraved rhythms reliably. A PDF must first be transcribed or converted through OMR and corrected; a downloaded PDF is not already symbolic music.

```bash
uv run transscribe arrange your-duet.musicxml --variant literature-chamber --output outputs/my-duet
uv run transscribe transpose your-duet.musicxml outputs/down-a-tone.musicxml --semitones -2
uv run transscribe demo --no-render
```

The source file is not modified. Re-running a command replaces the same named output files.

## Modular source ingestion

```bash
# Optional MuseScore tools; exact versions/integrity are in package-lock.json.
npm ci --ignore-scripts --prefix tools/librescore

uv run transscribe source-providers
uv run transscribe ingest my-score.mscz --output sources/my-score
uv run transscribe trio sources/my-score/normalized/score.musicxml \
  --output outputs/my-trio
```

`acquisition/providers.py` supplies independent local-file, direct-file-URL and MuseScore-URL adapters. `acquisition/librescore.py` isolates optional subprocesses; `acquisition/__init__.py` selects formats, validates notation and publishes a source bundle. Musical decisions remain in `core.py`. A new provider does not need to alter the arranger or renderer.

Each bundle contains immutable `original/` artifacts, hashes, provenance and `manifest.json`. Native MSCZ/MSCX is retained as the archival original and converted to compressed MusicXML through the pinned WebMscore engine used by LibreScore. MXL is unpacked to `normalized/score.musicxml`. Explicitly encoded tuplets with rounding errors of at most one source tick receive the existing conservative duration repair, recorded by measure; pitches are unchanged. The source is inspected at sounding pitch, including piano grand staves and transposing instruments. The return value includes the arrangement-ready path only when validation succeeds. Existing nonempty bundle directories are never replaced.

The pinned [dl-librescore 0.35.40 CLI](https://github.com/LibreScore/dl-librescore/blob/5a9491f977f900943cb6f1ed3d703eb701bb03a6/src/cli.ts) can download **PDF, MIDI and MP3 from MuseScore URLs**, but explicitly rejects MSCZ/MSCX/MusicXML URL requests. Its MusicXML support is for local-file conversion. Our MuseScore adapter therefore requests PDF, preserves it and returns `needs_transcription`; it never silently substitutes MIDI. Direct notation URLs and local MSCZ/MSCX/MusicXML/MXL files can return `ready`. No downloader availability is treated as a license for a composition.

```bash
# Substitute an actual full-score URL:
uv run transscribe ingest 'https://musescore.com/user/USER_ID/scores/SCORE_ID' \
  --provider musescore --output sources/downloaded-score
```

Native conversion runs offline in an isolated Node process. A small compatibility shim handles Node's newer browser globals without patching the upstream package. Download failures, timeouts, zero-byte files and upstream errors that exit with status zero fail explicitly. MIT/GPL notices and upstream version information are retained under `tools/librescore/`.

### Reusable PDF → MusicXML transcription

```bash
# Optional pinned Audiveris + bundled Java runtime and English text OCR model.
# Linux x86_64, installed in the user cache without sudo.
uv run python scripts/bootstrap_audiveris.py
uv run transscribe ingest printed-score.pdf --transcribe-pdf --output sources/recognized

# When independently reviewed symbolic notation is available:
uv run transscribe compare-transcription reviewed.musicxml \
  sources/recognized/normalized/candidate-1.musicxml --output outputs/ocr-review.json
```

The same opt-in works with direct PDF URLs and MuseScore PDF downloads. `acquisition/omr.py` runs Audiveris 5.11.0 in batch, exports MusicXML, and retains the source PDF, editable `.omr` project, raw export, diagnostics and checksums. Projects are relinked to the published source location after staging; untouched `.raw.omr` copies preserve the engine result. Multiple movements remain separate candidates.

OMR candidates always return `needs_review`, even when they parse and have aligned measures. Recognition failures return `transcription_failed` with the original retained. Compare and correct the candidate against the PDF before ingesting reviewed MusicXML as a new arrangement-ready source. The comparison command checks pitches, onsets, durations, ties, rests, measure structure and repeat barlines; it does not validate every articulation, text instruction or spanner.

Real checks include a rasterized violin/cello duet (all 64 note events and checked structure matched) and Hedwig's vector flute duet (169 of 171 note events matched; bar 5 contains an incorrect voice/rhythm split). The latter remains flagged for review and has not replaced the independently reviewed transcription used for arranging. These two examples are regression checks, not a general OMR accuracy estimate. Engine/model versions, download checksums and AGPL/Apache license notices are in `tools/audiveris/`.

The real native conversion check used the full 98-measure violin/viola Passacaglia, converted to MSCZ and ingested back. After 21 audited tuplet tick repairs, its complete sounding note/rest/tie fingerprint matched the reference exactly. A live MuseScore URL returned “Score not found”; that unsuccessful acquisition is retained in [the check record](outputs/ingestion-checks/musescore-live-check.json). This is not evidence that all MuseScore URLs will work.

## Theme trio drafts

**Use the [register-revised editions](outputs/theme-register-review/README.md).** The first editions below remain comparison controls. Their advanced physical range checks were too permissive about sustained upper-register string writing.

The new source-independent pipeline produced both requested themes for E-flat alto saxophone, cello and double bass:

| Theme | Actual source edition | Selected draft | Preview |
| --- | --- | --- | --- |
| Hedwig's Theme — John Williams | Chew Sze Gee's two-flute PDF, 51 written measures including pickup | [Score and parts](outputs/themes/hedwigs-theme/relay-two/) | [MP3](outputs/themes/hedwigs-theme/relay-two/score.mp3) |
| Merry-Go-Round of Life — Joe Hisaishi | Joiani's clarinet sextet from PDMX, 138 written measures | [Score and parts](outputs/themes/merry-go-round-of-life/relay-two/) | [MP3](outputs/themes/merry-go-round-of-life/relay-two/score.mp3) |

Download the [complete bundle](outputs/theme-trios.zip), including three policies per theme, source editions, provenance, note-level audits, separate transposed parts, MIDI and synthesized MP3 previews. The [review](outputs/themes/README.md) explains the source choices and remaining musical concerns. These are rehearsal drafts, not player-approved editions.

Hedwig retains all 171 source pitch events; its two voices alternate among three players rather than gaining invented harmony. Howl retains 799 of 1,441 events (55.45%); reducing the sextet removes inner voices and doublings, with exact omissions recorded. Two-bar exchanges reduce maximum uninterrupted saxophone runs from 75.5 to 3.0 seconds for Hedwig and from 26.43 to 8.92 seconds for Howl. This chooses breathing space under the declared screening policy, not a preferred interpretation.

Hedwig's source required a reviewed, source-specific vector-PDF transcription in `scripts/ingest_hedwig_pdf.py`. It is not a generic OMR feature. Its printed E-flat/F-sharp spellings and ties were preserved, including unusual passages at bars 6 and 38; the audition uses an editorial quarter=120 because the source prints no tempo. Howl was ingested directly from MXL. Both immutable originals and source credits remain under `sources/themes/`.

```bash
uv run python scripts/compare_trio.py \
  --input sources/themes/hedwigs-theme/source.musicxml \
  --output outputs/themes/hedwigs-theme
uv run python scripts/compare_trio.py \
  --input sources/themes/merry-go-round-of-life/normalized/score.musicxml \
  --title 'Merry-Go-Round of Life' --composer 'Joe Hisaishi' \
  --source-credit 'Source transcription: Joiani (PDMX)' \
  --output outputs/themes/merry-go-round-of-life
```

## Agent/MCP setup

Launch the client from the checkout root. The project-scoped stdio definitions are in `.mcp.json` (also available as `mcp-config.json`). For a client launched elsewhere, insert `--project /absolute/path/to/checkout` after `uv run`.

The project server exposes:

- `build_rehearsal_packet`: export control and candidate with evaluations and a ZIP.
- `search_pdmx_scores`: search the pinned edition catalog, then pass a selected source ID to ingestion.
- `score_source_providers`: inspect source adapters and format capabilities.
- `ingest_score_source`: acquire and validate an immutable source bundle; PDF-only results require transcription.
- `compare_score_transcription`: compare OMR notation with an independently reviewed symbolic reference.
- `inspect_score_file`: identify parts, length and sounding ranges.
- `arrangement_policies`: inspect the available transformations.
- `arrange_duo`: generate score, parts, audition and an audit trail.
- `arrange_alto_trio`: reduce an arbitrary pitched ensemble to the fixed trio.
- `evaluate_alto_trio`: independently check exported trio integrity and playability diagnostics.
- `critique_duo`: return measure-specific range, multiple-stop and rapid-leap issues.
- `evaluate_arrangement`: independent frozen score judge, source-relative integrity checks and measure-level findings.
- `literature_rules`: inspect the cited rule catalog or audit a score against it.
- `transpose_score`: test simple transposition through the cloned upstream tool.

The second configured server exposes the upstream MIDI tools. Its transposition function is also used directly by our pipeline, preserving the notation stream instead of converting through MIDI.

## Independently judged iterations

The latest selected draft is [iteration 1](outputs/judged-passacaglia/iteration-1/score.pdf), with [cello](outputs/judged-passacaglia/iteration-1/cello.pdf), [double bass](outputs/judged-passacaglia/iteration-1/double-bass.pdf), and an [independent review](outputs/judged-passacaglia/independent-review.md). See the [comparison](outputs/judged-passacaglia/README.md) and [complete experiment bundle](outputs/judged-passacaglia.zip).

The evaluator in `judge.py` was implemented by a separate reviewing agent and never imports the optimizer's scoring or critique functions. It examines exported MusicXML at sounding pitch, using a fixed, hashed rubric for duration-weighted low/close spacing, wide spacing, introduced crossings, contour changes, range, fast motion and multiple stops. It records source/candidate/evaluator hashes. Its vertical analysis uses outer voices, so interior chord conflicts can be missed; it is neither an acoustic model nor a musician assessment.

Three revisions were tested against the existing chamber edition. The first paired search reduced the judge's low-spacing exposure by about 41%, wide spacing by 22%, new crossings by 82%, and contour changes by 28%. Its fast-motion exposure increased 6.2%. Two subsequent revisions strengthened the movement penalty; the strongest reduced fast motion below the original chamber edition but sacrificed spacing and crossings. The fixed judge retained iteration 1 overall. Scores are diagnostic summaries, not musical-quality grades; the small differences do not establish an artistic preference.

The experiment rejects revisions that alter the chamber control's event timing, rests, tie types or pitches modulo octaves, preventing improvement by note deletion. Independent source-level meter/repeat/timing gates must also pass, and range/multiple-stop exposure cannot increase at selection. All attempts and their per-measure audits are retained. This is a development comparison on one piece, not a held-out benchmark.

```bash
# Reproduce all proposals, independent evaluations, selection, PDFs and bundle:
uv run python -m music_arranger.experiment --output outputs/judged-passacaglia-replay

# Judge any exported candidate independently:
uv run python -m music_arranger.judge sources/handel-halvorsen.musicxml \
  outputs/judged-passacaglia/iteration-1/score.musicxml --output outputs/review.json

# Generate the selected policy directly:
uv run transscribe arrange sources/handel-halvorsen.musicxml \
  --variant joint-chamber --output outputs/my-joint-arrangement
```

The replay uses explicit optimizer recipes (rapid-motion weights 0, 3 and 12) selected during this review, rather than an undisclosed LLM or an automatic claim of musical improvement. The evaluator's weights remain fixed throughout. See `selection.json` for remaining regressions and `independent-review.md` for passages needing player review, especially cello bars 13–14 and the crossing at bar 82.

An agent can call `inspect_score_file`, generate `reference`, inspect `report.json`, generate `chamber` and `study-d-minor`, and compare critiques and the rendered pages. Each run contains deterministic propose/critique/revise stages. **There is no hidden LLM or API key requirement.** The MCP client supplies the conversational agent; the built-in arranger is a reproducible rule-based search. The source code exposes the profiles and transformations for further arranging experiments.

## Literature-backed deterministic rules

`rules.py` supplies models and diagnostics; `core.py` applies them in the `literature-chamber` optimizer. The independent `judge.py` and the earlier experiments remain separate. The [machine-readable rulebook](outputs/literature-passacaglia/rulebook.json) records citations, source locations, rule types, parameters and a stable catalog hash; audits also hash the implementation.

| Rule | Evidence | Deterministic implementation and limits |
| --- | --- | --- |
| Spectral roughness | [Sethares, Relating Tuning and Timbre](https://sethares.engr.wisc.edu/consemi.html), with [published fit coefficients](https://sethares.engr.wisc.edu/comprog.html) | Sum the published pair-of-partials curve across every active pitch, including chord interiors and held notes. This distinguishes thirds from tenths and changes with register. Six harmonics, 1/k amplitudes, equal instrument levels and the objective weight are **our assumptions**. No measured cello/bass spectra or masking simulation is claimed. |
| Stream proximity and silence | [Huron (2001), Tone and Voice](https://doi.org/10.1525/mp.2001.19.1.1), pp. 12, 18, 24; [paper](https://www.musanim.com/articles/Huron.ToneAndVoice.2001.pdf) | Use the approximate 0.8-second silence and 2-semitone/0.7-second contexts as soft streaming diagnostics. Integrate explicit tempo changes, including dotted metronome units. Our linear excess-distance penalty is an adaptation, not a published fitted law or a player-speed limit. Rubato/fermatas are not timed. |
| String compatibility | VSL Academy: [cello](https://www.vsl.co.at/academy/strings/cello), [double bass](https://www.vsl.co.at/academy/strings/double-bass) | Standard open-string floors are hard limits. Ordinary two-note bowed chord events need a distinct adjacent-string assignment; explicit pizzicato removes adjacency. Passing is necessary, not sufficient: reach, positions and bow mechanics are unmodelled. Harmonic/ambiguous circle markings are excluded; larger chords receive execution-review flags. |

This register-search stage preserves the chamber reduction's pitch classes, rhythms and ties; the earlier reduction's removed chord tones remain in the audit. Upper tessitura preferences, spectral envelope, weighting, crossing and contour penalties remain explicit implementation choices; the cited authors do not prescribe those optimization weights. A soft perceptual preference never automatically forbids an expressive dissonance. The remaining pitch/register decisions are deterministic dynamic programming, not agent votes.

```bash
uv run transscribe rules                         # catalog, citations and parameters
uv run transscribe rules outputs/literature-passacaglia/candidate/score.musicxml
uv run python scripts/compare_literature.py    # generate, export, independently judge
```

The full-score comparison retains the same-material guard and the frozen independent evaluator. See [comparison.json](outputs/literature-passacaglia/comparison.json) for actual outcomes; the new model is not assumed to win because it has citations. Models and tuning assumptions require further validation on other pieces and with musicians.

## Alto saxophone, cello and double bass

The trio extension writes E-flat alto saxophone, cello and orchestral double bass from any aligned pitched ensemble. Explicit voices and chord tones are partitioned into distinct monophonic lines, with tied notes kept in the same line even when their position within a chord changes. At nominal four-bar source phrase boundaries it selects active high, inner and low lines, deferring switches across ties and retained spans. Register/activity and continuity preferences are heuristics, not recognition of the composer's intended melody.

All target parts are monophonic. A sparse source can leave one or more players resting; accompaniment is not invented. Each retained note has a source-part/voice/measure mapping and its final sounding-pitch transposition in `source_assignments`. Every omitted pitched or unpitched event is audited; percussion is not assigned fictitious pitches. Tempo instructions, meters, repeats and repeat endings are gathered across the ensemble, including unselected staves. Misaligned measures, overlapping events without separate voices and conflicting simultaneous tempi/meters require source repair.

```bash
uv run transscribe trio sources/handel-halvorsen.musicxml --variant relay-two --output outputs/my-trio
uv run python scripts/compare_trio.py
```

The comparison generates `sax-led` (control), `relay-four` and `relay-two`, retaining the same selected source material across candidates. The relay versions exchange melody and inner material between alto and cello at nominal four- or two-bar boundaries, deferred across retained ties and spans. A separate deterministic octave search repairs lines too wide for a whole-bar transposition. Ordinary alto is limited here to sounding D-flat3–A-flat5 (written B-flat3–F6), with no altissimo assumed. The scores retain transposition metadata: alto written a major sixth higher, double bass written an octave higher; cello stays at concert pitch. See [Yamaha's transposition guide](https://www.yamaha.com/en/musical_instrument_guide/saxophone/play/play003.html).

`trio_judge.py` independently checks source-relative pitch classes/onsets/durations, range, monophony, ties, spanner endpoints and repeat structure. It expands repeats for wind-phrase diagnostics. A 0.25-second rest counts as a potential breath and runs exceeding 12 seconds receive a review flag: these are **our screening assumptions**, not limits claimed by [Yamaha's breathing guidance](https://www.yamaha.com/en/musical_instrument_guide/saxophone/play/play004.html). Articulation-created gaps, circular breathing and individual breath capacity are not modelled. The trio has no aggregate quality score comparable to the duo's old grade. Sax/string balance and actual fingerings still require player review; shared harmonic spectra from the earlier duo model are not a calibrated model of these different timbres.

The MCP adds `arrange_alto_trio` and `evaluate_alto_trio`. Both now accept arbitrary source ensembles. `inspect_score_file` also accepts exported trio scores. The earlier duo evaluator and its rubric remain unchanged.

The trio evaluator separates integrity from texture reduction. It rejects changed note onsets/durations/pitch classes, broken ties/spans/endings, out-of-range notes and overlapping target parts. Retention below 70% is a review flag rather than an integrity failure: reducing six simultaneous parts to three necessarily omits material. The retained fraction and exact omissions remain visible; passing the gates is not a claim of musical quality.

Version `v4-tessitura` adds duration-weighted register exposure and measure-level warnings. Physical limits remain distinct from preferred chamber registers: alto G3–D5 sounding, cello C3–A4, bass E1–D3. More than 10% of played duration outside a preferred band triggers a summary warning; each excursion is still listed. These are editorial defaults, not universal skill limits. The shared single-part register optimizer searches all applicable octaves, weights sustained notes by duration and targets destination register instead of penalizing distance from the source octave. Ties and within-bar intervals remain constrained. Regression tests cover octave-shifted sources, sparse sustained notes, the flute duet and a mixed sextet.

The following Passacaglia figures and linked artifacts document the earlier duet-specific policy. They remain available as historical controls; the new ensemble policy does not retroactively change those editions.

For the full 98-measure Passacaglia, all three versions retain the same 2,308 of 2,537 source pitch events (90.97%), allowing octave changes and player reassignment. The independent evaluator reports:

| Version | Longest written wind run, repeats included | Flagged fast leaps, all players |
| --- | ---: | ---: |
| Sax-led control | 77.29 s | 106 |
| Four-bar exchanges | 37.26 s | 72 |
| Two-bar exchanges | 16.10 s | 75 |

The two-bar version is the recommended starting draft under the declared breathing-first screening policy. It still has one wind run above the 12-second review threshold; four-bar exchanges have slightly fewer leap flags. These figures do not establish a listening preference. See the [comparison](outputs/alto-trio/comparison.json), [score](outputs/alto-trio/relay-two/score.pdf), and [bundle](outputs/alto-trio.zip).

Each trio also has a synthesized `score.mp3` alongside its original `score.mid`. Start with the [two-bar audio preview](outputs/alto-trio/relay-two/score.mp3), or download the [audio comparison bundle](outputs/alto-trio-audio.zip). These use TiMidity++ and FluidR3 GM sampled instruments; live phrasing, bowing/tonguing and acoustic balance are not reproduced. The MIDI retains editable instrument tracks and tempo changes.

To render another MIDI using the synth packages extracted into `~/.cache/music-arranger/audio`:

```bash
uv run --with imageio-ffmpeg==0.6.0 python scripts/render_audio.py outputs/alto-trio/relay-two/score.mid
```

The renderer leaves MIDI unchanged, checks the MP3 decodes, and records source/output hashes and synthesis settings in `score.audio.json`. FluidR3's license is included with the previews. The optional FFmpeg Python wrapper is pinned in this command; the score-processing environment's `uv.lock` is unchanged.

MIDI export realizes grace notes on a separate performance copy. Each grace gets up to 1/8 quarter, with the group borrowing at most 1/4 quarter and half the following anchor's duration. The anchor ends on its original beat; notation is unchanged. This is an explicit audition policy, not a historical ornament interpretation. Unanchored ornaments or ornaments interrupting incoming ties raise an error instead of silently generating unsafe timing. Per-measure realization details are recorded in `report.json` under `midi_playback`.

The original previews had a playback bug: zero-duration grace notes produced MIDI note-offs before their note-ons, leaving pitches sounding indefinitely (particularly audible around 0:33 in the trio). Regenerated previews fix this. Export now independently checks the serialized MIDI for unmatched note-offs, unclosed notes, nonpositive note durations and an unreleased sustain pedal. Full trio tests also enforce monophonic MIDI tracks. `playback-repair.json` records the before/after audit and unchanged notation hashes for existing outputs.

### Tempo and expressive playback review

The [tempo review](outputs/tempo-review/comparison.json) keeps the preceding trio as a control. At 1:22.91, bar 29 begins at quarter=110 with grace notes and fast tuplets; there is no new tempo instruction there. “A tempo rubato” starts in bar 33 (1:31.64 in the encoded preview). The renderer does not infer an expressive rubato curve from that text.

The importer now preserves MusicXML `<sound tempo>` independently of the printed metronome number, including dotted or half-note units. This fixes six lost source playback values in bars 76, 88 and 95; music21 had preferred the printed quarter=80 in those directions. Source files remain immutable. See the [MusicXML sound specification](https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/sound/). MusicXML export now checks tempo round trips as well as note/rhythm/tie round trips.

Run `uv run python scripts/review_tempo.py` to transfer those source values to the existing trio without changing its pitches, rhythms or ties. It creates an `encoded` control and a `smooth-rit` interpretation. The latter interpolates seconds per quarter between explicitly encoded slowing points within a bar containing a ritardando marking, sampled every 1/32 quarter. This interpolation is our deterministic performance policy, not an inferred historical performance. Explicit tempo anchors are retained. Rubato, ad libitum and fermata durations without numeric instructions remain unresolved and are reported; fast tuplets are not automatically slowed.

The review includes updated [MusicXML](outputs/tempo-review/score.musicxml), [encoded-tempo audio](outputs/tempo-review/encoded/score.mp3), and [smooth-rit audio](outputs/tempo-review/smooth-rit/score.mp3). Render either MIDI with `scripts/render_audio.py` as above. Old previews remain available as controls; they have the grace-note repair but predate this tempo repair.

That historical tempo review used the independent trio evaluator `v2-tempo-import`, which respects sound tempos when measuring wind-run seconds. The current ensemble evaluator is v4; earlier reports retain their original hashes and describe their original artifacts.

## Musical and engineering conventions

- All arranging and critique uses **sounding pitches**. Export converts the bass to written pitch exactly once, with MusicXML octave transposition `-1`. Extracted bass parts retain that declaration and receive shared tempo cues from the upper staff. MIDI uses sounding pitches.
- The register optimizer chooses a whole-bar octave displacement through dynamic programming. It enforces the physical lowest string, penalizes register extremes, large boundary leaps and unnecessary register changes, and constrains shifts across tied bar boundaries. A5 for cello and G4 sounding for bass are advanced physical search ceilings, not preferred tessituras. Trio preferred bands and duration exposure are listed above. A separately audited tied-note fallback handles lines too wide to fit by moving an entire bar.
- Chord reduction keeps the lower chord tone in the bass and, in the study version, the upper chord tone in the cello. Where the source uses independent simultaneous voices, the most complete musical strand is retained; ties in coverage favor the lower bass or upper cello voice. Removed voices, chord tones and octave shifts are recorded by measure.
- Retained musical strands keep their rhythms, rests, measures, repeats and expression. Missing secondary-voice rest padding is made explicit and hidden for stable export. Source fingerings and string indications are removed because they are instrument-specific. Clefs are selected for the destination register. Intermediate dynamic marks in bars containing three or more dynamics are omitted from printed MusicXML/PDF to avoid collisions; MIDI retains their playback values. Slurs or hairpins with endpoints removed by voice reduction are dropped and logged.
- Every exported full score is re-imported and checked against the expected sounding notes, rests, offsets, durations and ties. Failure raises an error rather than reporting success.
- Printed repeats remain in the notation; the MIDI audition expands repeats. MIDI playback is a synthetic aid, not a performance model for fermatas, bowing or tone.
- Music21's chromatic transposition can choose an undesirable enharmonic key spelling. The wrapper uses a named interval to correct the key signatures; the regression test covers G minor → F minor.
- PDF rendering uses Verovio → SVG → CairoSVG → pypdf. No graphical application, system-wide installation or proprietary notation software is required.
- Destination clefs minimize ledger lines and unnecessary changes across adjacent bars; rests retain the surrounding clef. In bars with three or more numeric tempo marks, PDFs show the first and last. All tempo anchors remain in MusicXML/MIDI, and suppressed printed marks are listed in the rendering report.

## Tests and provenance

```bash
uv run pytest tests -q
uv run pytest vendor/music21-mcp/tests/test_transpose_midi.py -q
uv run python scripts/smoke_mcp.py
uv run ruff check src scripts tests
```

The initial duet run recorded 11 passing project tests and 6 passing upstream transposition tests in `outputs/`. Later verification records live alongside their artifacts: [literature rules](outputs/literature-passacaglia/verification.json) and [alto trio](outputs/alto-trio/verification.json). The expanded MCP exposes twelve local tools. Full scores are checked for sounding-pitch, duration and tie round trips; separate parts are checked against the score, including saxophone and double-bass transpositions.

See `sources/pdmx-record.json` for the selected record and `sources/provenance.json` for the source checksum and origin. The PDMX record marks this transcription `cc-zero`, with `license_conflict=False`. This is dataset-reported provenance, not a new critical edition or a note-by-note verification against the historical print.

The upstream Git checkout is pinned to `621e7ab34223b3c8f6bffa5e50e52b9e18843d90` and retains its MIT license. It is a clone under `vendor/`, not a submodule: the initial workspace had a read-only placeholder `.git` directory rather than an initialized Git repository. `scripts/bootstrap_vendor.py` reproduces it without modifying an existing checkout. Other dependency licenses remain with their packages.
