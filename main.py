import json
import os
import secrets
import subprocess
from base64 import b64decode, b64encode
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import pbkdf2_hmac
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from psycopg import connect
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(ENV_PATH)

ORDER_STATES = [
    "ORDEN_CREADA",
    "EN_PROCESO",
    "PREPARANDO",
    "ENVIADO",
    "EN_CAMINO",
    "ENTREGADO",
]
ORDER_STEP_SECONDS = 4
PASSWORD_ITERATIONS = 120_000


def get_database_url() -> str:
    database_url = os.getenv("SUPABASE_DB_URL", "").strip() or os.getenv(
        "DATABASE_URL", ""
    ).strip()
    if not database_url:
        raise RuntimeError(
            "Define la variable de entorno SUPABASE_DB_URL o DATABASE_URL con la cadena de conexion de PostgreSQL."
        )
    return database_url


def get_db_connection():
    return connect(get_database_url(), row_factory=dict_row)


def frontend_store_data_path() -> Path:
    return Path(__file__).resolve().parents[1] / "GreenCard" / "src" / "storeData.js"


def load_seed_products_from_frontend() -> list[dict]:
    store_data_path = frontend_store_data_path()
    if not store_data_path.exists():
        raise RuntimeError(
            f"No se encontro el catalogo del frontend en {store_data_path}."
        )

    module_uri = store_data_path.resolve().as_uri()
    script = (
        f"import {{ products }} from {json.dumps(module_uri)};"
        "process.stdout.write(JSON.stringify(products));"
    )

    try:
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "No fue posible sembrar la base de datos porque Node.js no esta disponible."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"No fue posible leer los productos quemados del frontend: {exc.stderr.decode('utf-8', errors='replace').strip()}"
        ) from exc

    try:
        return json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("El catalogo del frontend no devolvio un JSON valido.") from exc


def decimal_to_float(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def serialize_product_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "category": row["category"],
        "stock": row["stock"],
        "rating": decimal_to_float(row["rating"]),
        "reviews": row["reviews"],
        "badge": row["badge"],
        "image": row["image"],
        "basePrice": decimal_to_float(row["base_price"]),
        "name": row["name"],
        "desc": row["description"],
    }


def build_order_history(created_at: datetime, status_index: int) -> list[dict]:
    history = []
    for index in range(status_index + 1):
        history.append(
            {
                "status": ORDER_STATES[index],
                "time": (
                    created_at + timedelta(seconds=index * ORDER_STEP_SECONDS)
                ).isoformat()
            }
        )
    return history


def hydrate_order(row: dict, now: datetime | None = None) -> dict:
    created_at = row["created_at"]
    current_time = now or datetime.now(timezone.utc)
    elapsed_seconds = max(0, int((current_time - created_at).total_seconds()))
    status_index = min(elapsed_seconds // ORDER_STEP_SECONDS, len(ORDER_STATES) - 1)
    history = build_order_history(created_at, status_index)

    return {
        "id": row["id"],
        "items": row["items"],
        "shippingInfo": row["shipping_info"],
        "paymentInfo": row["payment_info"],
        "country": row["country"],
        "currency": row["currency"],
        "total": decimal_to_float(row["total"]),
        "status": ORDER_STATES[status_index],
        "statusIndex": status_index,
        "createdAt": created_at.isoformat(),
        "history": history,
    }


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    password_hash = pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return (
        f"pbkdf2_sha256${PASSWORD_ITERATIONS}$"
        f"{b64encode(salt).decode('ascii')}$"
        f"{b64encode(password_hash).decode('ascii')}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, password_hash = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False

        candidate = pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            b64decode(salt.encode("ascii")),
            int(iterations),
        )
        return secrets.compare_digest(
            b64encode(candidate).decode("ascii"), password_hash
        )
    except Exception:
        return False


def serialize_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "createdAt": row["created_at"].isoformat(),
    }


def ensure_schema() -> None:
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id          TEXT PRIMARY KEY,
                    category    TEXT NOT NULL,
                    stock       INTEGER NOT NULL CHECK (stock >= 0),
                    rating      NUMERIC(3, 1) NOT NULL,
                    reviews     INTEGER NOT NULL CHECK (reviews >= 0),
                    badge       TEXT,
                    image       TEXT NOT NULL,
                    base_price  NUMERIC(10, 2) NOT NULL,
                    name        JSONB NOT NULL,
                    description JSONB NOT NULL,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id           TEXT PRIMARY KEY,
                    items        JSONB NOT NULL,
                    shipping_info JSONB NOT NULL,
                    payment_info JSONB NOT NULL,
                    country      TEXT NOT NULL,
                    currency     TEXT NOT NULL,
                    total        NUMERIC(12, 2) NOT NULL,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    email         TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        conn.commit()


def seed_products_if_needed() -> int:
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM products;")
            total = cur.fetchone()["total"]
            if total:
                return total

            products = load_seed_products_from_frontend()
            for product in products:
                base_price = product.get("prices", {}).get("USD", 0)
                cur.execute(
                    """
                    INSERT INTO products (
                        id, category, stock, rating, reviews, badge, image,
                        base_price, name, description
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb);
                    """,
                    (
                        product["id"],
                        product["category"],
                        product["stock"],
                        product["rating"],
                        product["reviews"],
                        product.get("badge"),
                        product["image"],
                        base_price,
                        json.dumps(product["name"], ensure_ascii=False),
                        json.dumps(product["desc"], ensure_ascii=False),
                    ),
                )
        conn.commit()
    return len(products)


