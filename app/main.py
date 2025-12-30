import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.spaces import (
    get_s3_client,
    make_zip_object_key,
    presign_put_zip,
    fetch_object_bytes,
    delete_object,
)
from app.latex_compile import compile_zip_bytes_to_pdf


APP_API_KEY = os.environ.get("APP_API_KEY", "")
SPACES_BUCKET = os.environ.get("SPACES_BUCKET", "")

app = FastAPI(title="LaTeX Render API")


def require_api_key(x_api_key: str | None):
    if not APP_API_KEY or x_api_key != APP_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {"ok": True}


class PresignResponse(BaseModel):
    key: str
    put_url: str
    expires_in: int


@app.post("/presign", response_model=PresignResponse)
def presign(x_api_key: str | None = Header(default=None)):
    require_api_key(x_api_key)

    if not SPACES_BUCKET:
        raise HTTPException(status_code=500, detail="SPACES_BUCKET not set")

    s3 = get_s3_client()
    key = make_zip_object_key()
    expires_in = 300
    put_url = presign_put_zip(s3, SPACES_BUCKET, key, expires_seconds=expires_in)
    return PresignResponse(key=key, put_url=put_url, expires_in=expires_in)


class CompileRequest(BaseModel):
    key: str
    delete_after: bool = True


@app.post("/compile")
def compile(req: CompileRequest, x_api_key: str | None = Header(default=None)):
    require_api_key(x_api_key)

    if not req.key.startswith("uploads/") or not req.key.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Invalid key")

    s3 = get_s3_client()
    try:
        zip_bytes = fetch_object_bytes(s3, SPACES_BUCKET, req.key)
        pdf_bytes = compile_zip_bytes_to_pdf(zip_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if req.delete_after:
            try:
                delete_object(s3, SPACES_BUCKET, req.key)
            except Exception:
                pass  # not fatal

    return Response(content=pdf_bytes, media_type="application/pdf")
