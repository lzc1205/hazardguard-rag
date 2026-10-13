from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.auth import LoginRequest, PasswordChangeRequest
from app.services.auth_service import AuthService
from app.api.deps import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login")
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await AuthService(db).login(request.username, request.password)
    return {"code": 0, "message": "success", "data": result}

@router.post("/refresh")
async def refresh_token():
    return {"code": 0, "message": "success", "data": None}

@router.post("/logout")
async def logout():
    return {"code": 0, "message": "success", "data": None}

@router.get("/me")
async def me(current_user=Depends(get_current_user)):
    return {"code": 0, "message": "success", "data": {"id": str(current_user.id), "username": current_user.username, "email": current_user.email, "full_name": current_user.full_name, "role": current_user.role, "organization": current_user.organization, "is_active": current_user.is_active}}

@router.put("/me/password")
async def change_password(request: PasswordChangeRequest, current_user=Depends(get_current_user)):
    return {"code": 0, "message": "success", "data": None}
