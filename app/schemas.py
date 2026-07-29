from pydantic import BaseModel, Field


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

    class Config:
        from_attributes = True


class TorrentCategoryUpdate(BaseModel):
    category: str = Field(default="", max_length=255)


class SettingsUpdate(BaseModel):
    qb_host: str | None = None
    qb_username: str | None = None
    check_hour: int | None = Field(default=None, ge=0, le=23)
    check_minute: int | None = Field(default=None, ge=0, le=59)
    rutracker_cookie: str | None = None
    flaresolver_address: str | None = None
    flaresolver_port: int | None = Field(default=None, ge=1, le=65535)
