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
from concurrent.futures import ThreadPoolExecutor
import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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
        self._lock = threading.RLock()
        self._session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=12, pool_maxsize=12)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

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
        with self._lock:
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
            resp = self._session.post(url, data=payload, timeout=20, verify=True)
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
                   page_size: int = 1000, max_records: Optional[int] = None) -> List[Dict]:
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
        headers = self._headers()

        while True:
            page = dict(base_params)
            page["sysparm_offset"] = str(offset)
            page["sysparm_limit"] = str(limit)
            resp = self._session.get(
                url, headers=headers, params=page,
                timeout=45, verify=True
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
        d = self._normalize_date(date_str)
        if not d:
            return False
        return from_date <= d <= to_date

    @staticmethod
    def _normalize_date(date_str: Any) -> str:
        """Normalize ServiceNow display/value dates to YYYY-MM-DD."""
        if not date_str:
            return ""
        if isinstance(date_str, dict):
            date_str = date_str.get("display_value") or date_str.get("value") or ""
        s = str(date_str).strip()
        if not s:
            return ""
        # Already ISO
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        # DD/MM/YYYY or DD-MM-YYYY
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
        if m:
            dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return datetime(yy, mm, dd).strftime("%Y-%m-%d")
            except ValueError:
                return ""
        # "30 Jul 2026" / "Jul 30, 2026"
        s2 = s.replace(",", " ")
        m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s2)
        if m:
            months = {
                "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
            }
            mi = months.get(m.group(2)[:3].lower())
            if mi:
                try:
                    return datetime(int(m.group(3)), mi, int(m.group(1))).strftime("%Y-%m-%d")
                except ValueError:
                    return ""
        m = re.match(r"^([A-Za-z]+)\s+(\d{1,2})\s+(\d{4})", s2)
        if m:
            months = {
                "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
            }
            mi = months.get(m.group(1)[:3].lower())
            if mi:
                try:
                    return datetime(int(m.group(3)), mi, int(m.group(2))).strftime("%Y-%m-%d")
                except ValueError:
                    return ""
        return ""

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
            "change_task_type", "state", "close_code", "close_notes",
            "assignment_group", "assigned_to",
            "change_request", "parent", "cmdb_ci",
            "planned_start_date", "planned_end_date",
            "work_start", "work_end", "closed_at", "sys_created_on",
        ]
        gid = self._group_sys_id(assignment_group)
        # Do NOT put planned_start_date in the encoded query — that field ACL caused 403.
        try:
            rows = self._table_get("change_task", {
                "sysparm_query": f"assignment_group={gid}^ORDERBYDESCsys_created_on",
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            })
        except requests.HTTPError as e:
            # Some instances ACL close_code / close_notes on change_task — retry without them
            logger.warning("CTASK fetch with close_code failed (%s) — retrying without close fields", e)
            fields_safe = [f for f in fields if f not in ("close_code", "close_notes")]
            rows = self._table_get("change_task", {
                "sysparm_query": f"assignment_group={gid}^ORDERBYDESCsys_created_on",
                "sysparm_fields": ",".join(fields_safe),
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
            closed = self._dv(t.get("closed_at")) or self._dv(t.get("work_end")) or ""
            # Keep if planned/start OR closed/activity falls in range (Metric 3 needs recent closes)
            if self._in_date_range(start, from_date, to_date) or self._in_date_range(closed, from_date, to_date):
                t["_start_raw"] = start
                t["_closed_raw"] = closed
                filtered.append(t)
        return filtered

    def fetch_ctask_detail(self, ctask_number: str) -> Dict[str, Any]:
        """Fetch one CTASK from ServiceNow with full description + common fields."""
        num = (ctask_number or "").strip()
        if not num:
            return {"error": "CTASK number required"}
        fields = [
            "number", "short_description", "description",
            "change_task_type", "type", "state", "priority",
            "close_code", "close_notes",
            "assignment_group", "assigned_to",
            "change_request", "parent", "cmdb_ci",
            "planned_start_date", "planned_end_date",
            "work_start", "work_end", "closed_at",
            "sys_created_on", "sys_updated_on",
            "expected_start", "due_date",
        ]
        try:
            rows = self._table_get("change_task", {
                "sysparm_query": f"number={num}",
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": "1",
            }, page_size=1, max_records=1)
        except requests.HTTPError as e:
            # Retry without fields that sometimes ACL-block
            logger.warning("CTASK detail full fields failed (%s) — retrying reduced set", e)
            fields_safe = [f for f in fields if f not in ("close_code", "close_notes", "priority")]
            rows = self._table_get("change_task", {
                "sysparm_query": f"number={num}",
                "sysparm_fields": ",".join(fields_safe),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": "1",
            }, page_size=1, max_records=1)

        if not rows:
            return {"error": f"CTASK {num} not found in ServiceNow", "number": num}

        t = rows[0]
        chg_num = self._dv(t.get("change_request")) or self._dv(t.get("parent"))
        if chg_num and not str(chg_num).startswith("CHG"):
            chg_num = ""
        chg = {}
        if chg_num:
            chg = self.fetch_change_requests_by_numbers([chg_num]).get(chg_num, {})

        desc = self._dv(t.get("description"))
        ms_names, mf_names = self.parse_ms_mf_from_description(desc)
        service = (
            self._dv(t.get("cmdb_ci"))
            or self._dv(chg.get("cmdb_ci"))
            or ""
        )
        return {
            "number": self._dv(t.get("number")) or num,
            "ctask_name": self._dv(t.get("short_description")),
            "short_description": self._dv(t.get("short_description")),
            "description": desc,
            "type": self._dv(t.get("change_task_type")) or self._dv(t.get("type")),
            "state": self._dv(t.get("state")),
            "priority": self._dv(t.get("priority")),
            "close_code": self._dv(t.get("close_code")),
            "close_notes": self._dv(t.get("close_notes")),
            "assignment_group": self._dv(t.get("assignment_group")),
            "assigned_to": self._dv(t.get("assigned_to")),
            "configuration_item": service,
            "chg": chg_num,
            "chg_short": self._dv(chg.get("short_description")),
            "chg_state": self._dv(chg.get("state")),
            "chg_close_code": self._dv(chg.get("close_code")),
            "chg_assignment_group": self._dv(chg.get("assignment_group")),
            "planned_start": self._dv(t.get("planned_start_date")),
            "planned_end": self._dv(t.get("planned_end_date")),
            "work_start": self._dv(t.get("work_start")),
            "work_end": self._dv(t.get("work_end")),
            "expected_start": self._dv(t.get("expected_start")),
            "due_date": self._dv(t.get("due_date")),
            "closed_at": self._dv(t.get("closed_at")),
            "created_on": self._dv(t.get("sys_created_on")),
            "updated_on": self._dv(t.get("sys_updated_on")),
            "ms_names": ms_names,
            "mf_names": mf_names,
            "source": "servicenow_live",
        }

    def fetch_change_requests_by_numbers(self, numbers: List[str]) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        nums = [n for n in numbers if n and str(n).startswith("CHG")]
        if not nums:
            return out
        fields = [
            "number", "short_description",
            "start_date", "end_date", "state", "close_code",
            "assignment_group", "assigned_to", "cmdb_ci",
            "category", "type", "risk",
        ]
        batches = [nums[i:i + 80] for i in range(0, len(nums), 80)]

        def fetch_batch(batch: List[str]) -> List[Dict]:
            try:
                return self._table_get("change_request", {
                    "sysparm_query": "numberIN" + ",".join(batch),
                    "sysparm_fields": ",".join(fields),
                    "sysparm_display_value": "true",
                    "sysparm_exclude_reference_link": "true",
                })
            except requests.HTTPError as e:
                logger.warning("CHG batch fetch failed: %s", e)
                return []
            except Exception as e:
                logger.warning("CHG batch fetch error: %s", e)
                return []

        workers = min(6, max(1, len(batches)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for rows in ex.map(fetch_batch, batches):
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
            # Keep payload light — full text rarely needed beyond MS/MF parse + short preview
            if len(desc) > 800:
                desc = desc[:800] + "…"

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

            ctask_cc = self._dv(t.get("close_code"))
            items.append({
                "ctask": self._dv(t.get("number")),
                "ctask_short": self._dv(t.get("short_description")),
                "ctask_type": self._dv(t.get("change_task_type")) or self._dv(t.get("type")),
                "ctask_state": self._dv(t.get("state")),
                "ctask_close_code": ctask_cc,
                "ctask_close_notes": self._dv(t.get("close_notes")),
                "assigned_to": self._dv(t.get("assigned_to")),
                "assignment_group": self._dv(t.get("assignment_group")) or assignment_group,
                "planned_start": self._dv(t.get("planned_start_date")),
                "planned_end": self._dv(t.get("planned_end_date")),
                "closed_at": self._dv(t.get("closed_at")),
                "description": desc,
                "chg": chg_num,
                "chg_short": self._dv(chg.get("short_description")),
                "chg_state": self._dv(chg.get("state")),
                "chg_close_code": self._dv(chg.get("close_code")),
                "chg_assignment_group": self._dv(chg.get("assignment_group")),
                "service": service,
                "start": self._normalize_date(start) or (start[:10] if start else ""),
                "closed": self._normalize_date(t.get("_closed_raw") or t.get("closed_at") or ""),
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
                    "close_code": it.get("chg_close_code") or "",
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
                    "_ctask_close_codes": [],
                    "_closed_dates": [],
                }
            by_chg[chg]["_ctasks"].append({
                "number": it["ctask"],
                "short_description": it["ctask_short"],
                "type": it["ctask_type"],
                "state": it["ctask_state"],
                "close_code": it.get("ctask_close_code") or "",
                "close_notes": it.get("ctask_close_notes") or "",
                "assigned_to": it["assigned_to"],
                "closed_at": it.get("closed_at") or it.get("closed") or "",
                "ms_names": it.get("ms_names") or [],
                "mf_names": it.get("mf_names") or [],
            })
            if it.get("closed") or it.get("closed_at"):
                cd = self._normalize_date(it.get("closed") or it.get("closed_at"))
                if cd:
                    by_chg[chg]["_closed_dates"].append(cd)
            if it.get("ctask_close_code"):
                by_chg[chg]["_ctask_close_codes"].append(it["ctask_close_code"])
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
            # Prefer worst close code across CHG + all CTASKs (Unsuccessful > with issues > Successful)
            by_chg[chg]["close_code"] = self._worst_close_code(
                by_chg[chg].get("close_code"),
                it.get("chg_close_code"),
                it.get("ctask_close_code"),
            )
            start_raw = it.get("start") or ""
            start_n = self._normalize_date(start_raw) or (start_raw[:10] if start_raw else "")
            if start_n and (
                not by_chg[chg]["start_date"]
                or start_n < self._normalize_date(by_chg[chg]["start_date"]) or not self._normalize_date(by_chg[chg]["start_date"])
            ):
                if not by_chg[chg]["start_date"] or start_n < (self._normalize_date(by_chg[chg]["start_date"]) or "9999-99-99"):
                    by_chg[chg]["start_date"] = start_n
            if it.get("planned_end") and (
                not by_chg[chg]["end_date"]
                or str(it["planned_end"]) > str(by_chg[chg]["end_date"])
            ):
                by_chg[chg]["end_date"] = it["planned_end"]
            if it.get("planned_start") and not by_chg[chg].get("work_start"):
                by_chg[chg]["work_start"] = it["planned_start"]
            if it.get("planned_end"):
                by_chg[chg]["work_end"] = it["planned_end"]
            # closed_at on failing CTASK can fill end window for MTTR
            if it.get("closed_at") and not by_chg[chg].get("work_end"):
                by_chg[chg]["work_end"] = it["closed_at"]

        records = []
        for rec in by_chg.values():
            services = sorted(rec.pop("_services"))
            ctasks = rec.pop("_ctasks")
            ms = rec.pop("_ms")
            mf = rec.pop("_mf")
            lts = rec.pop("_lt_hours")
            rec.pop("_ctask_close_codes", None)
            closed_list = rec.pop("_closed_dates", []) or []
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
            # Only real CTASK closed_at — never planned_end (that caused future dates)
            candidates = [c for c in closed_list if c]
            rec["_closed_date"] = max(candidates) if candidates else ""
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

    @staticmethod
    def _normalize_close_code(close_code: str) -> str:
        c = (close_code or "").strip().lower().replace("_", " ").replace("-", " ")
        c = re.sub(r"\s+", " ", c)
        if c in ("unsuccessful", "failed", "failure"):
            return "unsuccessful"
        if c in (
            "successful with issues", "successful issues",
            "successful with issue", "success with issues",
        ):
            return "successful with issues"
        if c in ("successful", "success", "successful without issues"):
            return "successful"
        return c

    @staticmethod
    def _is_cloud_or_network_group(assignment_group: str) -> bool:
        """
        Exclude Cloud Ops, Network, and any cloud-related DIG groups
        from Overall IndiGo Metric 3. Keep apps / Navitaire / OCP / etc.
        """
        ag = (assignment_group or "").strip().upper()
        if not ag:
            return False
        if "CLOUD" in ag:
            return True
        if "NETWORK" in ag:
            return True
        # IndiGo network teams: …-NET-…, …-NETENGG-…
        if re.search(r"(^|[-_])NET(ENGG)?([-_]|$)", ag):
            return True
        return False

    @classmethod
    def _close_code_marks(cls, close_code: str) -> Tuple[bool, bool, str]:
        """
        ServiceNow close_code has 3 marks:
          Successful | Successful with issues | Unsuccessful
        Metric 3 failures = Unsuccessful OR Successful with issues.
        Rollback assumed for Unsuccessful.
        Returns (is_failure, is_rollback, kind) where kind is
        unsuccessful | with_issues | successful | other
        """
        c = cls._normalize_close_code(close_code)
        if c == "unsuccessful":
            return True, True, "unsuccessful"
        if c == "successful with issues":
            return True, False, "with_issues"
        if c == "successful":
            return False, False, "successful"
        return False, False, "other"

    @classmethod
    def _worst_close_code(cls, *codes: Any) -> str:
        """Pick worst among close codes: Unsuccessful > with issues > Successful > other."""
        rank = {"unsuccessful": 3, "successful with issues": 2, "successful": 1}
        best_rank = -1
        best_raw = ""
        for raw in codes:
            if not raw:
                continue
            s = str(raw).strip()
            if not s:
                continue
            n = cls._normalize_close_code(s)
            r = rank.get(n, 0)
            if r > best_rank:
                best_rank = r
                # Canonical display labels
                if n == "unsuccessful":
                    best_raw = "Unsuccessful"
                elif n == "successful with issues":
                    best_raw = "Successful with issues"
                elif n == "successful":
                    best_raw = "Successful"
                else:
                    best_raw = s
        return best_raw

    @staticmethod
    def _hours_between(start: Any, end: Any) -> float:
        if not start or not end:
            return 0.0
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                sdt = datetime.strptime(str(start)[:19], fmt)
                edt = datetime.strptime(str(end)[:19], fmt)
                hours = (edt - sdt).total_seconds() / 3600.0
                return round(hours, 2) if hours > 0 else 0.0
            except ValueError:
                continue
        return 0.0

    def transform_to_dashboard_format(self, records: List[Dict]) -> Dict[str, Any]:
        cmr_data: List[Dict] = []
        cmr_extra: List[Dict] = []
        inc_list: List[Dict] = []
        new_ms_registry: List[Dict] = []
        sno = 0
        close_code_stats = {
            "Successful": 0,
            "Successful with issues": 0,
            "Unsuccessful": 0,
            "Other/Empty": 0,
        }
        failure_stats = {
            "total_failures": 0,
            "unsuccessful": 0,
            "with_issues": 0,
            "rollback": 0,
            "ctask_failures": 0,
            "cmr_failures": 0,
        }

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

            close_code = self._dv(rec.get("close_code"))
            cc_fail, cc_rollback, cc_kind = self._close_code_marks(close_code)
            if cc_kind == "successful":
                close_code_stats["Successful"] += 1
            elif cc_kind == "with_issues":
                close_code_stats["Successful with issues"] += 1
            elif cc_kind == "unsuccessful":
                close_code_stats["Unsuccessful"] += 1
            else:
                close_code_stats["Other/Empty"] += 1

            ctasks = rec.get("_ctask_list") or []
            # Per-CTASK failure marks (assignee closed CTASK as unsuccessful / with issues)
            ctask_fail_events: List[Dict[str, Any]] = []
            any_ctask_fail = False
            any_ctask_rollback = False
            for t in ctasks:
                t_cc = self._dv(t.get("close_code"))
                t_fail, t_rb, t_kind = self._close_code_marks(t_cc)
                if not t_fail:
                    continue
                any_ctask_fail = True
                if t_rb:
                    any_ctask_rollback = True
                ctask_fail_events.append({
                    "ctask": t.get("number") or "",
                    "close_code": t_cc,
                    "kind": t_kind,
                    "rollback": t_rb,
                    "assigned_to": t.get("assigned_to") or "",
                    "short_description": t.get("short_description") or "",
                    "close_notes": t.get("close_notes") or "",
                    "state": t.get("state") or "",
                    "closed_at": t.get("closed_at") or "",
                })

            has_incident = (
                cc_fail
                or any_ctask_fail
                or str(rec.get("u_incident_flag", "")).lower() in ("true", "yes", "1")
            )
            has_rollback = (
                cc_rollback
                or any_ctask_rollback
                or str(rec.get("u_rollback", "")).lower() in ("true", "yes", "1")
            )

            end = rec.get("end_date") or ""
            work_start = rec.get("work_start") or start
            work_end = rec.get("work_end") or end
            window_h = self._hours_between(work_start, work_end) or self._hours_between(start, end)
            mttr = 0.0
            if has_incident:
                try:
                    custom = float(rec.get("u_mttr_hours", 0) or 0)
                except (ValueError, TypeError):
                    custom = 0.0
                mttr = custom if custom > 0 else window_h

            ms_names = self._parse_list(rec, "u_microservices")
            mf_names = self._parse_list(rec, "u_microfrontends")
            nms_names = self._parse_list(rec, "u_new_microservices")
            nmf_names = self._parse_list(rec, "u_new_microfrontends")

            ms = len(ms_names) if ms_names else self._parse_count(rec, "u_ms_count")
            mf = len(mf_names) if mf_names else self._parse_count(rec, "u_mf_count")
            nms = len(nms_names) if nms_names else self._parse_count(rec, "u_nms_count")
            nmf = len(nmf_names) if nmf_names else self._parse_count(rec, "u_nmf_count")

            sno += 1
            fail_kind = "none"
            if has_rollback or cc_kind == "unsuccessful" or any_ctask_rollback:
                fail_kind = "unsuccessful"
            elif has_incident:
                fail_kind = "with_issues"

            start_n = self._normalize_date(date_str) or date_str
            closed_n = self._normalize_date(rec.get("_closed_date") or "")
            # Activity date for time filters: prefer close for failures, else latest known
            if has_incident and closed_n:
                activity_n = closed_n
            else:
                activity_n = max([x for x in [closed_n, start_n] if x] or [start_n])
            # Clamp future activity (bad planned_end leakage) to closed or start
            today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            if activity_n > today:
                activity_n = closed_n if closed_n and closed_n <= today else start_n
            if closed_n > today:
                closed_n = ""

            cmr_data.append({
                "d": start_n,          # planned/start (deploy)
                "cd": closed_n,        # close/activity
                "ad": activity_n,      # used for Last N days filter (Overall IndiGo)
                "c": cluster, "p": project,
                "i": has_incident, "r": has_rollback, "m": mttr,
                "cc": close_code,
                "fk": fail_kind,
                "scope": "ocp",
            })

            extra: Dict[str, Any] = {
                "chg": chg, "ms": ms, "mf": mf, "nms": nms, "nmf": nmf,
                "ctasks": [t.get("number") for t in ctasks if t.get("number")],
                "ctask_details": ctasks,
                "ctask_failures": ctask_fail_events,
                "services": rec.get("_service_list") or ms_names,
                "close_code": close_code,
                "fail_kind": fail_kind,
            }
            if rec.get("_lt_hours"):
                extra["lt_hours"] = rec["_lt_hours"]
            elif window_h > 0:
                extra["lt_hours"] = window_h
            if ms_names:
                extra["msn"] = ms_names
            if mf_names:
                extra["mfn"] = mf_names
            svc = rec.get("_service_list") or []
            if svc:
                extra["services"] = list(svc)
            elif ms_names:
                extra["services"] = ms_names
            cmr_extra.append(extra)

            if has_incident:
                failure_stats["total_failures"] += 1
                if fail_kind == "unsuccessful":
                    failure_stats["unsuccessful"] += 1
                elif fail_kind == "with_issues":
                    failure_stats["with_issues"] += 1
                if has_rollback:
                    failure_stats["rollback"] += 1
                if cc_fail:
                    failure_stats["cmr_failures"] += 1
                if any_ctask_fail:
                    failure_stats["ctask_failures"] += 1

                try:
                    # Prefer close date on failure cards so Last 30 days catches them
                    show_d = closed_n or start_n or date_str
                    dt_disp = datetime.strptime(show_d, "%Y-%m-%d").strftime("%d %b %Y")
                except ValueError:
                    dt_disp = date_str

                # One row per failing CTASK (assignee closed as unsuccessful / with issues)
                if ctask_fail_events:
                    for ev in ctask_fail_events:
                        who = ev["assigned_to"] or "unassigned"
                        notes = (ev.get("close_notes") or "").strip()
                        issue_txt = (
                            f"CTASK {ev['ctask']} closed by {who} as {ev['close_code'] or ev['kind']}"
                            + (f" — {ev['short_description']}" if ev.get("short_description") else "")
                            + (f" · {notes[:160]}" if notes else "")
                        )
                        inc_list.append({
                            "p": project,
                            "t": "ctask",
                            "ch": chg or ev["ctask"],
                            "ctask": ev["ctask"],
                            "dt": dt_disp,
                            "is": issue_txt,
                            "sv": "e" if ev["rollback"] else "w",
                            "rb": bool(ev["rollback"]),
                            "close_code": ev["close_code"],
                            "fail_kind": ev["kind"],
                            "assigned_to": ev["assigned_to"],
                            "source": "servicenow_ctask_close_code",
                            "mttr_hours": mttr,
                        })
                # CHG-level close_code failure (even if no CTASK close_code reported)
                if cc_fail:
                    issue_txt = (
                        rec.get("u_incident_description", "")
                        or f"CMR {chg} closed as {close_code} — {project}"
                    )
                    # Avoid duplicate if only one CTASK already mirrors same CHG code and no other events
                    already = any(
                        x.get("source") == "servicenow_ctask_close_code"
                        and x.get("ch") == chg
                        and self._normalize_close_code(x.get("close_code") or "") == self._normalize_close_code(close_code)
                        for x in inc_list
                    )
                    if not already or len(ctask_fail_events) == 0:
                        inc_list.append({
                            "p": project,
                            "t": "inc" if has_rollback else "cmr",
                            "ch": chg,
                            "ctask": "",
                            "dt": dt_disp,
                            "is": issue_txt,
                            "sv": "e" if has_rollback else "w",
                            "rb": has_rollback,
                            "close_code": close_code,
                            "fail_kind": cc_kind,
                            "assigned_to": "",
                            "source": "servicenow_cmr_close_code",
                            "mttr_hours": mttr,
                        })
                elif has_incident and not ctask_fail_events:
                    # Flagged by custom fields without close_code
                    inc_list.append({
                        "p": project,
                        "t": "inc" if has_rollback else "cmr",
                        "ch": chg,
                        "ctask": "",
                        "dt": dt_disp,
                        "is": rec.get("u_incident_description") or f"Issue flagged on {chg} — {project}",
                        "sv": "e" if has_rollback else "w",
                        "rb": has_rollback,
                        "close_code": close_code or "",
                        "fail_kind": fail_kind,
                        "assigned_to": "",
                        "source": "servicenow_flag",
                        "mttr_hours": mttr,
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
            "close_code_stats": close_code_stats,
            "failure_stats": failure_stats,
            "data_source": "servicenow_live",
            "metric3_rule": (
                "Failure = CTASK or CMR close_code in "
                "{Unsuccessful, Successful with issues}; "
                "Rollback = Unsuccessful"
            ),
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "permission_hint": bundle.get("permission_hint"),
        }

    def fetch_indigo_close_failures(
        self,
        from_date: str = "2025-01-01",
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Overall IndiGo Metric 3: DIG-* CHGs closed Unsuccessful / Successful with issues.
        Excludes Cloud Ops, Network, and any cloud-related assignment groups.
        """
        if to_date is None:
            to_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        fields = [
            "number", "short_description", "close_code", "closed_at",
            "assignment_group", "assigned_to", "start_date", "end_date",
            "state", "cmdb_ci", "opened_at",
        ]
        try:
            rows = self._table_get("change_request", {
                "sysparm_query": (
                    "close_codeINunsuccessful,successful with issues"
                    "^ORDERBYDESCclosed_at"
                ),
                "sysparm_fields": ",".join(fields),
                "sysparm_display_value": "true",
                "sysparm_exclude_reference_link": "true",
            }, page_size=500, max_records=2000)
            query_error = None
        except Exception as e:
            rows = []
            query_error = str(e)
            logger.exception("fetch_indigo_close_failures failed")

        items = []
        excluded = 0
        for r in rows:
            ag = self._dv(r.get("assignment_group"))
            # Overall IndiGo = DIG-* digital groups (not Cloud / Network)
            if ag and not ag.upper().startswith("DIG"):
                continue
            if self._is_cloud_or_network_group(ag or ""):
                excluded += 1
                continue
            closed = self._normalize_date(self._dv(r.get("closed_at")))
            start = self._normalize_date(self._dv(r.get("start_date"))) or closed
            if not (self._in_date_range(closed or "", from_date, to_date)
                    or self._in_date_range(start or "", from_date, to_date)):
                continue
            cc = self._dv(r.get("close_code"))
            fail, rb, kind = self._close_code_marks(cc)
            if not fail:
                continue
            project = (
                self._dv(r.get("cmdb_ci"))
                or self._dv(r.get("short_description"))
                or "Unknown"
            )
            cluster = self._resolve_cluster({
                "u_project": project,
                "cmdb_ci": self._dv(r.get("cmdb_ci")),
                "short_description": self._dv(r.get("short_description")),
                "assignment_group": ag,
            })
            items.append({
                "number": self._dv(r.get("number")),
                "short_description": self._dv(r.get("short_description")),
                "close_code": cc,
                "fail_kind": kind,
                "rollback": rb,
                "closed_at": closed,
                "start_date": start,
                "assignment_group": ag,
                "assigned_to": self._dv(r.get("assigned_to")),
                "project": project,
                "cluster": cluster,
                "state": self._dv(r.get("state")),
            })

        return {
            "items": items,
            "total": len(items),
            "excluded_cloud_network": excluded,
            "from_date": from_date,
            "to_date": to_date,
            "query_error": query_error,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "scope_note": (
                "DIG-* close failures excluding Cloud Ops, Network, "
                "and any assignment group containing CLOUD / NETWORK / NET"
            ),
        }

    def merge_indigo_failures(self, dashboard: Dict[str, Any],
                              failures: Dict[str, Any]) -> Dict[str, Any]:
        """Merge Overall IndiGo DIG close_code failures into Metric 3 dataset."""
        items = failures.get("items") or []
        if not items:
            dashboard["indigo_failures"] = failures
            return dashboard

        cmr = list(dashboard.get("cmr_data") or [])
        extra = list(dashboard.get("cmr_extra") or [])
        incs = list(dashboard.get("incidents") or [])
        seen = {str(e.get("chg") or "").upper() for e in extra if e.get("chg")}
        stats = dict(dashboard.get("failure_stats") or {})
        cc_stats = dict(dashboard.get("close_code_stats") or {})

        added = 0
        for it in items:
            chg = (it.get("number") or "").upper()
            if not chg:
                continue
            kind = it.get("fail_kind") or "unsuccessful"
            cc = it.get("close_code") or ""
            closed = it.get("closed_at") or it.get("start_date") or ""
            start = it.get("start_date") or closed
            try:
                dt_disp = datetime.strptime(closed or start, "%Y-%m-%d").strftime("%d %b %Y")
            except ValueError:
                dt_disp = closed or start

            # Always surface on incident list (even if OCP already has CHG)
            incs.append({
                "p": it.get("project") or "Unknown",
                "t": "inc" if it.get("rollback") else "cmr",
                "ch": chg,
                "ctask": "",
                "dt": dt_disp,
                "is": (
                    f"Overall IndiGo · {it.get('assignment_group') or 'DIG'} · "
                    f"CMR {chg} closed as {cc}"
                    + (f" — {it.get('short_description')}" if it.get("short_description") else "")
                ),
                "sv": "e" if it.get("rollback") else "w",
                "rb": bool(it.get("rollback")),
                "close_code": cc,
                "fail_kind": kind,
                "assigned_to": it.get("assigned_to") or "",
                "assignment_group": it.get("assignment_group") or "",
                "source": "servicenow_indigo_close_code",
                "mttr_hours": 0,
            })

            if chg in seen:
                continue
            seen.add(chg)
            added += 1
            cmr.append({
                "d": start,
                "cd": closed,
                "ad": closed or start,
                "c": it.get("cluster") or "int",
                "p": it.get("project") or "Unknown",
                "i": True,
                "r": bool(it.get("rollback")),
                "m": 0,
                "cc": cc,
                "fk": kind,
                "scope": "indigo",
                "ag": it.get("assignment_group") or "",
            })
            extra.append({
                "chg": chg,
                "ms": 0, "mf": 0, "nms": 0, "nmf": 0,
                "ctasks": [],
                "ctask_details": [],
                "ctask_failures": [],
                "services": [],
                "close_code": cc,
                "fail_kind": kind,
                "assignment_group": it.get("assignment_group") or "",
                "scope": "indigo",
            })
            stats["total_failures"] = stats.get("total_failures", 0) + 1
            stats["cmr_failures"] = stats.get("cmr_failures", 0) + 1
            if kind == "unsuccessful":
                stats["unsuccessful"] = stats.get("unsuccessful", 0) + 1
                cc_stats["Unsuccessful"] = cc_stats.get("Unsuccessful", 0) + 1
            elif kind == "with_issues":
                stats["with_issues"] = stats.get("with_issues", 0) + 1
                cc_stats["Successful with issues"] = cc_stats.get("Successful with issues", 0) + 1
            if it.get("rollback"):
                stats["rollback"] = stats.get("rollback", 0) + 1

        # De-dupe incidents by chg+source+close_code
        uniq = []
        seen_inc = set()
        for inc in incs:
            key = (str(inc.get("ch") or "").upper(), str(inc.get("source") or ""), str(inc.get("ctask") or ""), str(inc.get("close_code") or ""))
            if key in seen_inc:
                continue
            seen_inc.add(key)
            uniq.append(inc)

        dashboard["cmr_data"] = cmr
        dashboard["cmr_extra"] = extra
        dashboard["incidents"] = uniq
        dashboard["failure_stats"] = stats
        dashboard["close_code_stats"] = cc_stats
        dashboard["total"] = len(cmr)
        dashboard["indigo_failures"] = {
            "total": len(items),
            "added_cmrs": added,
            "from_date": failures.get("from_date"),
            "to_date": failures.get("to_date"),
            "query_error": failures.get("query_error"),
        }
        return dashboard

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
