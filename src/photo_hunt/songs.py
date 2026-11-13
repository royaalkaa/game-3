"""Список песен: описание в songs.json, музыка в папке music_dir."""

import contextlib
import json
import logging
import secrets
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

log = logging.getLogger(__name__)


class SongIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    item: str = Field(min_length=1, max_length=60)
    lyric: str = Field(default="", max_length=200)
    stop_at: float | None = Field(default=None, ge=0, le=3600)


class Song(SongIn):
    id: str = Field(default_factory=lambda: secrets.token_hex(4), min_length=1, max_length=16)
    file: str | None = Field(default=None, max_length=200)

    def public(self) -> dict[str, Any]:
        # Имя файла ведущему не нужно: музыка отдаётся по id песни.
        return {**self.model_dump(exclude={"file"}), "has_music": self.file is not None}


class SongNotFoundError(LookupError):
    pass


class BadMusicError(ValueError):
    pass


def detect_audio_ext(data: bytes) -> str | None:
    """Расширение берётся из содержимого файла, а не из имени, которое прислал браузер."""
    if data.startswith(b"ID3") or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return ".mp3"
    if data[4:8] == b"ftyp":
        return ".m4a"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"fLaC"):
        return ".flac"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    return None


def _read_list(path: Path) -> list[Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("songs file must contain a list")
    return data


class SongLibrary:
    def __init__(self, songs_file: Path, music_dir: Path) -> None:
        self._file = songs_file
        self._music_dir = music_dir
        self.songs: list[Song] = []

    @classmethod
    def load(cls, songs_file: Path, music_dir: Path) -> "SongLibrary":
        lib = cls(songs_file, music_dir)
        if songs_file.is_file():
            try:
                lib.songs = [Song.model_validate(s) for s in _read_list(songs_file)]
            except (ValidationError, ValueError) as exc:
                raise ValueError(f"songs file is invalid: {songs_file}") from exc
        if len({s.id for s in lib.songs}) != len(lib.songs):
            raise ValueError(f"songs file has duplicate ids: {songs_file}")
        return lib

    def get(self, song_id: str) -> Song:
        for song in self.songs:
            if song.id == song_id:
                return song
        raise SongNotFoundError(song_id)

    def add(self, data: SongIn) -> Song:
        song = Song(**data.model_dump())
        self.songs.append(song)
        self._save()
        return song

    def update(self, song_id: str, data: SongIn) -> Song:
        song = self.get(song_id)
        updated = song.model_copy(update=data.model_dump())
        self.songs[self.songs.index(song)] = updated
        self._save()
        return updated

    def set_music(self, song_id: str, data: bytes) -> Song:
        song = self.get(song_id)
        ext = detect_audio_ext(data)
        if ext is None:
            raise BadMusicError("Нужен файл mp3, m4a, ogg, flac или wav")
        self._music_dir.mkdir(parents=True, exist_ok=True)
        name = f"{song.id}-{secrets.token_hex(4)}{ext}"
        (self._music_dir / name).write_bytes(data)
        old = song.file
        updated = song.model_copy(update={"file": name})
        self.songs[self.songs.index(song)] = updated
        self._save()
        self._drop_file(old)
        return updated

    def delete(self, song_id: str) -> None:
        song = self.get(song_id)
        self.songs.remove(song)
        self._save()
        self._drop_file(song.file)

    def music_path(self, song_id: str) -> Path | None:
        song = self.get(song_id)
        return self._safe_path(song.file) if song.file else None

    def _safe_path(self, name: str) -> Path | None:
        base = self._music_dir.resolve()
        path = (base / name).resolve()
        if base not in path.parents or not path.is_file():
            return None
        return path

    def _drop_file(self, name: str | None) -> None:
        # Файл удаляем, только если он больше ни у одной песни не указан.
        if name is None or any(s.file == name for s in self.songs):
            return
        path = self._safe_path(name)
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink()

    def _save(self) -> None:
        # Пишем во временный файл и подменяем: при сбое старый список не портится.
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        payload = [s.model_dump() for s in self.songs]
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._file)
        log.info("songs_saved", extra={"count": len(self.songs)})
