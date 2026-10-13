from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import User
from app.utils.security import decode_access_token
from app.utils.exceptions import UnauthorizedException, ForbiddenException

async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """解析 Bearer Token 并返回当前用户。

    authorization 必须给默认值 None：若声明为必填，
    缺 token 的请求会在进入函数前就返回 422 参数错误，
    而不是业务上更准确的 401。
    """
    if not authorization:
        raise UnauthorizedException("缺少认证信息")
    if not authorization.startswith("Bearer "):
        raise UnauthorizedException("Invalid auth format")
    token = authorization[7:].strip()
    try:
        payload = decode_access_token(token)
    except ValueError:
        raise UnauthorizedException("Token invalid or expired")
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedException("Token invalid")
    result = await db.execute(select(User).where(User.id == user_id, User.is_deleted == False))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise UnauthorizedException("User not found or disabled")
    return user

def require_roles(*roles: str):
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise ForbiddenException(f"Requires one of roles: {', '.join(roles)}")
        return current_user
    return role_checker

require_admin = require_roles("admin")
