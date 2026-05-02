from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from collections.abc import Callable

from .mock_data import CONTACTS, TEAMS, TEMPLATES
from .models import JsonDict, RequestRecord, ToolResult


class MockWatiClient:
    """A deterministic in-memory WATI client used for local demos."""

    def __init__(self) -> None:
        self.contacts = deepcopy(CONTACTS)
        self.templates = deepcopy(TEMPLATES)
        self.teams = set(TEAMS)

    def search_contacts_by_tag(self, tag: str) -> ToolResult:
        request = RequestRecord("GET", f"/api/v1/getContacts?tag={tag}")
        matches = [contact for contact in self.contacts if tag in contact["tags"]]
        return ToolResult(ok=True, output=deepcopy(matches), requests=[request])

    def search_contacts_by_attribute(self, name: str, value: str) -> ToolResult:
        request = RequestRecord("GET", "/api/v1/getContacts")
        matches = [
            contact
            for contact in self.contacts
            if str(contact.get("customParams", {}).get(name, "")).lower() == value.lower()
        ]
        return ToolResult(ok=True, output=deepcopy(matches), requests=[request])

    def add_contact(
        self,
        whatsapp_number: str,
        name: str,
        custom_params: list[JsonDict] | None = None,
    ) -> ToolResult:
        body = {"name": name, "customParams": custom_params or []}
        request = RequestRecord("POST", f"/api/v1/addContact/{whatsapp_number}", body)
        if self._find_contact(whatsapp_number):
            return ToolResult(False, requests=[request], error="Contact already exists")
        contact = {
            "whatsappNumber": whatsapp_number,
            "name": name,
            "tags": [],
            "customParams": _custom_params_to_dict(custom_params or []),
        }
        self.contacts.append(contact)
        return ToolResult(True, output=deepcopy(contact), requests=[request])

    def update_contact_attributes(
        self, whatsapp_number: str, custom_params: list[JsonDict]
    ) -> ToolResult:
        body = {"customParams": custom_params}
        request = RequestRecord(
            "POST", f"/api/v1/updateContactAttributes/{whatsapp_number}", body
        )
        contact = self._find_contact(whatsapp_number)
        if not contact:
            return ToolResult(False, requests=[request], error="Contact not found")
        contact.setdefault("customParams", {}).update(_custom_params_to_dict(custom_params))
        return ToolResult(True, output=deepcopy(contact), requests=[request])

    def add_tag(self, whatsapp_number: str, tag: str) -> ToolResult:
        request = RequestRecord(
            "POST", f"/api/v1/addTag/{whatsapp_number}", {"tag": tag}
        )
        contact = self._find_contact(whatsapp_number)
        if not contact:
            return ToolResult(False, requests=[request], error="Contact not found")
        if tag not in contact["tags"]:
            contact["tags"].append(tag)
        return ToolResult(True, output=deepcopy(contact), requests=[request])

    def send_template_message(
        self,
        whatsapp_number: str,
        template_name: str,
        parameters: list[JsonDict] | None = None,
        broadcast_name: str | None = None,
    ) -> ToolResult:
        body = {
            "template_name": template_name,
            "broadcast_name": broadcast_name or f"{template_name}_direct",
            "parameters": parameters or [],
        }
        request = RequestRecord(
            "POST", f"/api/v2/sendTemplateMessage/{whatsapp_number}", body
        )
        if template_name not in self.templates:
            return ToolResult(False, requests=[request], error="Template not found")
        if not self._find_contact(whatsapp_number):
            return ToolResult(False, requests=[request], error="Contact not found")
        return ToolResult(True, output={"sent": True, "to": whatsapp_number}, requests=[request])

    def send_broadcast_to_segment(
        self, template_name: str, broadcast_name: str, segment_name: str
    ) -> ToolResult:
        body = {
            "template_name": template_name,
            "broadcast_name": broadcast_name,
            "segmentName": segment_name,
        }
        request = RequestRecord("POST", "/api/v1/sendBroadcastToSegment", body)
        if template_name not in self.templates:
            return ToolResult(False, requests=[request], error="Template not found")
        return ToolResult(
            True,
            output={"broadcast": broadcast_name, "segmentName": segment_name},
            requests=[request],
        )

    def assign_ticket(self, whatsapp_number: str, team_name: str) -> ToolResult:
        body = {"whatsappNumber": whatsapp_number, "teamName": team_name}
        request = RequestRecord("POST", "/api/v1/tickets/assign", body)
        if team_name not in self.teams:
            return ToolResult(False, requests=[request], error="Team not found")
        if not self._find_contact(whatsapp_number):
            return ToolResult(False, requests=[request], error="Contact not found")
        return ToolResult(
            True,
            output={"assigned": True, "teamName": team_name},
            requests=[request],
        )

    def _find_contact(self, whatsapp_number: str) -> JsonDict | None:
        for contact in self.contacts:
            if contact["whatsappNumber"] == whatsapp_number:
                return contact
        return None


