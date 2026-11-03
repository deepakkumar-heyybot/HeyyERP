"""The GST service provider (GSP) behind one interface: e-invoice (IRN) and e-way bill.

V-Rub holds the contract with a licensed GSP, so the system talks to it through a small adapter.
`mock` makes plausible test numbers (never in production: it refuses to run there). `http` posts the
standard e-invoice JSON to the provider's URLs and reads the standard answer (IRN, acknowledgement
number and date, signed QR; e-way bill number and validity). Each GSP has small differences in its
paths and wrapping, so the `http` adapter is the single place to adjust once V-Rub's GSP is chosen;
it cannot be tried until a sandbox exists. A provider raises `GspError`; `retryable` says whether
trying again can help (a timeout or a server error) or not (the provider rejected the data).
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from heyyerp_api.config import get_settings


class GspError(Exception):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class IrnResult:
    irn: str
    ack_no: str
    ack_date: datetime
    signed_qr: str


@dataclass
class EwbResult:
    ewb_no: str
    valid_till: datetime


class Gsp(Protocol):
    async def generate_irn(self, payload: dict[str, Any]) -> IrnResult: ...

    async def cancel_irn(self, irn: str, reason: str, remark: str) -> None: ...

    async def generate_ewb(self, payload: dict[str, Any]) -> EwbResult: ...

    async def cancel_ewb(self, ewb_no: str, reason: str, remark: str) -> None: ...

    async def update_ewb_vehicle(self, ewb_no: str, vehicle_no: str, reason: str,
                                 remark: str) -> None: ...


class MockGsp:
    """Test numbers made from the document, so the same invoice always gets the same IRN."""

    async def generate_irn(self, payload: dict[str, Any]) -> IrnResult:
        doc = payload["DocDtls"]
        key = f"{payload['SellerDtls']['Gstin']}|{doc['Typ']}|{doc['No']}"
        digest = hashlib.sha256(key.encode()).hexdigest()
        return IrnResult(irn=digest, ack_no=str(int(digest[:12], 16))[:15].rjust(15, "1"),
                         ack_date=datetime.now(UTC), signed_qr=f"MOCK-QR:{digest[:32]}")

    async def cancel_irn(self, irn: str, reason: str, remark: str) -> None:
        return None

    async def generate_ewb(self, payload: dict[str, Any]) -> EwbResult:
        digest = hashlib.sha256(f"{payload['fromGstin']}|{payload['docNo']}".encode()).hexdigest()
        days = max(1, -(-int(payload.get("transDistance") or 0) // 200))  # one day per 200 km
        return EwbResult(ewb_no=str(int(digest[:12], 16))[:12].rjust(12, "3"),
                         valid_till=datetime.now(UTC) + timedelta(days=days))

    async def cancel_ewb(self, ewb_no: str, reason: str, remark: str) -> None:
        return None

    async def update_ewb_vehicle(self, ewb_no: str, vehicle_no: str, reason: str,
                                 remark: str) -> None:
        return None


class HttpGsp:
    """Posts the standard JSON to the provider and reads the standard answer."""

    def __init__(self) -> None:
        cfg = get_settings()
        if not cfg.gsp_base_url or not cfg.gsp_token:
            raise GspError("GSP_BASE_URL and GSP_TOKEN are not set", retryable=False)
        self.base, self.token = cfg.gsp_base_url.rstrip("/"), cfg.gsp_token

    async def call(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                answer = await client.post(f"{self.base}{path}", json=body,
                                           headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError as exc:
            raise GspError(f"The GST provider could not be reached: {type(exc).__name__}") from exc
        if answer.status_code >= 500:
            raise GspError(f"The GST provider failed ({answer.status_code})")
        data: dict[str, Any] = answer.json() if answer.content else {}
        if answer.status_code >= 400 or data.get("error") or data.get("ErrorDetails"):
            detail = data.get("error") or data.get("ErrorDetails") or answer.text[:200]
            raise GspError(f"The GST provider rejected it: {str(detail)[:300]}", retryable=False)
        return data

    async def generate_irn(self, payload: dict[str, Any]) -> IrnResult:
        data = await self.call(get_settings().gsp_irn_path, payload)
        data = data.get("data", data)
        try:
            return IrnResult(
                irn=str(data["Irn"]), ack_no=str(data["AckNo"]),
                ack_date=datetime.fromisoformat(str(data["AckDt"]).replace(" ", "T")).replace(
                    tzinfo=UTC), signed_qr=str(data.get("SignedQRCode", "")))
        except (KeyError, ValueError) as exc:
            raise GspError("The GST provider's answer has no IRN", retryable=False) from exc

    async def cancel_irn(self, irn: str, reason: str, remark: str) -> None:
        await self.call(get_settings().gsp_irn_cancel_path,
                        {"Irn": irn, "CnlRsn": reason, "CnlRem": remark})

    async def generate_ewb(self, payload: dict[str, Any]) -> EwbResult:
        data = await self.call(get_settings().gsp_ewb_path, payload)
        data = data.get("data", data)
        try:
            return EwbResult(ewb_no=str(data["EwbNo"]), valid_till=datetime.fromisoformat(
                str(data["EwbValidTill"]).replace(" ", "T")).replace(tzinfo=UTC))
        except (KeyError, ValueError) as exc:
            raise GspError("The GST provider's answer has no e-way bill number",
                           retryable=False) from exc

    async def cancel_ewb(self, ewb_no: str, reason: str, remark: str) -> None:
        await self.call(get_settings().gsp_ewb_path + "/cancel",
                        {"ewbNo": ewb_no, "cancelRsnCode": reason, "cancelRmrk": remark})

    async def update_ewb_vehicle(self, ewb_no: str, vehicle_no: str, reason: str,
                                 remark: str) -> None:
        await self.call(get_settings().gsp_ewb_path + "/vehicle",
                        {"ewbNo": ewb_no, "vehicleNo": vehicle_no, "reasonCode": reason,
                         "reasonRem": remark})


override: dict[str, Gsp] = {}  # tests and special set-ups put their own provider here


def provider() -> Gsp:
    """The provider the server settings name."""
    if "gsp" in override:
        return override["gsp"]
    cfg = get_settings()
    if cfg.gsp_provider == "http":
        return HttpGsp()
    if cfg.app_env == "production":
        raise GspError("The mock GST provider cannot be used in production", retryable=False)
    return MockGsp()
