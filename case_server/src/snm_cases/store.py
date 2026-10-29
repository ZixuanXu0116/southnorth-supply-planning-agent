"""Append-only case stores: a local JSONL store (tests, offline runs, replay) and the Databricks store
(schema ``cases``, written by the case server's identity).

Both refuse a sequence number that is not the next one for the case, so concurrent writers cannot
interleave a case's events silently; neither offers an update or a delete.
"""

from __future__ import annotations

import fcntl
import io
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol


class StoreConflict(RuntimeError):
    """Another event was appended to the case first."""


@dataclass(frozen=True)
class Event:
    case_id: str
    seq: int
    event_type: str
    state_after: str
    plant: str
    material: str
    actor_kind: str
    actor_role: str
    facts: dict[str, Any]
    payload: dict[str, Any]
    run_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class Message:
    message_id: str
    case_id: str
    plant: str
    vendor: str
    direction: str
    author_kind: str
    channel: str
    body: str
    created_at: datetime


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    case_id: str
    plant: str
    vendor: str
    option_id: int
    kind: str
    quantity: float | None
    ship_date: date | None
    arrival_date: date | None
    unit_price: float | None
    surcharge_pct: float | None
    surcharge_amount: float | None
    freight_amount: float | None
    valid_until: date | None
    split_deliveries: str | None
    conditions: str | None
    source_message_id: str | None
    recorded_by: str
    created_at: datetime
    within_mandate: str | None = None  # JSON per mandate field plus "all"; NULL without a mandate


@dataclass(frozen=True)
class Attachment:
    attachment_id: str
    case_id: str
    plant: str
    vendor: str
    message_id: str | None
    direction: str
    file_name: str
    content_type: str
    size_bytes: int
    sha256: str
    storage_path: str
    parsed_options: int
    uploaded_by: str
    created_at: datetime


class CaseStore(Protocol):
    def append(
        self,
        event: Event,
        messages: list[Message] = ...,
        proposals: list[Proposal] = ...,
        attachments: list[Attachment] = ...,
    ) -> None: ...
    def events(self, case_id: str | None = None) -> list[Event]: ...
    def messages(self, case_id: str) -> list[Message]: ...
    def proposals(self, case_id: str) -> list[Proposal]: ...
    def attachments(self, case_id: str) -> list[Attachment]: ...
    def save_file(self, case_id: str, attachment_id: str, file_name: str, content: bytes) -> str: ...
    def read_file(self, storage_path: str) -> bytes: ...


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_date(value: Any) -> date | None:
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    return date.fromisoformat(str(value)[:10])


def event_from_row(row: dict[str, Any]) -> Event:
    facts, payload = row["facts"], row["payload"]
    return Event(
        case_id=row["case_id"],
        seq=int(row["seq"]),
        event_type=row["event_type"],
        state_after=row["state_after"],
        plant=row["plant"],
        material=row["material"],
        actor_kind=row["actor_kind"],
        actor_role=row["actor_role"],
        facts=json.loads(facts) if isinstance(facts, str) else facts,
        # A masked payload arrives as NULL for roles without cost access.
        payload=(json.loads(payload) if isinstance(payload, str) else payload) or {},
        run_id=row.get("run_id"),
        created_at=_parse_datetime(row["created_at"]),
    )


def proposal_from_row(row: dict[str, Any]) -> Proposal:
    def number(key: str) -> float | None:
        return None if row.get(key) is None else float(row[key])

    return Proposal(
        proposal_id=row["proposal_id"],
        case_id=row["case_id"],
        plant=row["plant"],
        vendor=row["vendor"],
        option_id=int(row["option_id"]),
        kind=row["kind"],
        quantity=number("quantity"),
        ship_date=_parse_date(row.get("ship_date")),
        arrival_date=_parse_date(row.get("arrival_date")),
        unit_price=number("unit_price"),
        surcharge_pct=number("surcharge_pct"),
        surcharge_amount=number("surcharge_amount"),
        freight_amount=number("freight_amount"),
        valid_until=_parse_date(row.get("valid_until")),
        split_deliveries=row.get("split_deliveries"),
        conditions=row.get("conditions"),
        source_message_id=row.get("source_message_id"),
        recorded_by=row["recorded_by"],
        created_at=_parse_datetime(row["created_at"]),
        within_mandate=row.get("within_mandate"),
    )


