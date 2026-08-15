/* ZuZhuang web UI controller */
(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };
  var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };

  var state = {
    target_os: "windows",
    python_version: null,
    packages: [],          // [{name, spec}]
    job_id: null,
    event_source: null,
  };

  // ---------- helpers ----------
  function api(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(path, Object.assign({}, opts, {
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    })).then(function (r) { return r.json(); });
  }

  function log(el, msg, cls) {
    var line = document.createElement("div");
    line.className = "line " + (cls || "");
    line.textContent = msg;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  // ---------- host capabilities ----------
  api("/api/host-capabilities").then(function (d) {
    if (!d.success) return;
    var warn = $("#host_warn");
    $$("input[name='target_os']").forEach(function (rb) {
      rb.addEventListener("change", function () {
        state.target_os = rb.value;
        loadVersions();
        checkHost(d);
      });
    });
    checkHost(d);
  });

  function checkHost(caps) {
    var warn = $("#host_warn");
    if (!caps.can_build[state.target_os]) {
      warn.textContent = "Note: this host (" + caps.host_os + ") may not fully support assembling for " + state.target_os + ". Windows embeddable zips can be built on any host; macOS/Linux builds require native toolchains.";
      warn.classList.remove("hidden");
    } else {
      warn.classList.add("hidden");
    }
  }

  // ---------- python versions ----------
  function loadVersions() {
    var sel = $("#python_version");
    sel.disabled = true;
    sel.innerHTML = "<option>loading…</option>";
    api("/api/python-versions?os=" + state.target_os).then(function (d) {
      sel.innerHTML = "";
      if (!d.success || !d.versions.length) {
        sel.innerHTML = "<option>(none — check network)</option>";
        return;
      }
      d.versions.slice(0, 40).forEach(function (v) {
        var o = document.createElement("option");
        o.value = v; o.textContent = v;
        sel.appendChild(o);
      });
      // default to a recent stable
      var pref = d.versions.find(function (v) { return /^3\.12\.\d+$/.test(v); }) || d.versions[0];
      sel.value = pref;
      state.python_version = pref;
      sel.disabled = false;
    }).catch(function () {
      sel.innerHTML = "<option>(failed to load)</option>";
    });
  }
  $("#refresh_versions").addEventListener("click", loadVersions);
  $("#python_version").addEventListener("change", function () {
    state.python_version = this.value;
  });

  // ---------- pypi search ----------
  function doSearch() {
    var q = $("#pkg_search").value.trim();
    if (!q) return;
    var ul = $("#search_results");
    ul.innerHTML = "<li class='muted'>searching…</li>";
    ul.classList.remove("hidden");
    api("/api/pypi/search?q=" + encodeURIComponent(q)).then(function (d) {
      ul.innerHTML = "";
      if (!d.success || !d.results.length) {
        ul.innerHTML = "<li class='muted'>no results</li>";
        return;
      }
      d.results.forEach(function (r) {
        var li = document.createElement("li");
        li.innerHTML = "<span class='name'>" + escapeHtml(r.name) + "</span><span class='desc'>" + escapeHtml(r.summary || "") + "</span>";
        li.addEventListener("click", function () {
          $("#pkg_input").value = r.name;
          ul.classList.add("hidden");
        });
        ul.appendChild(li);
      });
    });
  }
  $("#search_btn").addEventListener("click", doSearch);
  $("#pkg_search").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); doSearch(); }
  });

  // ---------- package list ----------
  function addPkg(spec) {
    spec = (spec || "").trim();
    if (!spec) return;
    var name = spec.split(/[<>=!~\[ ]/)[0];
    var existing = state.packages.find(function (p) { return p.name.toLowerCase() === name.toLowerCase(); });
    if (existing) {
      existing.spec = spec;
    } else {
      state.packages.push({ name: name, spec: spec });
    }
    renderTable();
  }
  function removePkg(name) {
    state.packages = state.packages.filter(function (p) { return p.name !== name; });
    renderTable();
  }
  function renderTable() {
    var tb = $("#pkg_table tbody");
    tb.innerHTML = "";
    state.packages.forEach(function (p) {
      var tr = document.createElement("tr");
      tr.innerHTML = "<td>" + escapeHtml(p.name) + "</td><td>" + escapeHtml(p.spec) + "</td>" +
        "<td><button class='rm' title='remove'>✕</button></td>";
      tr.querySelector(".rm").addEventListener("click", function () { removePkg(p.name); });
      tb.appendChild(tr);
    });
  }
  $("#add_btn").addEventListener("click", function () {
    addPkg($("#pkg_input").value);
    $("#pkg_input").value = "";
  });
  $("#pkg_input").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); addPkg(this.value); this.value = ""; }
  });

  // ---------- resolve on pypi ----------
  $("#resolve_btn").addEventListener("click", function () {
    var out = $("#resolve_out");
    out.classList.remove("hidden");
    out.innerHTML = "";
    log(out, "Resolving " + state.packages.length + " package(s) on PyPI…", "muted");
    var specs = state.packages.map(function (p) { return p.spec; });
    api("/api/pypi/resolve", { method: "POST", body: { packages: specs } }).then(function (d) {
      if (!d.success) { log(out, "Error: " + d.error, "err"); return; }
      (d.resolved || []).forEach(function (r) {
        log(out, "✓ " + r.spec, "ok");
      });
      (d.failed || []).forEach(function (r) {
        log(out, "✗ " + r.spec + " — " + r.error, "err");
      });
      log(out, "Resolved " + (d.resolved || []).length + " / " + ((d.resolved || []).length + (d.failed || []).length), "muted");
    });
  });

  // ---------- build ----------
  $("#build_btn").addEventListener("click", function () {
    if (!state.python_version) { alert("Pick a Python version first"); return; }
    var specs = state.packages.map(function (p) { return p.spec; });
    var prog = $("#progress");
    prog.classList.remove("hidden");
    prog.innerHTML = "";
    $("#download_area").classList.add("hidden");

    log(prog, "Submitting job: Python " + state.python_version + " for " + state.target_os, "muted");
    api("/api/jobs", {
      method: "POST",
      body: {
        python_version: state.python_version,
        packages: specs,
        target_os: state.target_os,
      },
    }).then(function (d) {
      if (!d.success) { log(prog, "Failed to start: " + d.error, "err"); return; }
      state.job_id = d.job_id;
      log(prog, "Job " + d.job_id + " queued. Streaming progress…", "muted");
      streamJob(d.job_id, prog);
    });
  });

  function streamJob(jobId, el) {
    if (state.event_source) { state.event_source.close(); }
    var es = new EventSource("/api/jobs/" + jobId + "/stream");
    state.event_source = es;
    es.onmessage = function (e) {
      var ev = JSON.parse(e.data);
      var cls = "muted";
      if (ev.status === "ok") cls = "ok";
      if (ev.status === "error" || ev.status === "fail") cls = "err";
      if (ev.status === "warn") cls = "warn";
      var msg = "[" + (ev.stage || "?") + "] " + (ev.message || "");
      if (ev.package) msg += " (" + ev.package + ")";
      log(el, msg, cls);
      if (ev.stage === "done") {
        es.close();
        state.event_source = null;
        var result = (ev.result || {});
        if (result.success) {
          log(el, "✓ Assembly complete. All dependencies verified.", "ok");
          var dl = $("#download_area");
          dl.classList.remove("hidden");
          $("#download_link").href = "/api/jobs/" + jobId + "/download";
        } else {
          log(el, "✗ " + (result.error || "Assembly failed"), "err");
          if (result.verify_log && result.verify_log.length) {
            result.verify_log.forEach(function (v) {
              if (!v.ok) log(el, "  import " + v.name + " failed: " + (v.error || ""), "err");
            });
          }
        }
      }
    };
    es.onerror = function () {
      log(el, "(stream interrupted — retrying)", "warn");
    };
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // init
  loadVersions();
})();