import os, time, logging
from pathlib import Path
from typing import Any, Dict
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from servicenow_client import ServiceNowClient

# Project uses a file named `env` (not `.env`)
ENV_FILE = Path(__file__).resolve().parent / "env"
load_dotenv(ENV_FILE, override=True)
load_dotenv(override=True)  # also allow a local `.env` override
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

DD_SITE = os.getenv("DD_SITE", "us5.datadoghq.com").strip()
PORT = int(os.getenv("PORT", "3000"))
PROXIES = {}
for k, e in [("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY")]:
    v = os.getenv(e, "").strip()
    if v: PROXIES[k] = v

OCP = ["ocpappprdclu2", "ssocpappprdclu", "ocpappprdclu", "ocpintprdclu2", "ocpintprdclu"]
CLEV = "(" + " OR ".join(f"kube_cluster:{c}" for c in OCP) + ")"
ENVMX = "env:prod OR env:prod2 OR env:drprod"
BASE_MX = ENVMX
DAYS = {"now-24h": 1, "now-2d": 2, "now-7d": 7, "now-14d": 14, "now-30d": 30, "now-90d": 90}

snow = ServiceNowClient()
_snow_cache: Dict[str, Any] = {}
SNOW_TTL = int(os.getenv("SNOW_CACHE_TTL", "300"))
SNOW_AG = os.getenv("SNOW_ASSIGNMENT_GROUP", "DIG-SOCE-SRE-OCP").strip()

def BASE():
    s = DD_SITE.strip().replace("https://", "").replace("http://", "")
    return f"https://api.{s}" if not s.startswith("api.") else f"https://{s}"

def H():
    load_dotenv(ENV_FILE, override=True)
    return {"DD-API-KEY": os.getenv("DD_API_KEY", "").strip(), "DD-APPLICATION-KEY": os.getenv("DD_APP_KEY", "").strip(), "Content-Type": "application/json"}

def V():
    load_dotenv(ENV_FILE, override=True)
    return [k for k in ["DD_SITE", "DD_API_KEY", "DD_APP_KEY"] if not os.getenv(k)]

_C: Dict[str, Any] = {}

def cg(k):
    it = _C.get(k)
    if not it: return None
    if it["e"] < time.time(): _C.pop(k, None); return None
    return it["v"]

def cs(k, v, t=180): _C[k] = {"v": v, "e": time.time() + t}

def snow_cg(k):
    it = _snow_cache.get(k)
    if not it: return None
    if it["e"] < time.time(): _snow_cache.pop(k, None); return None
    return it["v"]

def snow_cs(k, v, t=None): _snow_cache[k] = {"v": v, "e": time.time() + (t or SNOW_TTL)}

def sr(m, u, **kw):
    kw.setdefault("timeout", 20); kw.setdefault("verify", False)
    if PROXIES: kw.setdefault("proxies", PROXIES)
    return requests.request(m, u, **kw)

def lv(data):
    for s in data.get("series", []):
        pts = [p[1] for p in s.get("pointlist", []) if p[1] is not None]
        if pts: return pts[-1]
    return 0

@app.route("/")
def index(): return send_from_directory("static", "index.html")

@app.route("/app.js")
def appjs(): return send_from_directory("static", "app.js")

@app.route("/logo.png.webp")
def logo(): return send_from_directory("static", "logo.png.webp")

@app.get("/health")
def health():
    mv = V(); snow_ok = snow.is_configured()
    return jsonify({"status": "ok" if (not mv and snow_ok) else "warning", "dd_missing": mv, "dd_api_key_set": bool(os.getenv("DD_API_KEY", "")), "dd_app_key_set": bool(os.getenv("DD_APP_KEY", "")), "servicenow_configured": snow_ok, "servicenow_instance": "goindigo.service-now.com"})

@app.get("/api/snow/health")
def snow_health():
    return jsonify(snow.test_connection())

@app.get("/api/snow/discover-fields")
def snow_discover():
    if not snow.is_configured(): return jsonify({"error": "Not configured"}), 400
    try: return jsonify(snow.discover_fields())
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.get("/api/snow/test-tables")
def snow_test_tables():
    if not snow.is_configured(): return jsonify({"error": "Not configured"}), 400
    try: return jsonify(snow.test_tables())
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.get("/api/snow/ocp-work")
def snow_ocp_work():
    """Live CTASK + CHG + service list for DIG-SOCE-SRE-OCP."""
    from_date = request.args.get("from_date", "2025-01-01")
    to_date = request.args.get("to_date", None)
    ag = request.args.get("assignment_group", "") or SNOW_AG
    force = request.args.get("force_refresh", "false").lower() == "true"
    ck = f"ocp::{from_date}::{to_date}::{ag}"
    if not force:
        cached = snow_cg(ck)
        if cached:
            cached = dict(cached); cached["from_cache"] = True
            return jsonify(cached)
    if not snow.is_configured():
        return jsonify({"error": "ServiceNow not configured", "items": [], "ctask_count": 0}), 200
    try:
        result = snow.fetch_ocp_work_items(from_date=from_date, to_date=to_date, assignment_group=ag)
        result["from_cache"] = False
        snow_cs(ck, result)
        return jsonify(result)
    except Exception as e:
        logger.exception("OCP work fetch failed")
        return jsonify({"error": str(e), "items": [], "ctask_count": 0}), 200