class HttpWatiClient:
    """Minimal real WATI HTTP adapter.

    The adapter mirrors MockWatiClient's public methods so the executor can
    swap clients without knowing whether calls are mocked or real.
    """

    def __init__(self, tenant_id: str, token: str) -> None:
        tenant_id = tenant_id.strip().rstrip("/")
        if tenant_id.startswith("http://") or tenant_id.startswith("https://"):
            self.base_url = tenant_id
        else:
            self.base_url = f"https://live-mt-server.wati.io/{tenant_id}"
        token = token.strip()
        self.auth_header = token if token.lower().startswith("bearer ") else f"Bearer {token}"

    def search_contacts_by_tag(self, tag: str) -> ToolResult:
        endpoint = f"/api/v1/getContacts?tag={urllib.parse.quote(tag)}"
        return self._request("GET", endpoint, normalizer=_normalize_contacts)

    def search_contacts_by_attribute(self, name: str, value: str) -> ToolResult:
        result = self._get_all_contacts()
        if not result.ok or not isinstance(result.output, list):
            return result
        matches = [
            contact
            for contact in result.output
            if _custom_param_value(contact, name).lower() == value.lower()
        ]
        return ToolResult(ok=True, output=matches, requests=result.requests)

    def add_contact(
        self,
        whatsapp_number: str,
        name: str,
        custom_params: list[JsonDict] | None = None,
    ) -> ToolResult:
        endpoint = f"/api/v1/addContact/{urllib.parse.quote(whatsapp_number)}"
        body = {"name": name, "customParams": custom_params or []}
        return self._request("POST", endpoint, body)

    def update_contact_attributes(
        self, whatsapp_number: str, custom_params: list[JsonDict]
    ) -> ToolResult:
        endpoint = f"/api/v1/updateContactAttributes/{urllib.parse.quote(whatsapp_number)}"
        return self._request("POST", endpoint, {"customParams": custom_params})

    def add_tag(self, whatsapp_number: str, tag: str) -> ToolResult:
        endpoint = f"/api/v1/addTag/{urllib.parse.quote(whatsapp_number)}"
        return self._request("POST", endpoint, {"tag": tag})

    def send_template_message(
        self,
        whatsapp_number: str,
        template_name: str,
        parameters: list[JsonDict] | None = None,
        broadcast_name: str | None = None,
    ) -> ToolResult:
        endpoint = f"/api/v2/sendTemplateMessage/{urllib.parse.quote(whatsapp_number)}"
        body = {
            "template_name": template_name,
            "broadcast_name": broadcast_name or f"{template_name}_direct",
            "parameters": parameters or [],
        }
        return self._request("POST", endpoint, body)

    def send_broadcast_to_segment(
        self, template_name: str, broadcast_name: str, segment_name: str
    ) -> ToolResult:
        body = {
            "template_name": template_name,
            "broadcast_name": broadcast_name,
            "segmentName": segment_name,
        }
        return self._request("POST", "/api/v1/sendBroadcastToSegment", body)

    def assign_ticket(self, whatsapp_number: str, team_name: str) -> ToolResult:
        body = {"whatsappNumber": whatsapp_number, "teamName": team_name}
        return self._request("POST", "/api/v1/tickets/assign", body)

    def _get_all_contacts(self, page_size: int = 100, max_pages: int = 20) -> ToolResult:
        requests: list[RequestRecord] = []
        contacts: list[JsonDict] = []
        for page_number in range(1, max_pages + 1):
            endpoint = f"/api/v1/getContacts?pageSize={page_size}&pageNumber={page_number}"
            result = self._request("GET", endpoint, normalizer=_normalize_contacts)
            requests.extend(result.requests)
            if not result.ok or not isinstance(result.output, list):
                return ToolResult(False, output=contacts, requests=requests, error=result.error)
            contacts.extend(result.output)
            if len(result.output) < page_size:
                break
        return ToolResult(True, output=contacts, requests=requests)

    def _request(
        self,
        method: str,
        endpoint: str,
        body: JsonDict | None = None,
        normalizer: Callable[[object], object] | None = None,
    ) -> ToolResult:
        request_record = RequestRecord(method, endpoint, body)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            data=data,
            method=method,
            headers={
                "Authorization": self.auth_header,
                "Content-Type": "application/json",
            },
        )
        last_error = ""
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = _decode_response(response.read())
                    output = normalizer(payload) if normalizer else payload
                    return ToolResult(True, output=output, requests=[request_record])
            except urllib.error.HTTPError as exc:
                message = _decode_error(exc)
                last_error = f"HTTP {exc.code}: {message}"
                if exc.code not in {429, 500, 502, 503, 504}:
                    break
            except urllib.error.URLError as exc:
                last_error = str(exc)
            if attempt == 3:
                break
        return ToolResult(False, requests=[request_record], error=last_error)


def _decode_response(raw: bytes) -> object:
    if not raw:
        return {}
    text = raw.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _decode_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = _decode_response(exc.read())
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return exc.reason or "HTTP request failed"


def _normalize_contacts(payload: object) -> list[JsonDict]:
    if isinstance(payload, list):
        return [_normalize_contact(item) for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("contacts", "contact_list", "data", "items", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [_normalize_contact(item) for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _normalize_contacts(value)
            if nested:
                return nested
    return []


def _normalize_contact(contact: JsonDict) -> JsonDict:
    normalized = dict(contact)
    if "whatsappNumber" not in normalized:
        for key in ("phone", "phoneNumber", "waId", "whatsapp_number"):
            if key in normalized:
                normalized["whatsappNumber"] = normalized[key]
                break
    if "customParams" not in normalized:
        normalized["customParams"] = {}
    return normalized


def _custom_param_value(contact: JsonDict, name: str) -> str:
    custom_params = contact.get("customParams", {})
    if isinstance(custom_params, dict):
        return str(custom_params.get(name, ""))
    if isinstance(custom_params, list):
        for item in custom_params:
            if isinstance(item, dict) and item.get("name") == name:
                return str(item.get("value", ""))
    return ""


def _custom_params_to_dict(custom_params: list[JsonDict]) -> JsonDict:
    return {
        str(item["name"]): str(item["value"])
        for item in custom_params
        if "name" in item and "value" in item
    }
