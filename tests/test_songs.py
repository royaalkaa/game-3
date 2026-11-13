import json
from pathlib import Path

import pytest

from photo_hunt.songs import (
    BadMusicError,
    SongIn,
    SongLibrary,
    SongNotFoundError,
    detect_audio_ext,
)

MP3 = b"ID3" + b"0" * 20


def lib(tmp_path: Path) -> SongLibrary:
    return SongLibrary.load(tmp_path / "songs.json", tmp_path / "music")


def test_detect_audio_ext():
    assert detect_audio_ext(MP3) == ".mp3"
    assert detect_audio_ext(bytes.fromhex("fffb9064")) == ".mp3"
    assert detect_audio_ext(b"\0\0\0\x20ftypM4A ") == ".m4a"
    assert detect_audio_ext(b"OggS\0") == ".ogg"
    assert detect_audio_ext(b"fLaC\0") == ".flac"
    assert detect_audio_ext(b"RIFF\0\0\0\0WAVE") == ".wav"
    assert detect_audio_ext(b"<html>") is None
    assert detect_audio_ext(b"") is None


def test_missing_file_gives_empty_library(tmp_path: Path):
    assert lib(tmp_path).songs == []


def test_old_file_without_ids_gets_ids(tmp_path: Path):
    (tmp_path / "songs.json").write_text(
        json.dumps([{"title": "A", "item": "кружка"}, {"title": "B", "item": "очки"}]),
        encoding="utf-8",
    )
    songs = lib(tmp_path).songs
    assert len({s.id for s in songs}) == 2


@pytest.mark.parametrize(
    "content",
    [
        '{"not": "list"}',
        "[{}]",
        '[{"id": "a", "title": "A", "item": "x"}, {"id": "a", "title": "B", "item": "y"}]',
    ],
)
def test_invalid_file_fails_fast(tmp_path: Path, content: str):
    (tmp_path / "songs.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        lib(tmp_path)


def test_add_update_persist(tmp_path: Path):
    library = lib(tmp_path)
    song = library.add(SongIn(title="Песня", item="кружка", stop_at=12.5))
    library.update(song.id, SongIn(title="Песня 2", item="очки", lyric="строка"))
    reloaded = lib(tmp_path).get(song.id)
    assert (reloaded.title, reloaded.item, reloaded.lyric, reloaded.stop_at) == (
        "Песня 2",
        "очки",
        "строка",
        None,
    )
    assert not (tmp_path / "songs.tmp").exists()


def test_music_upload_replace_and_delete(tmp_path: Path):
    library = lib(tmp_path)
    song = library.add(SongIn(title="A", item="x"))
    first = library.set_music(song.id, MP3)
    first_path = library.music_path(song.id)
    assert first.file and first.file.endswith(".mp3") and first_path.read_bytes() == MP3
    library.set_music(song.id, b"OggS" + b"1" * 10)
    assert not first_path.exists()
    assert library.music_path(song.id).suffix == ".ogg"
    second_path = library.music_path(song.id)
    library.delete(song.id)
    assert not second_path.exists()
    assert lib(tmp_path).songs == []


def test_bad_music_and_unknown_song(tmp_path: Path):
    library = lib(tmp_path)
    song = library.add(SongIn(title="A", item="x"))
    with pytest.raises(BadMusicError):
        library.set_music(song.id, b"<script>")
    with pytest.raises(SongNotFoundError):
        library.get("nope")
    assert library.music_path(song.id) is None


def test_path_escape_in_file_is_ignored(tmp_path: Path):
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "songs.json").write_text(
        json.dumps([{"id": "a", "title": "A", "item": "x", "file": "../secret.txt"}]),
        encoding="utf-8",
    )
    library = lib(tmp_path)
    assert library.music_path("a") is None
    library.delete("a")
    assert (tmp_path / "secret.txt").exists()


def test_shared_file_kept_while_used(tmp_path: Path):
    music = tmp_path / "music"
    music.mkdir()
    (music / "same.mp3").write_bytes(MP3)
    (tmp_path / "songs.json").write_text(
        json.dumps(
            [
                {"id": "a", "title": "A", "item": "x", "file": "same.mp3"},
                {"id": "b", "title": "B", "item": "y", "file": "same.mp3"},
            ]
        ),
        encoding="utf-8",
    )
    library = lib(tmp_path)
    library.delete("a")
    assert (music / "same.mp3").exists()
    library.delete("b")
    assert not (music / "same.mp3").exists()
