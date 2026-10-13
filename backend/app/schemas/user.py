import uuid
from datetime import datetime
from pydantic import BaseModel, Field

class UserBase(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    email: str | None = None
    full_name: str | None = None
    phone: str | None = None
    role: str = Field(default="viewer", pattern="^(admin|supervisor|reviewer|technician|viewer)$")
    organization: str | None = None
    title: str | None = None

class UserCreate(UserBase):
    password: str = Field(..., min_length=6, max_length=128)

class UserUpdate(BaseModel):
    email: str | None = None
    full_name: str | None = None
    phone: str | None = None
    role: str | None = Field(default=None, pattern="^(admin|supervisor|reviewer|technician|viewer)$")
    organization: str | None = None
    title: str | None = None

class UserResponse(UserBase):
    id: uuid.UUID
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

class UserStatusUpdate(BaseModel):
    is_active: bool

class UserRoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(admin|supervisor|reviewer|technician|viewer)$")
