from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.utils.security import verify_password, create_access_token
from app.utils.exceptions import UnauthorizedException

class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def login(self, username: str, password: str) -> dict:
        result = await self.db.execute(select(User).where(User.username == username, User.is_deleted == False))
        user = result.scalar_one_or_none()
        if not user or not verify_password(password, user.hashed_password):
            raise UnauthorizedException("Invalid username or password")
        if not user.is_active:
            raise UnauthorizedException("Account is disabled")
        user.last_login_at = datetime.utcnow()
        await self.db.commit()
        access_token = create_access_token(data={"sub": str(user.id), "role": user.role})
        return {"access_token": access_token, "token_type": "bearer", "user": {"id": str(user.id), "username": user.username, "email": user.email, "full_name": user.full_name, "role": user.role, "organization": user.organization, "is_active": user.is_active}}
