"""Flask web UI for ZuZhuang.

Provides a single-page app where the user:

* picks a Python version (served from python.org),
* picks a target OS (windows / macos / linux),
* searches PyPI and adds packages with version specifiers,
* kicks off an assembly job that runs in the background,
* watches live progress via Server-Sent Events,
* downloads the resulting zip once verification passes.

The module is imported lazily so the package still works without Flask
installed (the CLI's non-web commands don't need it).
"""

from __future__ import annotations

import json
import logging
import queue
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_file,
)

from .__version__ import __version__
from .orchestrator import JobResult, current_host_supports, host_can_verify, run_assembly
from .pypi import lookup_package, resolve_packages, search_packages

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"
STATIC_DIR = Path(__file__).parent / "web" / "static"


@dataclass
class Job:
    """A single assembly job tracked by the JobManager."""

    id: str
    status: str = "queued"  # queued | running | done | error
    created: float = field(default_factory=time.time)
    result: JobResult | None = None
    events: list[dict] = field(default_factory=list)
    subscribers: list[queue.Queue] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def push(self, event: dict) -> None:
        with self.lock:
            self.events.append(event)
            for q in self.subscribers:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1024)
        with self.lock:
            # Replay history first
            for e in self.events:
                try:
                    q.put_nowait(e)
                except queue.Full:
                    pass
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)


