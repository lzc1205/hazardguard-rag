from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.utils.security import hash_password
from app.utils.exceptions import NotFoundException, ConflictException

class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_users(self, page: int = 1, page_size: int = 20, search: str | None = None, role: str | None = None) -> dict:
        query = select(User).where(User.is_deleted == False)
        count_query = select(func.count(User.id)).where(User.is_deleted == False)
        if search:
            sf = or_(User.username.ilike(f"%{search}%"), User.full_name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
            query = query.where(sf)
            count_query = count_query.where(sf)
        if role:
            query = query.where(User.role == role)
            count_query = count_query.where(User.role == role)
        total = (await self.db.execute(count_query)).scalar() or 0
        query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        users = (await self.db.execute(query)).scalars().all()
        return {"items": users, "total": total, "page": page, "page_size": page_size}

    async def get_user(self, user_id: str) -> User:
        result = await self.db.execute(select(User).where(User.id == user_id, User.is_deleted == False))
        user = result.scalar_one_or_none()
        if not user:
            raise NotFoundException("User not found")
        return user

    async def create_user(self, data: UserCreate) -> User:
        result = await self.db.execute(select(User).where(User.username == data.username, User.is_deleted == False))
        if result.scalar_one_or_none():
            raise ConflictException("Username already exists")
        user = User(username=data.username, email=data.email, hashed_password=hash_password(data.password), full_name=data.full_name, phone=data.phone, role=data.role, organization=data.organization, title=data.title)
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def update_user(self, user_id: str, data: UserUpdate) -> User:
        user = await self.get_user(user_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(user, key, value)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def delete_user(self, user_id: str):
        user = await self.get_user(user_id)
        user.is_deleted = True
        await self.db.commit()

    async def toggle_status(self, user_id: str, is_active: bool) -> User:
        user = await self.get_user(user_id)
        user.is_active = is_active
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def change_role(self, user_id: str, role: str) -> User:
        user = await self.get_user(user_id)
        user.role = role
        await self.db.commit()
        await self.db.refresh(user)
        return user