def attachment_from_row(row: dict[str, Any]) -> Attachment:
    return Attachment(
        row["attachment_id"], row["case_id"], row["plant"], row["vendor"], row.get("message_id"),
        row["direction"], row["file_name"], row["content_type"], int(row["size_bytes"]), row["sha256"],
        row["storage_path"], int(row.get("parsed_options") or 0), row["uploaded_by"],
        _parse_datetime(row["created_at"]),
    )  # fmt: skip


def message_from_row(row: dict[str, Any]) -> Message:
    return Message(**{**row, "created_at": _parse_datetime(row["created_at"])})


class LocalCaseStore:
    """JSONL files in one directory, guarded by a file lock (one writer at a time)."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.directory / f"{name}.jsonl"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with (self.directory / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self, name: str) -> list[dict[str, Any]]:
        path = self._path(name)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def _write(self, name: str, rows: list[dict[str, Any]]) -> None:
        with self._path(name).open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, default=_json_default, sort_keys=True) + "\n")

    def append(
        self,
        event: Event,
        messages: list[Message] | None = None,
        proposals: list[Proposal] | None = None,
        attachments: list[Attachment] | None = None,
    ) -> None:
        with self._locked():
            last = max(
                (row["seq"] for row in self._read("case_event") if row["case_id"] == event.case_id), default=0
            )
            if event.seq != last + 1:
                raise StoreConflict(f"{event.case_id}: expected seq {last + 1}, got {event.seq}")
            self._write("supplier_message", [asdict(message) for message in messages or []])
            self._write("supplier_proposal", [asdict(proposal) for proposal in proposals or []])
            self._write("supplier_attachment", [asdict(attachment) for attachment in attachments or []])
            self._write("case_event", [asdict(event)])

    def attachments(self, case_id: str) -> list[Attachment]:
        return [
            attachment_from_row(row) for row in self._read("supplier_attachment") if row["case_id"] == case_id
        ]

    def save_file(self, case_id: str, attachment_id: str, file_name: str, content: bytes) -> str:
        """Files live next to the JSONL rows: attachments/<case>/<attachment id>-<file name>."""
        directory = self.directory / "attachments" / case_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{attachment_id}-{Path(file_name).name}"
        path.write_bytes(content)
        return str(path.relative_to(self.directory))

    def read_file(self, storage_path: str) -> bytes:
        return (self.directory / storage_path).read_bytes()

    def events(self, case_id: str | None = None) -> list[Event]:
        rows = [row for row in self._read("case_event") if case_id is None or row["case_id"] == case_id]
        return sorted((event_from_row(row) for row in rows), key=lambda e: (e.case_id, e.seq))

    def messages(self, case_id: str) -> list[Message]:
        return [message_from_row(row) for row in self._read("supplier_message") if row["case_id"] == case_id]

    def proposals(self, case_id: str) -> list[Proposal]:
        return [
            proposal_from_row(row) for row in self._read("supplier_proposal") if row["case_id"] == case_id
        ]


EVENT_COLUMNS = (
    "case_id", "seq", "event_type", "state_after", "plant", "material", "actor_kind", "actor_role", "facts",
    "payload", "run_id", "created_at",
)  # fmt: skip
MESSAGE_COLUMNS = tuple(Message.__dataclass_fields__)
PROPOSAL_COLUMNS = tuple(Proposal.__dataclass_fields__)
ATTACHMENT_COLUMNS = tuple(Attachment.__dataclass_fields__)
ATTACHMENT_VOLUME = "attachments"


def insert_sql(catalog: str, table: str, columns: tuple[str, ...]) -> str:
    names = ", ".join(f"`{column}`" for column in columns)
    values = ", ".join(f":{column}" for column in columns)
    return f"INSERT INTO `{catalog}`.`cases`.`{table}` ({names}) VALUES ({values})"


def _parameters(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if isinstance(value, dict | list):
            value = json.dumps(value, default=_json_default, sort_keys=True)
        out[key] = value
    return out


class DatabricksCaseStore:
    """The ``cases`` schema in Unity Catalog. ``connect`` returns a DB-API connection (the case server's
    identity); statements use native named parameters."""

    def __init__(
        self,
        connect: Callable[[], Any],
        catalog: str,
        files: Callable[[], Any] | None = None,
    ) -> None:
        self.connect = connect
        self.catalog = catalog
        self._connection: Any = None
        self._files = files  # returns a workspace client whose ``files`` API reads and writes the volume

    def _cursor(self) -> Any:
        # One connection for the store's lifetime: opening one per statement costs seconds on Databricks.
        if self._connection is None:
            self._connection = self.connect()
        return self._connection.cursor()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _query(self, sql: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._cursor() as cursor:
            cursor.execute(sql, parameters or {})
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def append(
        self,
        event: Event,
        messages: list[Message] | None = None,
        proposals: list[Proposal] | None = None,
        attachments: list[Attachment] | None = None,
    ) -> None:
        last = self._query(
            f"SELECT COALESCE(MAX(seq), 0) AS last FROM `{self.catalog}`.`cases`.`case_event` "
            "WHERE case_id = :case_id",
            {"case_id": event.case_id},
        )[0]["last"]
        if event.seq != int(last) + 1:
            raise StoreConflict(f"{event.case_id}: expected seq {int(last) + 1}, got {event.seq}")
        with self._cursor() as cursor:
            for message in messages or []:
                cursor.execute(
                    insert_sql(self.catalog, "supplier_message", MESSAGE_COLUMNS),
                    _parameters(asdict(message)),
                )
            for proposal in proposals or []:
                cursor.execute(
                    insert_sql(self.catalog, "supplier_proposal", PROPOSAL_COLUMNS),
                    _parameters(asdict(proposal)),
                )
            for attachment in attachments or []:
                cursor.execute(
                    insert_sql(self.catalog, "supplier_attachment", ATTACHMENT_COLUMNS),
                    _parameters(asdict(attachment)),
                )
            # The event goes last: a case never refers to messages or proposals that are not stored.
            cursor.execute(insert_sql(self.catalog, "case_event", EVENT_COLUMNS), _parameters(asdict(event)))

    def events(self, case_id: str | None = None) -> list[Event]:
        where, parameters = ("WHERE case_id = :case_id", {"case_id": case_id}) if case_id else ("", {})
        rows = self._query(
            f"SELECT * FROM `{self.catalog}`.`cases`.`case_event` {where} ORDER BY case_id, seq", parameters
        )
        return [event_from_row(row) for row in rows]

    def messages(self, case_id: str) -> list[Message]:
        rows = self._query(
            f"SELECT * FROM `{self.catalog}`.`cases`.`supplier_message` WHERE case_id = :case_id "
            "ORDER BY created_at",
            {"case_id": case_id},
        )
        return [message_from_row(row) for row in rows]

    def proposals(self, case_id: str) -> list[Proposal]:
        rows = self._query(
            f"SELECT * FROM `{self.catalog}`.`cases`.`supplier_proposal` WHERE case_id = :case_id "
            "ORDER BY created_at, option_id",
            {"case_id": case_id},
        )
        return [proposal_from_row(row) for row in rows]

    def attachments(self, case_id: str) -> list[Attachment]:
        rows = self._query(
            f"SELECT * FROM `{self.catalog}`.`cases`.`supplier_attachment` WHERE case_id = :case_id "
            "ORDER BY created_at",
            {"case_id": case_id},
        )
        return [attachment_from_row(row) for row in rows]

    def volume_path(self, case_id: str, attachment_id: str, file_name: str) -> str:
        name = f"{attachment_id}-{Path(file_name).name}"
        return f"/Volumes/{self.catalog}/cases/{ATTACHMENT_VOLUME}/{case_id}/{name}"

    def save_file(self, case_id: str, attachment_id: str, file_name: str, content: bytes) -> str:
        if self._files is None:
            raise RuntimeError("the lakehouse case store has no file client for attachments")
        path = self.volume_path(case_id, attachment_id, file_name)
        self._files().files.upload(path, io.BytesIO(content), overwrite=True)
        return path

    def read_file(self, storage_path: str) -> bytes:
        if self._files is None:
            raise RuntimeError("the lakehouse case store has no file client for attachments")
        response = self._files().files.download(storage_path)
        return response.contents.read()
