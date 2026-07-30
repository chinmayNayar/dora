"""
ServiceNow API Client for DORA Dashboard
Connects to goindigo.service-now.com via OAuth2

Data model (IndiGo):
  - CTASK (change_task) assigned to DIG-SOCE-SRE-OCP  ← entry point
  - Parent CHG (change_request) for Change Request Number + Configuration Item (service)
"""

import os
import re
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ENV_FILE = Path(__file__).resolve().parent / "env"


class ServiceNowClient:

    def __init__(self):
        load_dotenv(ENV_FILE, override=True)
        load_dotenv(override=True)
        self.instance = os.getenv("SNOW_INSTANCE", "goindigo.service-now.com").strip()
        self.client_id = os.getenv("SNOW_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("SNOW_CLIENT_SECRET", "").strip()
        self.username = os.getenv("SNOW_USERNAME", "").strip()
        self.password = os.getenv("SNOW_PASSWORD", "").strip()
        self._token: Optional[str] = None
        self._token_expiry: float = 0
        self._last_ocp_bundle: Dict[str, Any] = {}

        self.CLUSTER_MAP = {
            "b2c": "app", "skyplus": "app", "6e skyplus": "app",
            "hotels": "app", "hotel": "app", "shop": "app",
            "cabs": "app", "cab": "app", "sightseeing": "app",
            "marketplace": "app", "mobile app": "app",
            "loyalty": "app", "ventures": "app", "offer miniapp": "app",
            "visa2fly": "app", "self service": "app", "salesforce": "app",
            "communication service": "app", "cobrand": "app",
            "booking": "app", "voucher": "app",
            "indigo access": "ap2", "agent portal": "ap2",
            "6e partner": "ap2", "b2b": "ap2",
            "slt": "sso", "6e slt": "sso", "breez": "sso",
            "6e breez": "sso", "career": "sso", "6e career": "sso",
            "success factor": "sso",
            "occ hub": "int", "6e occ hub": "int", "occhub": "int",
            "aocs": "int", "prs": "int", "flight api": "int",
            "cruuz": "int", "6e cruuz": "int", "crewz": "int",
            "ucg": "int", "notification hub": "int",
            "pilot feedback": "int", "breath analyzer": "int",
            "wingops": "int", "flight papers": "int", "flight paper": "int",
            "egca": "int", "boarding": "int", "skygo": "int",
            "opticlimb": "int", "cops": "int", "nps": "int",
            "crew": "int", "airline op": "int", "employee diary": "int",
            "mpos": "int", "orca": "int", "camel": "int",
            "hotac": "int", "clob": "int", "new skies": "int",
            "mccmel": "int", "spo service": "int",
            "sap": "in2", "gems": "in2", "snowflake": "in2",
            "genesys": "in2", "prims": "in2", "natgrid": "in2",
            "agile report": "in2",
        }

        self.FULL_CLUSTER_MAP = {
            "ocpappprdclu": "app", "ocpappprdclu2": "ap2",
            "ssocpappprdclu": "sso", "ocpintprdclu": "int",
            "ocpintprdclu2": "in2",
        }

    def _base_url(self) -> str:
        inst = self.instance.replace("https://", "").replace("http://", "").rstrip("/")
        return f"https://{inst}"

    def _authenticate(self) -> str:
        if self._token and time.time() < self._token_expiry:
            return self._token

        url = f"{self._base_url()}/oauth_token.do"
        payload = {
            "grant_type": "password",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "username": self.username,
            "password": self.password,
        }
        resp = requests.post(url, data=payload, timeout=20, verify=True)
        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"No access_token in response: {data}")

        self._token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 1800)) - 300
        logger.info("ServiceNow token acquired (expires in %ss)", data.get("expires_in"))
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._authenticate()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _table_get(self, table: str, params: Dict[str, str],
                   page_size: int = 500, max_records: Optional[int] = None) -> List[Dict]:
        url = f"{self._base_url()}/api/now/table/{table}"
        all_records: List[Dict] = []
        offset = 0
        # Honor caller limit when smaller than page_size (e.g. discover probes).
        try:
            requested = int(params.get("sysparm_limit", page_size))
        except (TypeError, ValueError):
            requested = page_size
        limit = min(page_size, requested) if requested > 0 else page_size
        if max_records is None and requested > 0 and requested < page_size:
            max_records = requested
        base_params = {k: v for k, v in params.items() if k not in ("sysparm_offset", "sysparm_limit")}

        while True:
            page = dict(base_params)
            page["sysparm_offset"] = str(offset)
            page["sysparm_limit"] = str(limit)
            resp = requests.get(
                url, headers=self._headers(), params=page,
                timeout=30, verify=True
            )
            resp.raise_for_status()
            records = resp.json().get("result", [])
            all_records.extend(records)
            if max_records is not None and len(all_records) >= max_records:
                return all_records[:max_records]
            if len(records) < limit:
                break
            offset += limit

        return all_records

    @staticmethod
    def _ref_number(val: Any) -> str:
        if isinstance(val, dict):
            return (val.get("display_value") or val.get("value") or "").strip()
        return (str(val) if val is not None else "").strip()

    @staticmethod
    def _dv(val: Any) -> str:
        """Normalize display_value=all / true / plain string fields."""
        if isinstance(val, dict):
            return (val.get("display_value") or val.get("value") or "").strip()
        return (str(val) if val is not None else "").strip()

    @staticmethod
    def parse_ms_mf_from_description(description: str) -> Tuple[List[str], List[str]]:
        """
        Extract microservice / microfrontend names from CTASK description.
        Supports IndiGo formats like:
          Microservices (MS) :18 releases/foo/1.0.4 ... Microfrontends (MF) :6 releases/bar/1.1.7
          Jenkins job URLs .../job/Microservices/.../job/<name>
        """
        text = description or ""
        ms: List[str] = []
        mf: List[str] = []

        # Split MS / MF sections when labeled
        ms_part, mf_part = text, ""
        m = re.search(r"Microfrontends?\s*\(MF\)\s*:?", text, re.I)
        if m:
            ms_part = text[:m.start()]
            mf_part = text[m.start():]
        else:
            m2 = re.search(r"\bMF\s*:", text, re.I)
            if m2:
                ms_part = text[:m2.start()]
                mf_part = text[m2.start():]

        def from_releases(chunk: str) -> List[str]:
            # releases/<name>/<version>  OR  name without path
            names = re.findall(r"releases/([A-Za-z0-9._-]+)/", chunk)
            if names:
                return names
            return []

        def from_jenkins(chunk: str, kind: str) -> List[str]:
            # .../job/Microservices/job/.../job/<svc>  or Microfrontend
            pat = rf"/job/{kind}/job/(?:[^/\s]+/job/)*([A-Za-z0-9._-]+)/?"
            return re.findall(pat, chunk, flags=re.I)

        ms.extend(from_releases(ms_part))
        mf.extend(from_releases(mf_part))
        ms.extend(from_jenkins(text, "Microservices"))
        mf.extend(from_jenkins(text, "Microfrontends?"))
        mf.extend(from_jenkins(text, "Microfrontend"))

        # de-dupe preserve order
        def uniq(seq: List[str]) -> List[str]:
            seen = set()
            out = []
            for s in seq:
                s = s.strip()
                if not s or s.lower() in seen:
                    continue
                seen.add(s.lower())
                out.append(s)
            return out

        return uniq(ms), uniq(mf)

    def _group_sys_id(self, group_name: str) -> str:
        groups = self._table_get("sys_user_group", {
            "sysparm_query": f"name={group_name}",
            "sysparm_fields": "sys_id,name",
            "sysparm_limit": "1",
        })
        if not groups:
            raise RuntimeError(f"Assignment group not found: {group_name}")
        return groups[0]["sys_id"]

    def _in_date_range(self, date_str: str, from_date: str, to_date: str) -> bool:
        if not date_str:
            return False
        d = date_str[:10]
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            return False
        return from_date <= d <= to_date

    # ── CTASK → CHG → service ──────────────────────────────

    def fetch_group_ctasks(self, from_date: str = "2025-01-01",
                           to_date: Optional[str] = None,
                           assignment_group: str = "DIG-SOCE-SRE-OCP") -> List[Dict]:
        """
        Fetch CTASKs for DIG-SOCE-SRE-OCP.
        Query by group sys_id only (date filters on planned_* can 403);
        filter dates in Python.
        """
        if to_date is None:
            to_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        fields = [
            "number", "short_description", "description",
            "change_task_type", "state",
            "assignment_group", "assigned_to",
            "change_request", "parent", "cmdb_ci",
            "planned_start_date", "planned_end_date",
            "work_start", "work_end", "closed_at", "sys_created_on",
        ]
        gid = self._group_sys_id(assignment_group)
        # Do NOT put planned_start_date in the encoded query — that field ACL caused 403.
        rows = self._table_get("change_task", {
            "sysparm_query": f"assignment_group={gid}^ORDERBYDESCsys_created_on",
            "sysparm_fields": ",".join(fields),
            "sysparm_display_value": "true",
            "sysparm_exclude_reference_link": "true",
        })

        filtered = []
        for t in rows:
            start = (
                self._dv(t.get("planned_start_date"))
                or self._dv(t.get("work_start"))
                or self._dv(t.get("closed_at"))
                or self._dv(t.get("sys_created_on"))
                or ""
            )
            if self._in_date_range(start, from_date, to_date):
                t["_start_raw"] = start
                filtered.append(t)
        return filtered

    def fetch_change_requests_by_numbers(self, numbers: List[str]) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        nums = [n for n in numbers if n and str(n).startswith("CHG")]
        if not nums:
            return out
        fields = [
            "number", "short_description", "description",
            "start_date", "end_date", "state", "close_code",
            "assignment_group", "assigned_to", "cmdb_ci",
            "category", "type", "risk",
        ]
        for i in range(0, len(nums), 50):
            batch = nums[i:i + 50]
            try:
                rows = self._table_get("change_request", {
                    "sysparm_query": "numberIN" + ",".join(batch),
                    "sysparm_fields": ",".join(fields),
                    "sysparm_display_value": "true",
                    "sysparm_exclude_reference_link": "true",
                })
            except requests.HTTPError as e:
                logger.warning("CHG batch fetch failed: %s", e)
                rows = []
            for row in rows:
                num = self._dv(row.get("number"))
                if num:
                    out[num] = row
        return out

    def fetch_ocp_work_items(self, from_date: str = "2025-01-01",
                             to_date: Optional[str] = None,
                             assignment_group: str = "DIG-SOCE-SRE-OCP") -> Dict[str, Any]:
        """Live: CTASKs for group → parent CHG → Configuration Item + MS/MF list."""
        try:
            ctasks = self.fetch_group_ctasks(from_date, to_date, assignment_group)
            query_error = None
        except Exception as e:
            ctasks = []
            query_error = str(e)
            logger.exception("fetch_group_ctasks failed")

        for t in ctasks:
            chg = self._dv(t.get("change_request")) or self._dv(t.get("parent"))
            t["_chg_number"] = chg if chg.startswith("CHG") else ""

        chg_nums = sorted({t["_chg_number"] for t in ctasks if t.get("_chg_number")})
        chg_map = self.fetch_change_requests_by_numbers(chg_nums)

        items = []
        for t in ctasks:
            chg_num = t.get("_chg_number") or ""
            chg = chg_map.get(chg_num, {})
            service = (
                self._dv(t.get("cmdb_ci"))
                or self._dv(chg.get("cmdb_ci"))
                or ""
            )
            start = t.get("_start_raw") or ""
            end = self._dv(t.get("planned_end_date")) or self._dv(t.get("work_end")) or ""
            desc = self._dv(t.get("description"))
            ms_names, mf_names = self.parse_ms_mf_from_description(desc)

            lt_hours = None
            if start and end:
                try:
                    sdt = datetime.strptime(start[:16], "%Y-%m-%d %H:%M")
                    edt = datetime.strptime(end[:16], "%Y-%m-%d %H:%M")
                    hrs = (edt - sdt).total_seconds() / 3600.0
                    if hrs > 0:
                        lt_hours = round(hrs, 2)
                except ValueError:
                    pass

            items.append({
                "ctask": self._dv(t.get("number")),
                "ctask_short": self._dv(t.get("short_description")),
                "ctask_type": self._dv(t.get("change_task_type")) or self._dv(t.get("type")),
                "ctask_state": self._dv(t.get("state")),
                "assigned_to": self._dv(t.get("assigned_to")),
                "assignment_group": self._dv(t.get("assignment_group")) or assignment_group,
                "planned_start": self._dv(t.get("planned_start_date")),
                "planned_end": self._dv(t.get("planned_end_date")),
                "description": desc,
                "chg": chg_num,
                "chg_short": self._dv(chg.get("short_description")),
                "chg_state": self._dv(chg.get("state")),
                "chg_assignment_group": self._dv(chg.get("assignment_group")),
                "service": service,
                "start": start[:10] if start else "",
                "ms_names": ms_names,
                "mf_names": mf_names,
                "lt_hours": lt_hours,
            })

        hint = None
        if query_error:
            hint = f"ServiceNow query error: {query_error}"
        elif not ctasks:
            hint = "No CTASKs found for DIG-SOCE-SRE-OCP in the selected date range."

        bundle = {
            "assignment_group": assignment_group,
            "ctask_count": len(ctasks),
            "chg_count": len({i["chg"] for i in items if i.get("chg")}),
            "ocp_owned_chg_count": len({
                i["chg"] for i in items
                if i.get("chg") and (
                    (i.get("chg_assignment_group") or "") == assignment_group
                    or "SRE-OCP" in (i.get("chg_assignment_group") or "")
                )
            }),
            "items": items,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "permission_hint": hint,
            "query_error": query_error,
        }
        self._last_ocp_bundle = bundle
        return bundle

    def fetch_ocp_bin_change_requests(
        self,
        from_date: str = "2025-01-01",
        to_date: Optional[str] = None,
        assignment_group: str = "DIG-SOCE-SRE-OCP",
    ) -> Dict[str, Any]:
        """
        Change Requests assigned to OCP Bin (DIG-SOCE-SRE-OCP).
        Fields match Indigo Fulfiller form (Middleware OCP, CI, opened/requested by, etc.).
        """
        if to_date is None:
            to_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        gid = self._group_sys_id(assignment_group)
        fields = [
            "number", "short_description", "description",
            "state", "type", "risk", "priority", "category",
            "cmdb_ci", "assignment_group", "assigned_to",
            "opened_by", "requested_by", "opened_at",
            "start_date", "end_date", "work_start", "work_end",
            "close_code", "conflict_status", "user_input",
            "u_subcategory", "u_db_change",
            "x_kpmg3_pit_change_u_middleware_ocp",
            "x_kpmg3_pit_change_u_outage",
            "x_kpmg3_pit_change_u_navitaire_impact",
            "x_kpmg3_pit_change_u_fronend_change",
            "x_kpmg3_pit_change_u_front_end",
            "x_kpmg3_pit_change_u_change_have_impact",
            "x_kpmg3_pit_change_u_backend_change",
            "x_kpmg3_pit_change_sub_state",
        ]
        try:
            rows = self._table_get("change_request", {
                "sysparm_query": f"assignment_group={gid}^ORDERBYDESCopened_at",
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            })
            query_error = None
        except Exception as e:
            rows = []
            query_error = str(e)
            logger.exception("fetch_ocp_bin_change_requests failed")

        items = []
        for r in rows:
            opened = self._dv(r.get("opened_at"))
            start = self._dv(r.get("start_date")) or self._dv(r.get("work_start")) or opened
            if not self._in_date_range(start or opened or "9999-99-99", from_date, to_date):
                # Prefer planned start; fall back to opened_at for date window
                if not self._in_date_range(opened or "9999-99-99", from_date, to_date):
                    continue
            front = (
                self._dv(r.get("x_kpmg3_pit_change_u_fronend_change"))
                or self._dv(r.get("x_kpmg3_pit_change_u_front_end"))
                or ""
            )
            items.append({
                "number": self._dv(r.get("number")),
                "short_description": self._dv(r.get("short_description")),
                "description": self._dv(r.get("description")),
                "state": self._dv(r.get("state")),
                "sub_state": self._dv(r.get("x_kpmg3_pit_change_sub_state")),
                "type": self._dv(r.get("type")),
                "risk": self._dv(r.get("risk")),
                "priority": self._dv(r.get("priority")),
                "category": self._dv(r.get("category")),
                "subcategory": self._dv(r.get("u_subcategory")),
                "configuration_item": self._dv(r.get("cmdb_ci")),
                "assignment_group": self._dv(r.get("assignment_group")) or assignment_group,
                "assigned_to": self._dv(r.get("assigned_to")),
                "opened_by": self._dv(r.get("opened_by")),
                "requested_by": self._dv(r.get("requested_by")),
                "opened_at": opened,
                "start_date": self._dv(r.get("start_date")),
                "end_date": self._dv(r.get("end_date")),
                "close_code": self._dv(r.get("close_code")),
                "conflict_status": self._dv(r.get("conflict_status")),
                "change_reason": self._dv(r.get("user_input")),
                "middleware_ocp": self._dv(r.get("x_kpmg3_pit_change_u_middleware_ocp")),
                "outage": self._dv(r.get("x_kpmg3_pit_change_u_outage")),
                "navitaire_impact": self._dv(r.get("x_kpmg3_pit_change_u_navitaire_impact")),
                "front_end": front,
                "database": self._dv(r.get("u_db_change")),
                "web_mobile_impact": self._dv(r.get("x_kpmg3_pit_change_u_change_have_impact")),
                "backend_change": self._dv(r.get("x_kpmg3_pit_change_u_backend_change")),
            })

        mw_yes = sum(1 for i in items if str(i.get("middleware_ocp", "")).lower() == "yes")
        return {
            "assignment_group": assignment_group,
            "total": len(items),
            "middleware_ocp_yes": mw_yes,
            "items": items,
            "from_date": from_date,
            "to_date": to_date,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "query_error": query_error,
            "permission_hint": (
                f"ServiceNow query error: {query_error}" if query_error
                else (None if items else f"No change_request rows for {assignment_group} in range.")
            ),
        }

    def fetch_change_requests(self, from_date: str = "2025-01-01",
                               to_date: Optional[str] = None,
                               assignment_group: Optional[str] = None) -> List[Dict]:
        """Collapse OCP CTASKs into one CHG-level record for the dashboard."""
        ag = assignment_group or "DIG-SOCE-SRE-OCP"
        bundle = self.fetch_ocp_work_items(from_date, to_date, ag)

        by_chg: Dict[str, Dict] = {}
        for it in bundle["items"]:
            chg = it["chg"] or f"NOCHG:{it['ctask']}"
            if chg not in by_chg:
                by_chg[chg] = {
                    "number": it["chg"] or "",
                    "short_description": it["chg_short"] or it["ctask_short"] or it["service"] or "Unknown",
                    "start_date": it["start"] or (it["planned_start"][:10] if it["planned_start"] else ""),
                    "end_date": it["planned_end"] or "",
                    "state": it["chg_state"] or it["ctask_state"] or "",
                    "close_code": "",
                    "assignment_group": it["assignment_group"],
                    "cmdb_ci": it["service"],
                    "u_project": it["service"] or it["chg_short"] or it["ctask_short"],
                    "u_cluster": "",
                    "u_microservices": "",
                    "u_microfrontends": "",
                    "u_new_microservices": "",
                    "u_new_microfrontends": "",
                    "u_ms_count": "0",
                    "u_mf_count": "0",
                    "u_nms_count": "0",
                    "u_nmf_count": "0",
                    "u_incident_flag": "",
                    "u_rollback": "",
                    "u_mttr_hours": "0",
                    "u_incident_description": "",
                    "_ctasks": [],
                    "_services": set(),
                    "_ms": [],
                    "_mf": [],
                    "_lt_hours": [],
                }
            by_chg[chg]["_ctasks"].append({
                "number": it["ctask"],
                "short_description": it["ctask_short"],
                "type": it["ctask_type"],
                "state": it["ctask_state"],
                "assigned_to": it["assigned_to"],
                "ms_names": it.get("ms_names") or [],
                "mf_names": it.get("mf_names") or [],
            })
            if it["service"]:
                by_chg[chg]["_services"].add(it["service"])
            for n in it.get("ms_names") or []:
                if n not in by_chg[chg]["_ms"]:
                    by_chg[chg]["_ms"].append(n)
            for n in it.get("mf_names") or []:
                if n not in by_chg[chg]["_mf"]:
                    by_chg[chg]["_mf"].append(n)
            if it.get("lt_hours"):
                by_chg[chg]["_lt_hours"].append(it["lt_hours"])
            if it["start"] and (
                not by_chg[chg]["start_date"]
                or it["start"] < by_chg[chg]["start_date"][:10]
            ):
                by_chg[chg]["start_date"] = it["start"]
            if it.get("planned_end") and not by_chg[chg]["end_date"]:
                by_chg[chg]["end_date"] = it["planned_end"]

        records = []
        for rec in by_chg.values():
            services = sorted(rec.pop("_services"))
            ctasks = rec.pop("_ctasks")
            ms = rec.pop("_ms")
            mf = rec.pop("_mf")
            lts = rec.pop("_lt_hours")
            rec["u_microservices"] = ",".join(ms)
            rec["u_microfrontends"] = ",".join(mf)
            rec["u_ms_count"] = str(len(ms))
            rec["u_mf_count"] = str(len(mf))
            if services:
                rec["u_project"] = services[0]
                rec["cmdb_ci"] = services[0]
            elif ms:
                rec["u_project"] = ms[0]
            rec["_ctask_list"] = ctasks
            rec["_service_list"] = services or ms
            if lts:
                rec["_lt_hours"] = round(sum(lts) / len(lts), 2)
            records.append(rec)
        return records

    def _resolve_cluster(self, record: Dict) -> str:
        cl = (record.get("u_cluster", "") or "").strip().lower()
        if cl:
            if cl in self.FULL_CLUSTER_MAP:
                return self.FULL_CLUSTER_MAP[cl]
            for k, v in self.FULL_CLUSTER_MAP.items():
                if cl in k or k in cl:
                    return v

        project = (
            record.get("u_project", "")
            or record.get("cmdb_ci", "")
            or record.get("short_description", "")
            or ""
        ).lower()
        for keyword, code in self.CLUSTER_MAP.items():
            if keyword in project:
                return code

        ag = (record.get("assignment_group", "") or "").lower()
        for keyword, code in self.CLUSTER_MAP.items():
            if keyword in ag:
                return code
        return "int"

    @staticmethod
    def _parse_list(rec: Dict, field: str) -> List[str]:
        val = rec.get(field, "") or ""
        if isinstance(val, list):
            return [str(s).strip() for s in val if str(s).strip()]
        return [s.strip() for s in str(val).split(",") if s.strip()]

    @staticmethod
    def _parse_count(rec: Dict, field: str) -> int:
        try:
            return int(rec.get(field, 0) or 0)
        except (ValueError, TypeError):
            return 0

    def transform_to_dashboard_format(self, records: List[Dict]) -> Dict[str, Any]:
        cmr_data: List[Dict] = []
        cmr_extra: List[Dict] = []
        inc_list: List[Dict] = []
        new_ms_registry: List[Dict] = []
        sno = 0

        for rec in records:
            chg = rec.get("number", "")
            start = rec.get("start_date", "")
            if not start:
                continue
            try:
                date_str = start[:10]
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            project = (
                rec.get("u_project", "")
                or rec.get("cmdb_ci", "")
                or rec.get("short_description", "")
                or "Unknown"
            ).strip()
            cluster = self._resolve_cluster(rec)

            has_incident = (
                str(rec.get("u_incident_flag", "")).lower() in ("true", "yes", "1")
                or str(rec.get("close_code", "")).lower() in ("unsuccessful", "unsuccessful with issues")
            )
            has_rollback = str(rec.get("u_rollback", "")).lower() in ("true", "yes", "1")
            mttr = 0.0
            try:
                mttr = float(rec.get("u_mttr_hours", 0) or 0)
            except (ValueError, TypeError):
                pass

            ms_names = self._parse_list(rec, "u_microservices")
            mf_names = self._parse_list(rec, "u_microfrontends")
            nms_names = self._parse_list(rec, "u_new_microservices")
            nmf_names = self._parse_list(rec, "u_new_microfrontends")
            ctasks = rec.get("_ctask_list") or []

            ms = len(ms_names) if ms_names else self._parse_count(rec, "u_ms_count")
            mf = len(mf_names) if mf_names else self._parse_count(rec, "u_mf_count")
            nms = len(nms_names) if nms_names else self._parse_count(rec, "u_nms_count")
            nmf = len(nmf_names) if nmf_names else self._parse_count(rec, "u_nmf_count")

            sno += 1
            cmr_data.append({
                "d": date_str, "c": cluster, "p": project,
                "i": has_incident, "r": has_rollback, "m": mttr,
            })

            extra: Dict[str, Any] = {
                "chg": chg, "ms": ms, "mf": mf, "nms": nms, "nmf": nmf,
                "ctasks": [t.get("number") for t in ctasks if t.get("number")],
                "ctask_details": ctasks,
                "services": rec.get("_service_list") or ms_names,
            }
            if rec.get("_lt_hours"):
                extra["lt_hours"] = rec["_lt_hours"]
            else:
                end = rec.get("end_date") or ""
                if start and end:
                    try:
                        sdt = datetime.strptime(str(start)[:16], "%Y-%m-%d %H:%M")
                        edt = datetime.strptime(str(end)[:16], "%Y-%m-%d %H:%M")
                        hours = (edt - sdt).total_seconds() / 3600.0
                        if hours > 0:
                            extra["lt_hours"] = round(hours, 2)
                    except ValueError:
                        pass
            if ms_names:
                extra["msn"] = ms_names
            if mf_names:
                extra["mfn"] = mf_names
            # Prefer CI/service name list separately from MS/MF names
            svc = rec.get("_service_list") or []
            if svc:
                extra["services"] = list(svc)
            elif ms_names:
                extra["services"] = ms_names
            cmr_extra.append(extra)

            if has_incident:
                try:
                    dt_disp = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d %b %Y")
                except ValueError:
                    dt_disp = date_str
                inc_list.append({
                    "p": project,
                    "t": "inc" if has_rollback else "ctask",
                    "ch": chg, "dt": dt_disp,
                    "is": (rec.get("u_incident_description", "") or
                           f"Issue during {project} deployment"),
                    "sv": "e" if has_rollback else "w",
                    "rb": has_rollback,
                })

            if nms_names or nmf_names:
                entry: Dict[str, Any] = {
                    "sno": sno, "p": project, "d": date_str,
                    "chg": chg, "cl": cluster,
                }
                if nms_names:
                    entry["ms"] = nms_names
                if nmf_names:
                    entry["mf"] = nmf_names
                new_ms_registry.append(entry)

        prj_map: Dict[str, Dict[str, Any]] = {}
        for r in cmr_data:
            p = r["p"]
            if p not in prj_map:
                prj_map[p] = {"n": p, "c": 0, "i": False}
            prj_map[p]["c"] += 1
            if r["i"]:
                prj_map[p]["i"] = True
        projects = sorted(prj_map.values(), key=lambda x: x["c"], reverse=True)

        bundle = self._last_ocp_bundle or {}
        return {
            "cmr_data": cmr_data,
            "cmr_extra": cmr_extra,
            "incidents": inc_list,
            "new_ms_registry": new_ms_registry,
            "projects": projects,
            "total": len(cmr_data),
            "ctask_count": bundle.get("ctask_count", 0),
            "items": bundle.get("items", []),
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "permission_hint": bundle.get("permission_hint"),
        }

    def is_configured(self) -> bool:
        return all([
            self.instance, self.client_id, self.client_secret,
            self.username, self.password
        ])

    def test_connection(self) -> Dict[str, Any]:
        if not self.is_configured():
            missing = [k for k, v in {
                "SNOW_INSTANCE": self.instance,
                "SNOW_CLIENT_ID": self.client_id,
                "SNOW_CLIENT_SECRET": self.client_secret,
                "SNOW_USERNAME": self.username,
                "SNOW_PASSWORD": self.password,
            }.items() if not v]
            return {"ok": False, "error": f"Missing env vars: {missing}"}
        try:
            self._authenticate()
            return {"ok": True, "instance": self.instance}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def test_tables(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for table in ["change_request", "change_task", "incident", "sys_user", "sys_user_group"]:
            try:
                records = self._table_get(table, {
                    "sysparm_limit": "1",
                    "sysparm_query": "ORDERBYDESCsys_created_on",
                    "sysparm_display_value": "true",
                })
                results[table] = {
                    "count": len(records),
                    "has_data": len(records) > 0,
                    "sample_keys": sorted(records[0].keys())[:30] if records else [],
                }
            except Exception as e:
                results[table] = {"error": str(e), "has_data": False}
        try:
            g = self._table_get("sys_user_group", {
                "sysparm_query": "name=DIG-SOCE-SRE-OCP",
                "sysparm_fields": "sys_id,name",
                "sysparm_limit": "1",
            })
            results["group_DIG-SOCE-SRE-OCP"] = g[0] if g else {"found": False}
        except Exception as e:
            results["group_DIG-SOCE-SRE-OCP"] = {"error": str(e)}
        return results

    def discover_fields(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {}

        try:
            ctasks = self._table_get("change_task", {
                "sysparm_limit": "1",
                "sysparm_query": "assignment_group.name=DIG-SOCE-SRE-OCP^ORDERBYDESCsys_created_on",
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            })
            results["change_task"] = {
                "count": len(ctasks),
                "sample": ctasks[0] if ctasks else None,
                "fields": sorted(ctasks[0].keys()) if ctasks else [],
            }
        except Exception as e:
            results["change_task"] = {"error": str(e), "count": 0}

        try:
            chgs = self._table_get("change_request", {
                "sysparm_limit": "1",
                "sysparm_query": "ORDERBYDESCsys_created_on",
                "sysparm_display_value": "true",
            })
            results["change_request"] = {
                "count": len(chgs),
                "sample": chgs[0] if chgs else None,
                "fields": sorted(chgs[0].keys()) if chgs else [],
            }
        except Exception as e:
            results["change_request"] = {"error": str(e), "count": 0}

        try:
            chgs_grp = self._table_get("change_request", {
                "sysparm_limit": "1",
                "sysparm_query": "assignment_group.name=DIG-SOCE-SRE-OCP^ORDERBYDESCsys_created_on",
                "sysparm_display_value": "true",
            })
            results["change_request_by_group"] = {
                "count": len(chgs_grp),
                "sample": chgs_grp[0] if chgs_grp else None,
                "note": "Usually empty — your group is on CTASK, not parent CHG",
            }
        except Exception as e:
            results["change_request_by_group"] = {"error": str(e)}

        for label, table, q in [
            ("known_ctask_CTASK0058771", "change_task", "number=CTASK0058771"),
            ("known_chg_CHG0056296", "change_request", "number=CHG0056296"),
        ]:
            try:
                rows = self._table_get(table, {
                    "sysparm_limit": "1",
                    "sysparm_query": q,
                    "sysparm_display_value": "true",
                    "sysparm_exclude_reference_link": "true",
                })
                results[label] = {"count": len(rows), "sample": rows[0] if rows else None}
            except Exception as e:
                results[label] = {"error": str(e)}

        return results
