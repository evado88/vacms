import csv
import io
import json
import logging
import zipfile
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from django.http import StreamingHttpResponse

logger = logging.getLogger(__name__)

_DOWNLOAD_CHUNK_SIZE = 64 * 1024


class _ZipStreamBuffer:
    """Minimal unseekable file object used by ``zipfile`` while streaming."""

    def __init__(self):
        self._buffer = bytearray()
        self._position = 0

    def write(self, data):
        self._buffer.extend(data)
        self._position += len(data)
        return len(data)

    def tell(self):
        return self._position

    def flush(self):
        return None

    def pop(self, *, force=False):
        if not self._buffer or (not force and len(self._buffer) < _DOWNLOAD_CHUNK_SIZE):
            return None
        chunk = bytes(self._buffer)
        self._buffer.clear()
        return chunk


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    return value


def _json_default(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _stream_csv_archive(member_name, columns, rows):
    sink = _ZipStreamBuffer()
    try:
        with zipfile.ZipFile(
            sink,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=1,
            allowZip64=True,
        ) as archive:
            with archive.open(member_name, mode="w", force_zip64=True) as member:
                text_stream = io.TextIOWrapper(
                    member,
                    encoding="utf-8",
                    newline="",
                    write_through=True,
                )
                try:
                    writer = csv.DictWriter(
                        text_stream,
                        fieldnames=columns,
                        extrasaction="ignore",
                        lineterminator="\n",
                    )
                    writer.writeheader()
                    chunk = sink.pop(force=True)
                    if chunk:
                        yield chunk

                    for row in rows:
                        writer.writerow(
                            {column: _csv_value(row.get(column)) for column in columns}
                        )
                        chunk = sink.pop()
                        if chunk:
                            yield chunk
                finally:
                    text_stream.flush()
                    text_stream.detach()

            chunk = sink.pop(force=True)
            if chunk:
                yield chunk

        chunk = sink.pop(force=True)
        if chunk:
            yield chunk
    except GeneratorExit:
        raise
    except Exception:
        logger.exception("Export stream failed while writing %s", member_name)
        raise


def _stream_json_archive(member_name, row_count, rows):
    """Stream the legacy JSON wrapper without materialising its records string."""

    sink = _ZipStreamBuffer()
    try:
        with zipfile.ZipFile(
            sink,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=1,
            allowZip64=True,
        ) as archive:
            with archive.open(member_name, mode="w", force_zip64=True) as member:
                member.write(f'{{"count": {row_count}, "records": "['.encode())
                first = True
                for row in rows:
                    if not first:
                        member.write(b",")
                    record_json = json.dumps(
                        row,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=_json_default,
                    )
                    escaped_record = json.dumps(record_json, ensure_ascii=False)[1:-1]
                    member.write(escaped_record.encode("utf-8"))
                    first = False
                    chunk = sink.pop()
                    if chunk:
                        yield chunk
                member.write(b']"}')

            chunk = sink.pop(force=True)
            if chunk:
                yield chunk

        chunk = sink.pop(force=True)
        if chunk:
            yield chunk
    except GeneratorExit:
        raise
    except Exception:
        logger.exception("Export stream failed while writing %s", member_name)
        raise


def _download_response(stream, filename):
    response = StreamingHttpResponse(stream, content_type="application/zip")
    response["Content-Disposition"] = f"attachment; filename={filename}"
    response["Cache-Control"] = "private, no-store"
    response["X-Accel-Buffering"] = "no"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def stream_csv_zip_response(filename, member_name, columns, rows):
    return _download_response(
        _stream_csv_archive(member_name, columns, rows),
        filename,
    )


def stream_json_zip_response(filename, member_name, row_count, rows):
    return _download_response(
        _stream_json_archive(member_name, row_count, rows),
        filename,
    )