class JobManager:
    """Tracks all assembly jobs and broadcasts progress events."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def create(self, params: dict) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id)
        with self.lock:
            self.jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        with self.lock:
            return list(self.jobs.values())

    def start(self, job: Job, params: dict) -> None:
        """Launch the assembly in a background thread."""
        job.status = "running"
        job.push({"stage": "queued", "message": "Job queued", "ts": time.time()})

        def progress(ev: dict) -> None:
            job.push(ev)
            if ev.get("stage") == "done":
                job.status = "done"
            elif ev.get("status") == "error":
                job.status = "error"

        def on_done(result: JobResult) -> None:
            job.result = result
            if result.success:
                job.status = "done"
                job.push(
                    {
                        "stage": "done",
                        "message": "Assembly complete",
                        "status": "ok",
                        "ts": time.time(),
                        "result": result.to_dict(),
                    }
                )
            else:
                job.status = "error"
                job.push(
                    {
                        "stage": "done",
                        "message": result.error or "failed",
                        "status": "error",
                        "ts": time.time(),
                        "result": result.to_dict(),
                    }
                )

        out_dir = self.work_dir / job.id
        zip_path = self.work_dir / f"{job.id}.zip"

        thread = threading.Thread(
            target=self._run,
            args=(job, params, out_dir, zip_path, progress, on_done),
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _run(
        job: Job,
        params: dict,
        out_dir: Path,
        zip_path: Path,
        progress,
        on_done,
    ) -> None:
        try:
            result = run_assembly(
                python_version=params["python_version"],
                packages=params.get("packages", []),
                output_dir=out_dir,
                target_os=params.get("target_os"),
                force=True,
                progress=progress,
                do_verify=True,
                do_zip=True,
                zip_path=zip_path,
            )
        except Exception as e:
            result = JobResult(success=False, error=str(e))
        on_done(result)


def create_app(work_dir: str | Path | None = None) -> Flask:
    """Build and return the Flask app. Work dir defaults to a temp dir."""
    if work_dir is None:
        work_dir = Path(tempfile.gettempdir()) / "zuzhuang-web"
    manager = JobManager(Path(work_dir))

    app = Flask(
        __name__,
        template_folder=str(TEMPLATES_DIR),
        static_folder=str(STATIC_DIR),
    )
    app.config["JOB_MANAGER"] = manager
    app.config["WORK_DIR"] = str(manager.work_dir)

    # --- Pages ---
    @app.route("/")
    def index():
        from flask import render_template

        return render_template(
            "index.html",
            version=__version__,
            host_os=_host_os(),
        )

    # --- API: python versions ---
    @app.route("/api/python-versions")
    def python_versions():
        from .orchestrator import available_python_versions

        target = request.args.get("os")
        try:
            versions = available_python_versions(target)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        return jsonify({"success": True, "versions": versions})

    # --- API: pypi search ---
    @app.route("/api/pypi/search")
    def pypi_search():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"success": True, "results": []})
        try:
            results = search_packages(q, limit=25)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        return jsonify({"success": True, "results": results})

    # --- API: pypi lookup ---
    @app.route("/api/pypi/lookup")
    def pypi_lookup():
        name = request.args.get("name", "").strip()
        if not name:
            return jsonify({"success": False, "error": "name required"}), 400
        info = lookup_package(name)
        if info is None:
            return jsonify({"success": False, "error": "not found"}), 404
        return jsonify({"success": True, "package": info.to_dict()})

    # --- API: resolve packages ---
    @app.route("/api/pypi/resolve", methods=["POST"])
    def pypi_resolve():
        data = request.get_json(force=True, silent=True) or {}
        packages = data.get("packages", [])
        if not isinstance(packages, list):
            return jsonify({"success": False, "error": "packages must be a list"}), 400
        try:
            resolved, failed = resolve_packages(packages)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
        return jsonify({"success": True, "resolved": resolved, "failed": failed})

    # --- API: host capabilities ---
    @app.route("/api/host-capabilities")
    def host_caps():
        host = _host_os()
        return jsonify(
            {
                "success": True,
                "host_os": host,
                "can_build": {
                    "windows": current_host_supports("windows"),
                    "macos": current_host_supports("macos"),
                    "linux": current_host_supports("linux"),
                },
                "can_verify": {
                    "windows": host_can_verify("windows"),
                    "macos": host_can_verify("macos"),
                    "linux": host_can_verify("linux"),
                },
            }
        )

    # --- API: start a job ---
    @app.route("/api/jobs", methods=["POST"])
    def jobs_create():
        data = request.get_json(force=True, silent=True) or {}
        python_version = data.get("python_version")
        if not python_version:
            return jsonify({"success": False, "error": "python_version required"}), 400

        packages = data.get("packages", [])
        if not isinstance(packages, list):
            return jsonify({"success": False, "error": "packages must be a list"}), 400

        target_os = data.get("target_os")
        if target_os and not current_host_supports(target_os):
            logger.warning("host may not fully support target %s", target_os)

        job = manager.create(data)
        manager.start(
            job,
            {
                "python_version": python_version,
                "packages": packages,
                "target_os": target_os,
            },
        )
        return jsonify({"success": True, "job_id": job.id})

    # --- API: job status ---
    @app.route("/api/jobs/<job_id>")
    def job_status(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"success": False, "error": "not found"}), 404
        return jsonify(
            {
                "success": True,
                "job": {
                    "id": job.id,
                    "status": job.status,
                    "created": job.created,
                    "result": job.result.to_dict() if job.result else None,
                    "events": job.events[-50:],
                },
            }
        )

    # --- API: SSE stream ---
    @app.route("/api/jobs/<job_id>/stream")
    def job_stream(job_id: str):
        job = manager.get(job_id)
        if job is None:
            return jsonify({"success": False, "error": "not found"}), 404

        q = job.subscribe()

        def generate():
            try:
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev.get("stage") == "done":
                        break
            finally:
                job.unsubscribe(q)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- API: download zip ---
    @app.route("/api/jobs/<job_id>/download")
    def job_download(job_id: str):
        job = manager.get(job_id)
        if job is None or job.result is None or not job.result.success:
            return jsonify({"success": False, "error": "no artefact available"}), 404
        zp = Path(job.result.zip_path)
        if not zp.exists():
            return jsonify({"success": False, "error": "zip missing"}), 404
        name = f"python-{job_id}.zip"
        return send_file(zp, as_attachment=True, download_name=name)

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"success": False, "error": "not found"}), 404

    return app


def _host_os() -> str:
    import platform

    s = platform.system().lower()
    if s == "windows":
        return "windows"
    if s == "darwin":
        return "macos"
    return "linux"


def run(host: str = "127.0.0.1", port: int = 5000, work_dir: str | Path | None = None) -> None:
    """Start the web server (blocking)."""
    app = create_app(work_dir=work_dir)
    print(f"ZuZhuang web UI: http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
