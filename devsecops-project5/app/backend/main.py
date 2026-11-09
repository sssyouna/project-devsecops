"""Synthetic inventory API for the disposable defense-in-depth lab."""

from contextlib import asynccontextmanager
import os
import time

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
import psycopg
from psycopg.rows import dict_row


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The database may still be initializing when Kubernetes starts this Pod.
    for attempt in range(45):
        try:
            with connect() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS inventory (
                        id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        sku varchar(40) UNIQUE NOT NULL,
                        name varchar(100) NOT NULL,
                        quantity integer NOT NULL CHECK (quantity >= 0),
                        location varchar(80) NOT NULL
                    )
                """)
                # Seed only a genuinely new database. Restarting never resurrects
                # deleted demonstration records.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS app_metadata (
                        key text PRIMARY KEY, value text NOT NULL
                    )
                """)
                first_start = conn.execute("""
                    INSERT INTO app_metadata VALUES ('seeded', 'true')
                    ON CONFLICT DO NOTHING RETURNING key
                """).fetchone()
                if first_start:
                    for record in [
                        ("DEMO-001", "Demo laptop", 12, "Fictional office A"),
                        ("DEMO-002", "Demo monitor", 24, "Fictional office B"),
                        ("DEMO-003", "Demo keyboard", 36, "Fictional storeroom"),
                    ]:
                        conn.execute(
                            "INSERT INTO inventory (sku, name, quantity, location) VALUES (%s, %s, %s, %s)",
                            record,
                        )
            break
        except psycopg.OperationalError:
            if attempt == 44:
                raise
            time.sleep(2)
    yield


app = FastAPI(title="Project 5 — Synthetic Inventory", lifespan=lifespan)


class ItemInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sku: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=0, le=1_000_000, strict=True)
    location: str = Field(min_length=1, max_length=80)


class Item(ItemInput):
    id: int


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ok", "database": "reachable"}


@app.get("/api/items", response_model=list[Item])
def list_items():
    with connect() as conn:
        return conn.execute("SELECT * FROM inventory ORDER BY id").fetchall()


@app.get("/api/items/{item_id}", response_model=Item)
def get_item(item_id: int):
    with connect() as conn:
        item = conn.execute("SELECT * FROM inventory WHERE id = %s", (item_id,)).fetchone()
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.post("/api/items", response_model=Item, status_code=201)
def create_item(item: ItemInput):
    try:
        with connect() as conn:
            return conn.execute(
                "INSERT INTO inventory (sku, name, quantity, location) VALUES (%s, %s, %s, %s) RETURNING *",
                (item.sku, item.name, item.quantity, item.location),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="SKU already exists") from None


@app.put("/api/items/{item_id}", response_model=Item)
def update_item(item_id: int, item: ItemInput):
    try:
        with connect() as conn:
            result = conn.execute(
                "UPDATE inventory SET sku = %s, name = %s, quantity = %s, location = %s WHERE id = %s RETURNING *",
                (item.sku, item.name, item.quantity, item.location, item_id),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="SKU already exists") from None
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return result


@app.delete("/api/items/{item_id}", status_code=204)
def delete_item(item_id: int):
    with connect() as conn:
        result = conn.execute("DELETE FROM inventory WHERE id = %s RETURNING id", (item_id,)).fetchone()
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return Response(status_code=204)
