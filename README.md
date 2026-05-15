# GreenCart Backend

Conexion base entre FastAPI y una base de datos Supabase usando PostgreSQL.

## 1. Configura tus variables

Crea un archivo `.env` en la raiz del proyecto con esta variable:

```env
SUPABASE_DB_URL=postgresql://postgres:[TU_PASSWORD]@db.[TU_PROJECT_REF].supabase.co:5432/postgres
```

Puedes usar `.env.example` como referencia.

## 2. Instala dependencias

```bash
uv sync
```

## 3. Levanta el servidor

```bash
uv run uvicorn main:app --reload
```

## 4. Prueba la conexion

Visita:

- `GET /`
- `GET /health/db`

Si la conexion funciona, `/health/db` responde con el nombre de la base, el schema actual y la hora del servidor.