@app.get("/api/snow/ocp-chgs")
def snow_ocp_chgs():
    """Change Requests assigned to OCP Bin (DIG-SOCE-SRE-OCP)."""
    from_date = request.args.get("from_date", "2025-01-01")
    to_date = request.args.get("to_date", None)
    ag = request.args.get("assignment_group", "") or SNOW_AG
    force = request.args.get("force_refresh", "false").lower() == "true"
    ck = f"ocpchg::{from_date}::{to_date}::{ag}"
    if not force:
        cached = snow_cg(ck)
        if cached:
            cached = dict(cached)
            cached["from_cache"] = True
            return jsonify(cached)
    if not snow.is_configured():
        return jsonify({"error": "ServiceNow not configured", "items": [], "total": 0}), 200
    try:
        result = snow.fetch_ocp_bin_change_requests(
            from_date=from_date, to_date=to_date, assignment_group=ag
        )
        result["from_cache"] = False
        snow_cs(ck, result)
        return jsonify(result)
    except Exception as e:
        logger.exception("OCP bin CHG fetch failed")
        return jsonify({"error": str(e), "items": [], "total": 0}), 200

@app.get("/api/snow/cmr-data")
def snow_cmr_data():
    from_date = request.args.get("from_date", "2025-01-01")
    to_date = request.args.get("to_date", None)
    ag = request.args.get("assignment_group", "") or SNOW_AG
    force = request.args.get("force_refresh", "false").lower() == "true"
    ck = f"snow::v5::{from_date}::{to_date}::{ag}"
    if not force:
        cached = snow_cg(ck)
        if cached:
            out = dict(cached)
            out["from_cache"] = True
            return jsonify(out)
    if not snow.is_configured():
        return jsonify({"error": "ServiceNow not configured", "cmr_data": [], "total": 0}), 200
    try:
        records = snow.fetch_change_requests(from_date=from_date, to_date=to_date, assignment_group=ag or None)
        result = snow.transform_to_dashboard_format(records)
        # Overall IndiGo: all DIG-* CHG close_code failures (Metric 3)
        indigo = snow.fetch_indigo_close_failures(from_date=from_date, to_date=to_date)
        result = snow.merge_indigo_failures(result, indigo)
        result["assignment_group"] = ag
        if result["total"] == 0 and result.get("permission_hint"):
            result["warning"] = result["permission_hint"]
        # Overall IndiGo cluster + DIG group classification
        by_cl = {}
        by_ag = {}
        for r in result.get("cmr_data") or []:
            cl = r.get("c") or "unknown"
            slot = by_cl.setdefault(cl, {"total": 0, "failures": 0, "unsuccessful": 0, "with_issues": 0})
            slot["total"] += 1
            if r.get("i"):
                slot["failures"] += 1
                if r.get("fk") == "unsuccessful" or r.get("r"):
                    slot["unsuccessful"] += 1
                elif r.get("fk") == "with_issues":
                    slot["with_issues"] += 1
        for inc in result.get("incidents") or []:
            agn = inc.get("assignment_group") or ("DIG-SOCE-SRE-OCP" if "ctask" in str(inc.get("source") or "") else "DIG")
            g = by_ag.setdefault(agn, {"failures": 0, "unsuccessful": 0, "with_issues": 0})
            g["failures"] += 1
            if inc.get("fail_kind") == "unsuccessful" or inc.get("rb"):
                g["unsuccessful"] += 1
            elif inc.get("fail_kind") == "with_issues":
                g["with_issues"] += 1
        result["overall_indigo"] = {
            "label": "Overall IndiGo · OCP CTASKs + all DIG-* close_code failures",
            "total_cmrs": result.get("total", 0),
            "total_ctasks": result.get("ctask_count", 0),
            "failures": (result.get("failure_stats") or {}).get("total_failures", 0),
            "indigo_close_failures": (result.get("indigo_failures") or {}).get("total", 0),
            "by_cluster": by_cl,
            "by_assignment_group": by_ag,
            "close_code_stats": result.get("close_code_stats"),
        }
        result["issue_sheet"] = {
            "loaded": False,
            "source": "servicenow_live",
            "note": (
                "Metric 3 = OCP CTASK/CMR close_code + Overall IndiGo DIG-* CHG "
                "Unsuccessful / Successful with issues. Time filter uses close/activity date."
            ),
        }
        result["from_cache"] = False
        snow_cs(ck, result)
        return jsonify(result)
    except Exception as e:
        logger.exception("ServiceNow CMR fetch failed")
        return jsonify({"error": str(e), "cmr_data": [], "total": 0}), 200

