import pytest
from unittest.mock import patch, AsyncMock
from pytest_httpx import HTTPXMock
from services.canva import CanvaClient, CanvaError

CANVA_BASE = "https://api.canva.com/rest/v1"
TOKEN = "canva-access-token"


def make_client() -> CanvaClient:
    return CanvaClient(access_token=TOKEN)


# --- list_designs ---

@pytest.mark.asyncio
async def test_list_designs(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{CANVA_BASE}/designs",
        json={"items": [{"id": "design-1", "title": "My Template"}]},
    )
    client = make_client()
    designs = await client.list_designs()
    assert len(designs) == 1
    assert designs[0]["id"] == "design-1"
    await client.close()


@pytest.mark.asyncio
async def test_list_designs_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(url=f"{CANVA_BASE}/designs", status_code=401, json={"error": "Unauthorized"})
    client = make_client()
    with pytest.raises(CanvaError, match="401"):
        await client.list_designs()
    await client.close()


# --- get_design ---

@pytest.mark.asyncio
async def test_get_design(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url=f"{CANVA_BASE}/designs/design-abc",
        json={"design": {"id": "design-abc", "title": "Cool Design"}},
    )
    client = make_client()
    design = await client.get_design("design-abc")
    assert design["id"] == "design-abc"
    await client.close()


# --- update_design_elements ---

@pytest.mark.asyncio
async def test_update_design_elements(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="PATCH",
        url=f"{CANVA_BASE}/designs/design-abc/elements",
        json={"success": True},
    )
    client = make_client()
    await client.update_design_elements("design-abc", {
        "elements": [{"id": "el-1", "type": "TEXT", "text": "New headline"}]
    })
    await client.close()


@pytest.mark.asyncio
async def test_update_design_elements_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="PATCH",
        url=f"{CANVA_BASE}/designs/design-abc/elements",
        status_code=422,
        json={"error": "Invalid element"},
    )
    client = make_client()
    with pytest.raises(CanvaError, match="422"):
        await client.update_design_elements("design-abc", {"elements": []})
    await client.close()


# --- export_design ---

@pytest.mark.asyncio
async def test_export_design_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url=f"{CANVA_BASE}/designs/design-abc/exports",
        json={"job": {"id": "export-job-1"}},
    )
    httpx_mock.add_response(
        url=f"{CANVA_BASE}/exports/export-job-1",
        json={"job": {"id": "export-job-1", "status": "success", "urls": ["https://cdn.canva.com/export.png"]}},
    )
    httpx_mock.add_response(
        url="https://cdn.canva.com/export.png",
        content=b"png-image-data",
    )

    client = make_client()
    with patch("services.canva.asyncio.sleep", new_callable=AsyncMock):
        result = await client.export_design("design-abc", format="png")
    assert result == b"png-image-data"
    await client.close()


@pytest.mark.asyncio
async def test_export_design_job_failed(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url=f"{CANVA_BASE}/designs/design-abc/exports",
        json={"job": {"id": "export-job-fail"}},
    )
    httpx_mock.add_response(
        url=f"{CANVA_BASE}/exports/export-job-fail",
        json={"job": {"id": "export-job-fail", "status": "failed", "error": "oops"}},
    )
    client = make_client()
    with patch("services.canva.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(CanvaError, match="Export job failed"):
            await client.export_design("design-abc")
    await client.close()


@pytest.mark.asyncio
@pytest.mark.httpx_mock(assert_all_requests_were_expected=False)
async def test_export_design_timeout(httpx_mock: HTTPXMock):
    for _ in range(3):
        httpx_mock.add_response(
            url=f"{CANVA_BASE}/exports/export-job-slow",
            json={"job": {"id": "export-job-slow", "status": "in_progress"}},
        )
    client = make_client()
    with patch("services.canva.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(TimeoutError):
            await client._poll_export_job("export-job-slow", max_retries=3, interval=0)
    await client.close()


# --- OAuth ---

@pytest.mark.asyncio
async def test_exchange_code_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.canva.com/rest/v1/oauth/token",
        json={"access_token": "new-token", "refresh_token": "refresh-xyz", "expires_in": 3600},
    )
    result = await CanvaClient.exchange_code(
        code="auth-code-abc",
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://localhost:3000/callback",
    )
    assert result["access_token"] == "new-token"
    assert result["refresh_token"] == "refresh-xyz"


@pytest.mark.asyncio
async def test_exchange_code_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.canva.com/rest/v1/oauth/token",
        status_code=400,
        json={"error": "invalid_grant"},
    )
    with pytest.raises(CanvaError, match="Token exchange failed"):
        await CanvaClient.exchange_code("bad-code", "cid", "csec", "http://cb")


@pytest.mark.asyncio
async def test_refresh_token_success(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        method="POST",
        url="https://api.canva.com/rest/v1/oauth/token",
        json={"access_token": "refreshed-token", "expires_in": 3600},
    )
    result = await CanvaClient.refresh_token("old-refresh", "cid", "csec")
    assert result["access_token"] == "refreshed-token"


# --- upload_asset ---

@pytest.mark.asyncio
async def test_upload_asset_success(httpx_mock: HTTPXMock):
    # POST includes ?name=slide.jpg query param
    httpx_mock.add_response(
        method="POST",
        url=f"{CANVA_BASE}/asset-uploads?name=slide.jpg",
        json={"job": {"id": "asset-job-1"}},
    )
    httpx_mock.add_response(
        url=f"{CANVA_BASE}/asset-uploads/asset-job-1",
        json={"job": {"id": "asset-job-1", "status": "success", "asset": {"id": "asset-abc"}}},
    )
    client = make_client()
    with patch("services.canva.asyncio.sleep", new_callable=AsyncMock):
        asset_id = await client.upload_asset(b"image-bytes", "slide.jpg")
    assert asset_id == "asset-abc"
    await client.close()
