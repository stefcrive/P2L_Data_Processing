from fastapi import FastAPI
from fastapi.responses import JSONResponse
from .api.v1.router import router as v1_router
from .api.internal.router import router as internal_router


def create_app() -> FastAPI:
    app = FastAPI(title="IRMS Lab API", version="0.1.0")
    app.include_router(v1_router, prefix="/v1")
    app.include_router(internal_router, prefix="/internal")

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
