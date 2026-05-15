import os
from contextlib import closing

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from psycopg import connect
from psycopg.rows import dict_row

load_dotenv()


def get_database_url() -> str:
    database_url = os.getenv("SUPABASE_DB_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "Define la variable de entorno SUPABASE_DB_URL con la cadena de conexion de Supabase."
        )
    return database_url


def get_db_connection():
    return connect(get_database_url(), row_factory=dict_row)


app = FastAPI(
    title="GreenCart Backend",
    description="Backend base conectado a una base de datos Supabase.",
    version="0.1.0",
)


# ── Modelos ──────────────────────────────────────────────────────────────────

class ProductoIn(BaseModel):
    nombre: str
    precio: float


# ── Inicio: crear tablas si no existen ───────────────────────────────────────

@app.on_event("startup")
def crear_tablas():
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS productos (
                    id     SERIAL PRIMARY KEY,
                    nombre TEXT    NOT NULL,
                    precio NUMERIC NOT NULL
                );
                """
            )
        conn.commit()


# ── Utilidad ─────────────────────────────────────────────────────────────────

@app.get("/")
def read_root():
    return {
        "message": "GreenCart backend activo",
        "supabase_configured": bool(os.getenv("SUPABASE_DB_URL")),
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


# ── Productos ─────────────────────────────────────────────────────────────────

@app.get("/productos")
def obtener_productos():
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, nombre, precio FROM productos ORDER BY id;")
            rows = cur.fetchall()
    return {"productos": rows}


@app.get("/productos/{producto_id}")
def obtener_producto(producto_id: int):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, nombre, precio FROM productos WHERE id = %s;",
                (producto_id,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    return row


@app.post("/productos", status_code=201)
def crear_producto(producto: ProductoIn):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO productos (nombre, precio) VALUES (%s, %s) RETURNING id, nombre, precio;",
                (producto.nombre, producto.precio),
            )
            nuevo = cur.fetchone()
        conn.commit()
    return nuevo


@app.put("/productos/{producto_id}")
def actualizar_producto(producto_id: int, producto: ProductoIn):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE productos
                SET nombre = %s, precio = %s
                WHERE id = %s
                RETURNING id, nombre, precio;
                """,
                (producto.nombre, producto.precio, producto_id),
            )
            actualizado = cur.fetchone()
        conn.commit()
    if not actualizado:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    return actualizado


@app.delete("/productos/{producto_id}")
def eliminar_producto(producto_id: int):
    with closing(get_db_connection()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM productos WHERE id = %s RETURNING id;",
                (producto_id,),
            )
            eliminado = cur.fetchone()
        conn.commit()
    if not eliminado:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    return {"mensaje": f"Producto {producto_id} eliminado."}
