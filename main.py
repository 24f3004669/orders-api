import base64
import math
import time
import uuid
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

TOTAL = 50          # T: orders 1..50 in the fixed catalog
RATE_LIMIT = 17     # R: requests allowed per window per client
WINDOW = 10.0       # seconds

app = FastAPI()

request_times = defaultdict(deque)   # client id -> timestamps of recent requests
idempotent_orders = {}               # idempotency key -> the order created


# ---------- 3) Rate limiting (per X-Client-Id) ----------
# Added BEFORE the CORS middleware so CORS wraps it and 429s also get CORS headers.
@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    client_id = request.headers.get("x-client-id")
    if client_id and request.method != "OPTIONS":
        now = time.monotonic()
        q = request_times[client_id]
        while q and now - q[0] >= WINDOW:      # forget requests older than 10s
            q.popleft()
        if len(q) >= RATE_LIMIT:
            retry_after = max(1, math.ceil(WINDOW - (now - q[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests"},
                headers={"Retry-After": str(retry_after)},
            )
        q.append(now)
    return await call_next(request)


# ---------- CORS: allow the grader's web page ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)


@app.get("/")
async def home():
    return {"status": "ok", "use": "POST /orders, GET /orders"}


# ---------- 1) Idempotent order creation ----------
@app.post("/orders")
async def create_order(request: Request):
    key = request.headers.get("idempotency-key")

    if key and key in idempotent_orders:               # seen this key before
        return JSONResponse(
            status_code=201,
            content=idempotent_orders[key],
            headers={"Idempotent-Replayed": "true"},
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    order = {
        "id": "ord_" + uuid.uuid4().hex[:12],
        "status": "created",
        "data": body,
        "created_at": int(time.time()),
    }
    if key:
        idempotent_orders[key] = order
    return JSONResponse(status_code=201, content=order)


# ---------- 2) Cursor pagination ----------
def encode_cursor(offset: int) -> str:
    raw = base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()
    return raw.rstrip("=")                             # no "=" so URLs stay clean


def decode_cursor(cursor: str) -> int:
    padded = cursor + "=" * (-len(cursor) % 4)
    return int(base64.urlsafe_b64decode(padded.encode()).decode().split(":")[1])


@app.get("/orders")
async def list_orders(limit: int = 10, cursor: Optional[str] = None):
    limit = max(1, min(limit, TOTAL))
    start = 0
    if cursor:
        try:
            start = max(0, min(decode_cursor(cursor), TOTAL))
        except Exception:
            return JSONResponse(status_code=400, content={"detail": "invalid cursor"})

    end = min(start + limit, TOTAL)
    items = [{"id": i, "name": f"Order {i}"} for i in range(start + 1, end + 1)]
    next_cursor = encode_cursor(end) if end < TOTAL else None
    return {"items": items, "next_cursor": next_cursor}