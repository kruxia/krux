import os
import databases
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route
from api.endpoints.index import Index
from api.endpoints.health import Health
from api.endpoints.ark import ArkParent
from api.endpoints.ark_files import ArkPath
from api.endpoints.export import ExportPath


async def app_startup():
    """
    On app startup, open a connection pool to the database server. 
    """
    app.db = databases.Database(
        os.getenv('DATABASE_URL'),
        min_size=int(os.getenv('DATABASE_POOL_MIN')),
        max_size=int(os.getenv('DATABASE_POOL_MAX')),
    )
    await app.db.connect()


async def app_shutdown():
    """
    On app shutdown, close the database connection pool.
    """
    await app.db.disconnect()


middleware = [Middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'])]

routes = [
    Route('/', endpoint=Index),
    Route('/health', endpoint=Health),
    Route('/ark/?', endpoint=ArkParent),
    Route('/ark/{name}/?', endpoint=ArkPath),
    Route('/ark/{name}/{path:path}', endpoint=ArkPath),
    Route('/export/{name}/{path:path}', endpoint=ExportPath),
]

app = Starlette(
    routes=routes,
    middleware=middleware,
    on_startup=[app_startup],
    on_shutdown=[app_shutdown],
)
