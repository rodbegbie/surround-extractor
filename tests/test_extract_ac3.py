from mutagen.id3 import ID3

from extract_ac3 import (
    _format_artist_credit,
    parse_hms,
    parse_scan,
    pick_album_title,
    sanitize_filename,
    track_filenames,
    write_id3_tags,
)

# Trimmed real HandBrakeCLI `--scan -t 0` output for the Phantom Power DVD:
# title 1 is a short menu/logo clip, title 2 is the full album, title 3 is a
# single-song title with no 5.1 track variety (only used to prove title 2
# still wins on duration).
SCAN_OUTPUT = """
+ title 1:
  + index 1
  + duration: 00:00:25
  + chapters:
    + 1: duration 00:00:25
  + audio tracks:
    + 1, English (AC3, 2.0 ch, 192 kbps) (iso639-2: eng), 48000Hz, 192000bps
  + subtitle tracks:
+ title 2:
  + index 2
  + duration: 00:52:36
  + size: 720x576, pixel aspect: 64/45, display aspect: 1.78, 25.000 fps
  + autocrop: 0/0/12/174
  + chapters:
    + 1: duration 00:03:35
    + 2: duration 00:02:58
  + audio tracks:
    + 1, English (AC3, 2.0 ch, 256 kbps) (iso639-2: eng), 48000Hz, 256000bps
    + 2, English (AC3, 5.1 ch, 448 kbps) (iso639-2: eng), 48000Hz, 448000bps
    + 3, English (DTS, 5.1 ch, 768 kbps) (iso639-2: eng), 48000Hz, 768000bps
    + 4, English (AC3, 2.0 ch, 192 kbps) (iso639-2: eng), 48000Hz, 192000bps
  + subtitle tracks:
+ title 3:
  + index 3
  + duration: 00:03:34
  + chapters:
    + 1: duration 00:03:34
  + audio tracks:
    + 1, English (AC3, 2.0 ch, 256 kbps) (iso639-2: eng), 48000Hz, 256000bps
  + subtitle tracks:
"""


def test_parse_hms():
    assert parse_hms("00:52:36") == 3156
    assert parse_hms("00:00:25") == 25


def test_parse_scan_extracts_titles_chapters_and_audio():
    titles = parse_scan(SCAN_OUTPUT)
    assert [t.number for t in titles] == [1, 2, 3]

    album = titles[1]
    assert album.length_seconds == 3156
    assert [c.length_seconds for c in album.chapters] == [215, 178]
    assert len(album.audio_tracks) == 4
    assert album.audio_tracks[1].codec == "AC3"
    assert album.audio_tracks[1].channels == 6
    assert album.audio_tracks[2].codec == "DTS"


def test_surround_ac3_track_picks_six_channel_ac3_not_dts():
    titles = parse_scan(SCAN_OUTPUT)
    album = titles[1]
    track = album.surround_ac3_track()
    assert track is not None
    assert track.index == 2
    assert track.codec == "AC3"
    assert track.channels == 6


def test_surround_ac3_track_none_when_absent():
    titles = parse_scan(SCAN_OUTPUT)
    single_song = titles[2]
    assert single_song.surround_ac3_track() is None


def test_find_surround_track_picks_dts_when_present():
    titles = parse_scan(SCAN_OUTPUT)
    album = titles[1]
    track = album.find_surround_track("DTS")
    assert track is not None
    assert track.index == 3
    assert track.codec == "DTS"
    assert track.channels == 6


def test_find_surround_track_none_when_dts_absent():
    titles = parse_scan(SCAN_OUTPUT)
    single_song = titles[2]
    assert single_song.find_surround_track("DTS") is None


def test_pick_album_title_prefers_longest_multi_chapter_surround_title():
    titles = parse_scan(SCAN_OUTPUT)
    chosen = pick_album_title(titles)
    assert chosen.number == 2


def test_track_filenames_uses_names_when_count_matches():
    names = track_filenames(2, ["Hello Sunshine", "Liberty Belle"], "ac3")
    assert names == ["01 - Hello Sunshine.ac3", "02 - Liberty Belle.ac3"]


def test_track_filenames_falls_back_when_count_mismatches():
    names = track_filenames(3, ["Only One Track"], "ac3")
    assert names == ["track01.ac3", "track02.ac3", "track03.ac3"]


def test_track_filenames_generic_when_no_names_given():
    names = track_filenames(2, None, "ac3")
    assert names == ["track01.ac3", "track02.ac3"]


def test_track_filenames_uses_given_extension():
    names = track_filenames(2, ["Hello Sunshine", "Liberty Belle"], "dts")
    assert names == ["01 - Hello Sunshine.dts", "02 - Liberty Belle.dts"]


def test_sanitize_filename_strips_path_hostile_characters():
    assert sanitize_filename("Sex, War & Robots") == "Sex, War & Robots"
    assert sanitize_filename("Father / Father?") == "Father - Father-"


def test_format_artist_credit_joins_using_musicbrainz_joinphrase():
    credit = [{"name": "Super Furry Animals", "joinphrase": ""}]
    assert _format_artist_credit(credit) == "Super Furry Animals"

    featuring = [
        {"name": "Artist A", "joinphrase": " feat. "},
        {"name": "Artist B", "joinphrase": ""},
    ]
    assert _format_artist_credit(featuring) == "Artist A feat. Artist B"


def test_write_id3_tags_round_trips_on_a_raw_file(tmp_path):
    # ID3v2 is just a header block prepended to the file, so it doesn't
    # matter that this isn't real AC3 audio -- tagging is format-agnostic.
    target = tmp_path / "track.ac3"
    target.write_bytes(b"\x0b\x77" + b"\x00" * 32)

    write_id3_tags(
        target,
        title="Hello Sunshine",
        artist="Super Furry Animals",
        album="Phantom Power",
        track_number=1,
        track_total=14,
        date="2003-07-21",
    )

    tags = ID3(target)
    assert tags["TIT2"].text == ["Hello Sunshine"]
    assert tags["TPE1"].text == ["Super Furry Animals"]
    assert tags["TALB"].text == ["Phantom Power"]
    assert tags["TRCK"].text == ["1/14"]
    assert tags["TDRC"].text[0].text == "2003-07-21"


def test_write_id3_tags_omits_date_when_none(tmp_path):
    target = tmp_path / "track.ac3"
    target.write_bytes(b"\x0b\x77" + b"\x00" * 32)

    write_id3_tags(
        target, title="T", artist="A", album="B", track_number=1, track_total=1, date=None
    )

    tags = ID3(target)
    assert "TDRC" not in tags