def initialize_database() -> dict:
    ensure_schema()
    seeded_products = seed_products_if_needed()

    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM products;")
            products_total = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM orders;")
            orders_total = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM users;")
            users_total = cur.fetchone()["total"]

    return {
        "ok": True,
        "productsSeeded": seeded_products,
        "productsTotal": products_total,
        "ordersTotal": orders_total,
        "usersTotal": users_total,
        "databaseUrlConfigured": bool(get_database_url()),
        "envPath": str(ENV_PATH),
    }


class StockAdjustmentIn(BaseModel):
    quantity: int = Field(gt=0)


class OrderItemIn(BaseModel):
    id: str
    name: dict[str, str]
    image: str
    price: float = Field(ge=0)
    qty: int = Field(gt=0)


class ShippingInfoIn(BaseModel):
    name: str
    email: str
    address: str
    city: str
    phone: str


class PaymentInfoIn(BaseModel):
    method: str
    amount: float = Field(ge=0)
    currency: str
    txId: str | None = None


class OrderIn(BaseModel):
    items: list[OrderItemIn]
    shippingInfo: ShippingInfoIn
    paymentInfo: PaymentInfoIn
    country: str
    currency: str


class UserRegisterIn(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    password: str = Field(min_length=6)


class UserLoginIn(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


app = FastAPI(
    title="GreenCart Backend",
    description="Backend conectado a PostgreSQL para productos, stock y ordenes.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    initialize_database()


@app.get("/")
def read_root():
    return {
        "message": "GreenCart backend activo",
        "database_configured": bool(
            os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
        ),
        "seed_source": str(frontend_store_data_path()),
        "env_path": str(ENV_PATH),
    }


@app.get("/health/db")
def check_database_connection():
    try:
        with closing(get_db_connection()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        current_database() AS database_name,
                        current_schema()   AS schema_name,
                        now()              AS server_time
                    """
                )
                result = cursor.fetchone()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"No fue posible conectarse a Supabase: {exc}",
        ) from exc

    return {
        "ok": True,
        "database": result["database_name"],
        "schema": result["schema_name"],
        "server_time": result["server_time"].isoformat(),
    }


@app.post("/api/setup")
def setup_database():
    try:
        return initialize_database()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/products")
def list_products():
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, category, stock, rating, reviews, badge, image, base_price, name, description
                FROM products
                ORDER BY id;
                """
            )
            rows = cur.fetchall()

    return {"products": [serialize_product_row(row) for row in rows]}


@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, category, stock, rating, reviews, badge, image, base_price, name, description
                FROM products
                WHERE id = %s;
                """,
                (product_id,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    return serialize_product_row(row)


@app.post("/api/products/{product_id}/reserve")
def reserve_stock(product_id: str, payload: StockAdjustmentIn):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE products
                SET stock = stock - %s, updated_at = NOW()
                WHERE id = %s AND stock >= %s
                RETURNING id, category, stock, rating, reviews, badge, image, base_price, name, description;
                """,
                (payload.quantity, product_id, payload.quantity),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        raise HTTPException(
            status_code=409,
            detail="No hay stock suficiente para reservar el producto.",
        )

    return serialize_product_row(row)


@app.post("/api/products/{product_id}/restore")
def restore_stock(product_id: str, payload: StockAdjustmentIn):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE products
                SET stock = stock + %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id, category, stock, rating, reviews, badge, image, base_price, name, description;
                """,
                (payload.quantity, product_id),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    return serialize_product_row(row)


@app.get("/api/orders")
def list_orders():
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, items, shipping_info, payment_info, country, currency, total, created_at
                FROM orders
                ORDER BY created_at;
                """
            )
            rows = cur.fetchall()

    return {"orders": [hydrate_order(row) for row in rows]}


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, items, shipping_info, payment_info, country, currency, total, created_at
                FROM orders
                WHERE id = %s;
                """,
                (order_id,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")

    return hydrate_order(row)


@app.post("/api/orders", status_code=201)
def create_order(order: OrderIn):
    if not order.items:
        raise HTTPException(status_code=400, detail="La orden debe tener al menos un item.")

    order_id = f"ORD-{uuid4().hex[:10].upper()}"
    total = sum(item.price * item.qty for item in order.items)

    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (
                    id, items, shipping_info, payment_info, country, currency, total
                )
                VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
                RETURNING id, items, shipping_info, payment_info, country, currency, total, created_at;
                """,
                (
                    order_id,
                    json.dumps([item.model_dump() for item in order.items], ensure_ascii=False),
                    json.dumps(order.shippingInfo.model_dump(), ensure_ascii=False),
                    json.dumps(order.paymentInfo.model_dump(), ensure_ascii=False),
                    order.country,
                    order.currency,
                    total,
                ),
            )
            created_order = cur.fetchone()
        conn.commit()

    return hydrate_order(created_order)


@app.post("/api/auth/register", status_code=201)
def register_user(payload: UserRegisterIn):
    normalized_email = payload.email.strip().lower()
    if not normalized_email:
        raise HTTPException(status_code=400, detail="El correo es obligatorio.")

    user_id = f"USR-{uuid4().hex[:10].upper()}"

    with closing(get_db_connection()) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, name, email, password_hash)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, name, email, created_at;
                    """,
                    (
                        user_id,
                        payload.name.strip(),
                        normalized_email,
                        hash_password(payload.password),
                    ),
                )
                user = cur.fetchone()
            conn.commit()
        except UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(
                status_code=409, detail="Ya existe una cuenta con ese correo."
            ) from exc

    return {"user": serialize_user(user)}


@app.post("/api/auth/login")
def login_user(payload: UserLoginIn):
    normalized_email = payload.email.strip().lower()

    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, email, password_hash, created_at
                FROM users
                WHERE email = %s;
                """,
                (normalized_email,),
            )
            user = cur.fetchone()

    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Correo o contrasena incorrectos.")

    return {"user": serialize_user(user)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
