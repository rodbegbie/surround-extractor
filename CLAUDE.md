# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working
with code in this repository.

## What this is

A personal CLI tool (`extract_ac3.py`) that rips the 5.1 surround mix off
DVD-Video album releases (e.g. DVD-Audio-era discs) and splits it into one
tagged file per track. It extracts AC3 always, and DTS too when the disc
has a 5.1 DTS track. It is disc-agnostic — no per-album logic — driven
entirely by auto-detection plus an optional MusicBrainz release ID.

The whole tool is one file, `extract_ac3.py`, plus `tests/test_extract_ac3.py`.
There is no package layout beyond that.

## Commands

```bash
# Install/sync dependencies (stdlib + mutagen only; pytest/ruff are dev deps)
uv sync

# Run the extractor (always dry-runs first: prints the plan, writes nothing)
uv run extract_ac3.py "<path to .dvdmedia or VIDEO_TS>" \
  --musicbrainz-release <MBID>

# Add --yes to actually write files
uv run extract_ac3.py "<path>" --musicbrainz-release <MBID> --yes

# Force a specific DVD title instead of auto-detection
uv run extract_ac3.py "<path>" --title <N>

# Run tests
uv run pytest -q

# Run a single test
uv run pytest -q tests/test_extract_ac3.py::test_pick_album_title_prefers_longest_multi_chapter_surround_title

# Lint / format
uv run ruff check .
uv run ruff format .
```

Tests run against a captured real `HandBrakeCLI --scan` text fixture and
never touch a disc or shell out — no external tools are required to run
the test suite. Actually running the extractor requires `HandBrakeCLI`,
`ffmpeg`, and `ffprobe` on `PATH` (`brew install handbrake ffmpeg`);
`extract_album()` checks for these up front and raises a clear
`ExtractionError` if any are missing.

## Architecture

The pipeline, in call order from `extract_album()`:

1. **`scan_disc()`** shells out to `HandBrakeCLI --scan -t 0` and
   `parse_scan()` parses its human-readable `+ title N:` block format into
   `Title` objects (each with `Chapter`s and `AudioTrack`s). This scan
   output format is the only "API" the script depends on for disc
   structure — there is no use of libdvdread/ffmpeg for navigation.
2. **`pick_album_title()`** auto-selects the title to extract: the longest
   multi-chapter title that has a 6-channel AC3 track. This heuristic has
   held across every disc tried (SFA and Björk releases) despite each disc
   having a different title number/audio-track-index layout — there is
   intentionally no per-disc special-casing. `--title N` overrides it.
3. **`Title.find_surround_track(codec)`** looks up the 6-channel AC3 or DTS
   track on the chosen title. `CodecSpec` (the `AC3`/`DTS` constants) holds
   the three codec-specific strings needed downstream: the HandBrakeCLI
   `-E` passthrough encoder name, and the ffmpeg raw-stream muxer/file
   extension (the same string serves both).
4. **`fetch_musicbrainz_release()`** (only if `--musicbrainz-release` is
   given) pulls title/artist/date/track list from the MusicBrainz API.
   Note `inc=recordings+artist-credits` is required in the query —
   `artist-credit` is not included by default even for the release's own
   top-level artist field, and silently comes back empty rather than
   erroring if omitted. If the track count doesn't match the DVD's chapter
   count, MusicBrainz data is discarded entirely (generic `trackNN`
   filenames, no ID3 tags) rather than partially applied.
5. **`extract_all_chapters()` → `extract_chapter()`** does a two-step
   extraction per chapter, per codec: HandBrakeCLI cuts a chapter-accurate
   `.mkv` (video must be encoded — HandBrake has no video-passthrough mode,
   so it uses a throwaway `x264 --encoder-preset ultrafast -q 51`), then
   ffmpeg strips the video and copies out just the raw AC3/DTS elementary
   stream (`-vn -c:a copy -f <codec>`).
6. **`write_id3_tags()`** prepends an ID3v2 tag via `mutagen` as a separate
   pass after extraction, since ffmpeg's raw `ac3`/`dts` muxers silently
   drop `-metadata` with no error or warning.

AC3 output goes to `--output-dir` (default `./output/<dvd folder stem>/`).
If the title has a DTS track, DTS output goes to a sibling directory named
`<output dir name> DTS`. `output/` is gitignored — it holds generated
audio, not source.

### Why HandBrakeCLI, not plain ffmpeg

Naive `ffmpeg -i "concat:part1.VOB|part2.VOB|..."` on a multi-part VOB
title can **silently produce a duplicated, ~2x-length audio stream** on
discs whose PGC cells reset timestamps mid-title — ffmpeg's demuxer has no
real DVD chapter/cell awareness and its discontinuity-correction heuristic
can over-correct. This was confirmed empirically (a title that should
decode to ~3156s came out at ~6519s with genuinely duplicated frame data,
not just bad metadata). HandBrakeCLI bundles its own libdvdread/libdvdnav
and navigates PGC/chapter/cell structure correctly regardless of how
convoluted the disc's title table is — this is why it's used for
extraction rather than ffmpeg alone. ffmpeg is only used afterward, on
HandBrake's already-correct output, to strip video.

### Known metadata quirks

`ffprobe`'s quick `format=duration` on a bare `.dts` (and to a lesser
extent `.ac3`) elementary stream is a **bitrate-based estimate**, not
derived from real frame count — it can read meaningfully short (~2% low
observed on `.dts`) even when the audio is complete. Verify actual content
with a full decode (`ffmpeg -i x -f null -`) rather than trusting a quick
`ffprobe` duration if correctness matters.
