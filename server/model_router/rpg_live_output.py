"""Display-only journal for explicitly versioned character story requests."""
import asyncio
import codecs
import os

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse

from .rpg_progress import TextPreview, MAX_PREVIEW

SINGLE = "modelmirror.rpg.live-single/1"
GROUP = "modelmirror.rpg.live-group/1"
PROPOSAL = "modelmirror.rpg.memory-proposal/1"
_active = {}


class Channel:
    def __init__(self, path):
        self.path = path
        self.preview = self.task = None
        self.stopping = self.finishing = False
        self.written = 0

    def attach(self, task):
        self.task = task
        _active[self.path] = self
        self.watcher = asyncio.create_task(self.watch_stop())

    async def watch_stop(self):
        # A different worker can receive the owner's stop command.
        while not self.task.done():
            if self.path.with_suffix(".stop").exists():
                self.stop()
                return
            await asyncio.sleep(0.1)

    def detach(self):
        self.watcher.cancel()
        _active.pop(self.path, None)

    def begin(self, model, secrets):
        if self.stopping or self.path.with_suffix(".stop").exists():
            self.stopping = True
            raise ValueError("rpg_authoring_host_stopped")
        self.preview = TextPreview(model, secrets)

    def feed(self, chunk):
        if self.stopping:
            return
        self.preview.feed(chunk)
        text = self.preview.text
        # Withhold only a possible credential prefix. Complete echoes, tool
        # calls, model mismatches and non-text deltas have already been blocked.
        keep = max((n for s in self.preview.secrets for n in range(1, min(len(s), len(text)+1))
                    if text.endswith(s[:n])), default=0)
        end = len(text) - keep
        if not self.preview.disabled and end > self.written:
            with self.path.with_suffix(".visible").open("ab") as out:
                out.write(text[self.written:end].encode())
                out.flush()
                os.fsync(out.fileno())
            self.written = end

    def stop(self):
        if not self.stopping and not self.finishing:
            self.stopping = True
            if self.preview is not None:
                self.task.cancel()


def register(router, auth, get_stable, paths, read):
    def binding(stable, session, request, source):
        claim, result = paths(stable, session, request)
        if not claim.exists():
            raise HTTPException(404, "rpg_live_request_absent")
        value = read(claim)["request"]
        if value["sourceRequestHash"] != source or value.get("messageContract") not in (SINGLE, GROUP):
            raise HTTPException(409, "rpg_live_binding_changed")
        return result

    def snapshot(path, cursor):
        fragment = path.with_suffix(".visible")
        raw = fragment.read_bytes() if fragment.exists() else b""
        if len(raw) > MAX_PREVIEW * 4:
            raise HTTPException(503, "rpg_live_capacity")
        text = codecs.getincrementaldecoder("utf-8")().decode(raw, final=False)
        if not 0 <= cursor <= len(text):
            raise HTTPException(409, "rpg_live_cursor_invalid")
        ch = _active.get(path)
        stopped = path.with_suffix(".stop").exists()
        status = read(path)["status"] if path.exists() else ("stopping" if stopped and ch else "active" if ch else "unknown")
        return JSONResponse({"format": "modelmirror.rpg.live-progress/1", "cursor": cursor,
                             "next": len(text), "text": text[cursor:], "status": status,
                             "stopRequested": stopped}, headers={"Cache-Control": "no-store"})

    @router.get("/authoring/live/{session_id}/{request_id}", dependencies=[Depends(auth)])
    async def progress(session_id: str, request_id: str, sourceRequestHash: str, cursor: int = 0,
                       stable=Depends(get_stable)):
        return snapshot(binding(stable, session_id, request_id, sourceRequestHash), cursor)

    @router.post("/authoring/live/{session_id}/{request_id}/stop", dependencies=[Depends(auth)])
    async def stop(session_id: str, request_id: str, sourceRequestHash: str, stable=Depends(get_stable)):
        path = binding(stable, session_id, request_id, sourceRequestHash)
        ch = _active.get(path)
        if not path.exists() and (ch is None or not ch.finishing):
            try:
                with path.with_suffix(".stop").open("xb") as marker:
                    marker.flush()
                    os.fsync(marker.fileno())
            except FileExistsError:
                pass
            if ch:
                ch.stop()
        return snapshot(path, 0)
