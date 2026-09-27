"""REST API + WebSocket + раздача веб-интерфейса (FastAPI)."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scada_core.config.loader import ConfigError, parse_config_text
from scada_core.hmi.generator import generate_hmi
from scada_core.runtime import ScadaRuntime

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
MAX_YAML_BYTES = 256 * 1024


class WriteRequest(BaseModel):
    device: str
    tag: str
    value: float


class ShelveRequest(BaseModel):
    seconds: float = Field(default=900, ge=10, le=24 * 3600)


class FaultRequest(BaseModel):
    fault: str


class YamlRequest(BaseModel):
    yaml: str = Field(max_length=MAX_YAML_BYTES)


def create_app(runtime: ScadaRuntime) -> FastAPI:
    app = FastAPI(
        title="SCADA Generator API",
        version=runtime.meta()["version"],
        description=(
            "Self-generating, self-learning SCADA: Modbus TCP/RTU, OPC UA, MQTT, IEC 104; "
            "ISA-18.2 alarms; ML."
        ),
    )
    token = os.getenv("SCADA_API_TOKEN", "")

    def require_token(x_api_token: str | None = Header(default=None)) -> None:
        """Команды управления защищены токеном, если он задан (SCADA_API_TOKEN)."""
        if token and not hmac.compare_digest(x_api_token or "", token):
            raise HTTPException(status_code=401, detail="invalid or missing X-API-Token")

    control = [Depends(require_token)]

    @app.get("/api/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        """Liveness: процесс жив и отвечает (для Docker HEALTHCHECK)."""
        return {"status": "ok"}

    @app.get("/api/health")
    def health() -> JSONResponse:
        """Readiness: 200 — всё в порядке, 503 — нет связи с устройством или БД."""
        body = runtime.readiness()
        return JSONResponse(status_code=200 if body["status"] == "ok" else 503, content=body)

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        m = runtime.meta()
        m["auth_required"] = bool(token)
        return m

    @app.get("/api/hmi")
    def hmi() -> dict[str, Any]:
        return runtime.hmi

    @app.post("/api/hmi/preview")
    def hmi_preview(req: YamlRequest) -> JSONResponse:
        """«Генератор»: YAML -> мнемосхема, без применения к работающей системе."""
        try:
            # Окружение сервера не подставляем: YAML пришёл от пользователя.
            cfg = parse_config_text(req.yaml, use_environment=False)
        except ConfigError as exc:
            errors = [{"path": p, "message": m} for p, m in exc.errors]
            return JSONResponse(status_code=422, content={"errors": errors})
        return JSONResponse(content={"hmi": generate_hmi(cfg)})

    @app.get("/api/config/yaml")
    def config_yaml() -> dict[str, str]:
        path = Path(os.getenv("SCADA_CONFIG", "configs/config.yaml"))
        return {"yaml": path.read_text(encoding="utf-8") if path.exists() else ""}

    @app.get("/api/snapshot")
    def snapshot() -> dict[str, Any]:
        return runtime.snapshot()

    @app.get("/api/history/{device}/{tag}")
    def history(
        device: str, tag: str, seconds: float = Query(default=900, gt=0, le=7 * 24 * 3600)
    ) -> dict[str, Any]:
        tag_cfg = runtime.config.find_tag(device, tag)
        if tag_cfg is None:
            raise HTTPException(404, "unknown tag")
        data = runtime.store.history(f"{device}/{tag}", seconds, now=time.time())
        st = runtime.ml.tag_state(device, tag)
        forecast = None
        if st and st.eta_s is not None and data["ts"]:
            forecast = {"eta_s": st.eta_s, "kind": st.eta_kind, "limit": st.eta_limit}
        return {
            **data,
            "limits": tag_cfg.limits(),
            "unit": tag_cfg.unit,
            "trend_per_min": st.trend_per_min if st else None,
            "forecast": forecast,
        }

    @app.get("/api/archive/{device}/{tag}")
    async def archive(device: str, tag: str, limit: int = Query(default=1000, ge=1, le=100_000)) -> Any:
        if not runtime.repo:
            raise HTTPException(503, "PostgreSQL archive is not connected")
        rows = await runtime.repo.get_tag_history(device, tag, limit)
        return [
            {"ts": r["timestamp"].timestamp(), "value": r["value"], "quality": r["quality"]} for r in rows
        ]

    @app.get("/api/alarms")
    def alarms() -> dict[str, Any]:
        return {
            "active": [a.to_dict() for a in runtime.alarms.visible_alarms()],
            "journal": list(runtime.alarms.journal)[-300:],
        }

    @app.get("/api/alarms/kpi")
    def alarms_kpi() -> dict[str, Any]:
        return runtime.alarms.kpi()

    @app.post("/api/alarms/{alarm_id}/ack", dependencies=control)
    def ack(alarm_id: int) -> dict[str, bool]:
        return {"ok": runtime.alarms.acknowledge(alarm_id)}

    @app.post("/api/alarms/ack_all", dependencies=control)
    def ack_all() -> dict[str, int]:
        return {"acknowledged": runtime.alarms.acknowledge_all()}

    @app.post("/api/alarms/{alarm_id}/shelve", dependencies=control)
    def shelve(alarm_id: int, req: ShelveRequest) -> dict[str, bool]:
        return {"ok": runtime.alarms.shelve(alarm_id, req.seconds)}

    @app.get("/api/ml/status")
    def ml_status() -> dict[str, Any]:
        return runtime.ml.status()

    @app.get("/api/ml/insights")
    def ml_insights() -> list[dict[str, Any]]:
        return [i.to_dict() for i in reversed(runtime.ml.insights)]

    @app.post("/api/ml/retrain", dependencies=control)
    def ml_retrain(device: str | None = None) -> dict[str, Any]:
        return {"retraining": runtime.ml.retrain(device)}

    @app.post("/api/ml/rebase/{device}", dependencies=control)
    def ml_rebase(device: str, tag: str | None = None) -> dict[str, bool]:
        return {"ok": runtime.ml.rebase(device, tag)}

    @app.post("/api/write", dependencies=control)
    async def write(req: WriteRequest) -> dict[str, bool]:
        try:
            await runtime.write(req.device, req.tag, req.value)
        except KeyError as exc:
            raise HTTPException(404, "unknown tag") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"ok": True}

    # ---------- демо-сценарии ----------
    def _sim() -> Any:
        if not runtime.simulator:
            raise HTTPException(404, "fault injection is available only in --demo mode")
        return runtime.simulator.model

    @app.get("/api/sim/faults")
    def sim_faults() -> dict[str, Any]:
        model = _sim()
        return {"available": runtime.meta()["faults"], "active": model.active_faults}

    @app.post("/api/sim/fault", dependencies=control)
    def sim_inject(req: FaultRequest) -> dict[str, Any]:
        model = _sim()
        try:
            model.inject(req.fault)
        except KeyError as exc:
            raise HTTPException(404, "unknown fault") from exc
        return {"active": model.active_faults}

    @app.delete("/api/sim/fault", dependencies=control)
    def sim_clear(fault: str | None = None) -> dict[str, Any]:
        model = _sim()
        model.clear(fault)
        return {"active": model.active_faults}

    # ---------- WebSocket ----------
    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = runtime.subscribe()
        try:
            await websocket.send_json({"type": "snapshot", "data": runtime.snapshot()})
            while True:
                msg = await queue.get()
                await websocket.send_json(msg)
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass
        finally:
            runtime.unsubscribe(queue)
            with contextlib.suppress(Exception):
                await websocket.close()

    # ---------- статика ----------
    if WEB_DIR.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")
        app.mount("/i18n", StaticFiles(directory=WEB_DIR / "i18n"), name="i18n")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app