@app.post("/api/snow/cache-clear")
def snow_clear(): _snow_cache.clear(); return jsonify({"ok": True})

@app.post("/api/dd/events/analytics")
def ea():
    mv = V()
    if mv: return jsonify({"error": f"Missing:{mv}"}), 200
    body = request.get_json(silent=True) or {}; ck = f"a::{str(body)}"; cached = cg(ck)
    if cached: return jsonify(cached)
    try:
        r = sr("POST", f"{BASE()}/api/v2/events/analytics/search", headers=H(), json=body); d = r.json() if r.content else {}
        if not r.ok: return jsonify({"error": f"DD {r.status_code}"}), 200
        cs(ck, d, 120); return jsonify(d)
    except Exception as e: return jsonify({"error": str(e)}), 200

@app.get("/api/dd/query")
def ddq():
    mv = V()
    if mv: return jsonify({"error": f"Missing:{mv}"}), 200
    p = {"from": request.args.get("from"), "to": request.args.get("to"), "query": request.args.get("query")}
    if not all(p.values()): return jsonify({"error": "Required:from,to,query"}), 400
    ck = f"q::{p['from']}::{p['query']}"; cached = cg(ck)
    if cached: return jsonify(cached)
    try:
        r = sr("GET", f"{BASE()}/api/v1/query", headers=H(), params=p); d = r.json() if r.content else {}
        if not r.ok: return jsonify({"error": f"DD {r.status_code}"}), 200
        cs(ck, d, 180); return jsonify(d)
    except Exception as e: return jsonify({"error": str(e)}), 200

@app.get("/api/dora/deployment-frequency")
def ddf():
    mv = V(); fr = request.args.get("from", "now-30d"); cl = request.args.get("cluster", "")
    if mv:
        return jsonify({"error": f"Missing:{mv}", "total": 0, "per_day": 0, "days": DAYS.get(fr, 30), "clusters": [], "envs": [], "namespaces": [], "dora_level": "N/A"})
    q = f"source:change_tracking (env:prod OR env:prod2 OR env:drprod) {CLEV}"
    if cl: q += f" kube_cluster:{cl}"
    ck = f"df::{fr}::{cl}"; cached = cg(ck)
    if cached: return jsonify(cached)
    def fetch(facet):
        b = {"compute": [{"aggregation": "count", "type": "total"}], "filter": {"query": q, "from": fr, "to": "now"}, "group_by": [{"facet": facet, "limit": 20, "sort": {"aggregation": "count", "order": "desc"}}]}
        try: r = sr("POST", f"{BASE()}/api/v2/events/analytics/search", headers=H(), json=b); return r.json() if r.ok else {}
        except Exception: return {}
    def b2l(data, f): return [{"label": b.get("by", {}).get(f, "?"), "count": b.get("computes", {}).get("c0", 0)} for b in data.get("data", {}).get("buckets", [])]
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as ex:
            fc = ex.submit(fetch, "kube_cluster"); fe = ex.submit(fetch, "env"); fn = ex.submit(fetch, "kube_namespace")
        clusters = b2l(fc.result(), "kube_cluster"); envs = b2l(fe.result(), "env"); ns = b2l(fn.result(), "kube_namespace")
        total = sum(x["count"] for x in clusters)
        d = DAYS.get(fr, 30); pd = round(total/d, 2) if d > 0 else 0
        level = "Elite" if pd >= 1 else "High" if pd >= 0.14 else "Medium" if pd >= 0.03 else "Low" if total else "N/A"
        res = {"total": total, "per_day": pd, "days": d, "clusters": clusters, "envs": envs, "namespaces": ns[:10], "dora_level": level}
        cs(ck, res, 120); return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e), "total": 0, "per_day": 0, "days": DAYS.get(fr, 30), "clusters": [], "envs": [], "namespaces": [], "dora_level": "N/A"})

