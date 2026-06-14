from pathlib import Path
from typing import Optional, Dict, Any


class FileInfo:
    def __init__(self, file_path, filetype: str, temp_dir):
        if isinstance(file_path, str):
            file_path = Path(file_path)
        elif not isinstance(file_path, Path):
            file_path = Path(str(file_path))

        if isinstance(temp_dir, str):
            temp_dir = Path(temp_dir)
        elif not isinstance(temp_dir, Path):
            temp_dir = Path(str(temp_dir))

        self._path = file_path
        self._dirname = str(file_path.parent)
        self._name = file_path.name
        self._stem = file_path.stem
        self._extension = file_path.suffix.lower()
        self._type = filetype.upper()
        self._temppath = temp_dir

        self._audio_track: Optional[Dict[str, Any]] = None
        self._selected_track: Optional[int] = None
        self._track_index: Optional[int] = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def name(self) -> str:
        return self._name

    @property
    def extension(self) -> str:
        return self._extension

    @property
    def type(self) -> str:
        return self._type

    @property
    def audio_track(self) -> Optional[Dict[str, Any]]:
        return self._audio_track

    @audio_track.setter
    def audio_track(self, value: Dict[str, Any]):
        self._audio_track = value

    @property
    def selected_track(self) -> Optional[int]:
        return self._selected_track

    @selected_track.setter
    def selected_track(self, value: int):
        self._selected_track = value

    @property
    def track_index(self) -> Optional[int]:
        return self._track_index

    @track_index.setter
    def track_index(self, value: int):
        self._track_index = value

    @property
    def is_audio(self) -> bool:
        from delay_check.config import get_config
        config = get_config()
        return config.is_audio(self._extension)

    def get_sample_path(self, display_id: Optional[int] = None) -> Path:
        if self.is_audio:
            return self._temppath / f"{self._stem}_sample.wav"
        else:
            if display_id is None:
                display_id = self._track_index
            return self._temppath / f"{self._stem}_AudioTrack_{display_id}_sample.wav"

    def exists(self) -> bool:
        return self._path.exists()

    def is_file(self) -> bool:
        return self._path.is_file()


