from pydantic import BaseModel, ConfigDict, Field


class TorrentCreate(BaseModel):
    qb_client_id: int | None = None
    source_url: str
    title: str | None = None
    save_path: str = ""
    category: str = ""
    tags: str = ""
    auto_update: bool = False
    recheck_after_add: bool = True
    start_after_recheck: bool = True
    add_paused: bool = True
    update_mode: str = "new_files_only"


class TorrentRead(TorrentCreate):
    id: int
    source_type: str
    tracker_type: str
    current_info_hash: str | None
    current_qb_hash: str | None
    current_torrent_name: str | None
    status: str
    last_error: str | None

    model_config = ConfigDict(from_attributes=True)


class TorrentCategoryUpdate(BaseModel):
    category: str = Field(default="", max_length=255)
