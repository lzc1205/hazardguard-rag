from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.schemas.user import UserCreate, UserUpdate, UserStatusUpdate, UserRoleUpdate
from app.services.user_service import UserService
from app.api.deps import require_admin

router = APIRouter(prefix="/users", tags=["users"])

@router.get("")
async def list_users(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), search: str | None = None, role: str | None = None, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)):
    result = await UserService(db).list_users(page=page, page_size=page_size, search=search, role=role)
    return {"code": 0, "message": "success", "data": result}

@router.get("/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)):
    user = await UserService(db).get_user(user_id)
    return {"code": 0, "message": "success", "data": user}

@router.post("")
async def create_user(request: UserCreate, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)):
    user = await UserService(db).create_user(request)
    return {"code": 0, "message": "success", "data": user}

@router.put("/{user_id}")
async def update_user(user_id: str, request: UserUpdate, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)):
    user = await UserService(db).update_user(user_id, request)
    return {"code": 0, "message": "success", "data": user}

@router.delete("/{user_id}")
async def delete_user(user_id: str, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)):
    await UserService(db).delete_user(user_id)
    return {"code": 0, "message": "success", "data": None}

@router.patch("/{user_id}/status")
async def toggle_user_status(user_id: str, request: UserStatusUpdate, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)):
    user = await UserService(db).toggle_status(user_id, request.is_active)
    return {"code": 0, "message": "success", "data": user}

@router.patch("/{user_id}/role")
async def change_user_role(user_id: str, request: UserRoleUpdate, db: AsyncSession = Depends(get_db), _admin=Depends(require_admin)):
    user = await UserService(db).change_role(user_id, request.role)
    return {"code": 0, "message": "success", "data": user}
