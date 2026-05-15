import os
from contextlib import closing

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from psycopg import connect
from psycopg.rows import dict_row

load_dotenv()


def get_database_url() -> str:
    database_url = os.getenv("postgresql://postgres:4521sOEhkCH8YQjh@db.pcmusigfekkckvrmhair.supabase.co:5432/postgres", "").strip()
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
                    select
                        current_database() as database_name,
                        current_schema() as schema_name,
                        now() as server_time
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
