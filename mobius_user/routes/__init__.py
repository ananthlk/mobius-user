"""
Auth route modules for Flask and FastAPI.

Flask: from mobius_user.routes.flask_auth import bp
FastAPI: from mobius_user.routes.fastapi_auth import router

Then mount: app.register_blueprint(bp) or app.include_router(router, prefix="/api/v1/auth")
"""

__all__ = []