@app.get("/api/dora/lead-time")
def dlt():
    mv = V(); fr = request.args.get("from", "now-30d"); cl = request.args.get("cluster", "")
    if mv: return jsonify({"error": f"Missing:{mv}", "rows": [], "avg_sec": None, "display": "—", "dora_level": "N/A"})
    d = DAYS.get(fr, 30); nu = int(time.time()); mf = BASE_MX
    if cl: mf += f" kube_cluster_name:{cl}"
    ck = f"lt::{fr}::{cl}"; cached = cg(ck)
    if cached: return jsonify(cached)
    try:
        r = sr("GET", f"{BASE()}/api/v1/query", headers=H(), params={"from": nu-d*86400, "to": nu, "query": f"avg:kubernetes_state.deployment.rollout_duration{{{mf}}} by {{kube_cluster_name}}"})
        data = r.json() if r.content else {}
        if not r.ok: raise Exception(f"HTTP {r.status_code}")
        rows = []; ts = 0; cnt = 0
        for s in data.get("series", []):
            tag = next((t.replace("kube_cluster_name:", "") for t in s.get("tag_set", []) if t.startswith("kube_cluster_name:")), None)
            pts = [p[1] for p in s.get("pointlist", []) if p[1] is not None]
            if tag and pts: avg = sum(pts)/len(pts); rows.append({"l": tag, "c": round(avg)}); ts += avg; cnt += 1
        if cnt == 0:
            return jsonify({"rows": [], "avg_sec": None, "display": "—", "dora_level": "N/A"})
        avg = ts/cnt
        lv2 = "Elite" if avg <= 3600 else "High" if avg <= 86400 else "Medium" if avg <= 604800 else "Low"
        tx = f"{round(avg)}s" if avg < 60 else f"{avg/60:.1f}m" if avg < 3600 else f"{avg/3600:.1f}h"
        res = {"rows": rows, "avg_sec": round(avg, 1), "display": tx, "dora_level": lv2}; cs(ck, res, 180); return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e), "rows": [], "avg_sec": None, "display": "—", "dora_level": "N/A"})

@app.get("/api/dora/pod-health")
def pod_health():
    mv = V()
    if mv: return jsonify({"error": f"Missing:{mv}"})
    fr = request.args.get("from", "now-30d"); d = DAYS.get(fr, 30); nu = int(time.time())
    ck = f"ph::{fr}"; cached = cg(ck)
    if cached: return jsonify(cached)
    try:
        import concurrent.futures
        def qry(q): r = sr("GET", f"{BASE()}/api/v1/query", headers=H(), params={"from": nu-d*86400, "to": nu, "query": q}); return r.json() if r.ok else {}
        with concurrent.futures.ThreadPoolExecutor() as ex:
            fr2 = ex.submit(qry, f"sum:kubernetes_state.pod.status_phase{{phase:running,{BASE_MX}}}"); ff = ex.submit(qry, f"sum:kubernetes_state.pod.status_phase{{phase:failed,{BASE_MX}}}")
        rv = round(lv(fr2.result())); fv = round(lv(ff.result()))
        out = {"running": rv, "failed": fv, "restart_rate": None}
        cs(ck, out, 120); return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)})

@app.get("/api/dora/apm-error-rate")
def apm_er():
    mv = V()
    if mv: return jsonify({"error": f"Missing:{mv}"})
    fr = request.args.get("from", "now-30d"); d = DAYS.get(fr, 30); nu = int(time.time())
    ck = f"apm::{fr}"; cached = cg(ck)
    if cached: return jsonify(cached)
    try:
        import concurrent.futures
        def qry(q): r = sr("GET", f"{BASE()}/api/v1/query", headers=H(), params={"from": nu-d*86400, "to": nu, "query": q}); return r.json() if r.ok else {}
        with concurrent.futures.ThreadPoolExecutor() as ex:
            fe2 = ex.submit(qry, "sum:trace.http.request.errors{env:prod}.as_rate()"); fr3 = ex.submit(qry, "sum:trace.http.request{env:prod}.as_rate()"); fp = ex.submit(qry, "p99:trace.web.request.duration{env:prod}")
        err = lv(fe2.result()); req = lv(fr3.result()); p99 = lv(fp.result())
        rate = round((err/req)*100, 2) if req > 0 else None
        out = {
            "error_rate": rate,
            "p99_ms": round(p99/1e6) if p99 and p99 > 0 else None,
            "requests_per_min": round(req*60) if req and req > 0 else None,
        }
        cs(ck, out, 120); return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    print(f"\n  DORA Dashboard -> http://localhost:{PORT}")
    print(f"  Datadog:     API key={'Y' if os.getenv('DD_API_KEY') else 'N'}  APP key={'Y' if os.getenv('DD_APP_KEY') else 'N'}")
    print(f"  ServiceNow:  {'Y goindigo.service-now.com' if snow.is_configured() else 'N Not configured'}  group={SNOW_AG}")
    print()
    app.run(host="0.0.0.0", port=PORT, debug=True, threaded=True)