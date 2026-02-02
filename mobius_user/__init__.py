"""
Mobius User Module - Shared user/auth functionality for Mobius applications.

This module provides:
- User models (Tenant, Role, AppUser, UserSession, Activity, UserPreference)
- Authentication service (JWT, bcrypt, register, login, validate)
- User context service (UserProfile, get_user_profile)
- Flask Blueprint and FastAPI router for auth routes

Usage:
    from mobius_user import get_auth_service, get_user_context_service
    from mobius_user.db import init_db, get_db_session
    
    # Initialize DB connection
    init_db(database_url)
    
    # Use auth service
    auth_service = get_auth_service()
    user, error = auth_service.register_user(...)
"""

__version__ = "0.1.0"

from mobius_user.services.auth_service import AuthService, get_auth_service
from mobius_user.services.user_context import UserContextService, get_user_context_service, UserProfile

__all__ = [
    "AuthService",
    "get_auth_service",
    "UserContextService", 
    "get_user_context_service",
    "UserProfile",
]
