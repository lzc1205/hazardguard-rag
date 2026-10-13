"""密码哈希与 JWT 工具。

**重要：不再使用 passlib 的 CryptContext。**
原因：passlib 1.7.4（2020 年）与 bcrypt 5.x 不兼容——
passlib 调用 `_bcrypt.hashpw()` 时会抛
`ValueError: password cannot be longer than 72 bytes`
（它对密码做 SHA256 预哈希后再交给 bcrypt，而 bcrypt 5.x 收不下该格式）。
这类"库版本组合踩坑"会在任何涉及密码的地方复现，包括登录与注册。

因此直接使用原生 bcrypt API，行为可控，无中间层 surprises。
"""

from datetime import datetime, timedelta

import bcrypt
from jose import JWTError, jwt

from app.config import settings

# bcrypt 硬上限 72 字节，超出必须手动截断，否则抛 ValueError
BCRYPT_MAX_BYTES = 72
BCRYPT_ROUNDS = 12


def _encode(password: str) -> bytes:
    """按 UTF-8 字节截断到 bcrypt 上限。

    必须在调用前截断而非让 bcrypt 抛错，否则超长密码用户无法注册。
    """
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raw = raw[:BCRYPT_MAX_BYTES]
    return raw


def hash_password(password: str) -> str:
    """生成 bcrypt 哈希。"""
    return bcrypt.hashpw(
        _encode(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验密码。

    哈希格式非法时返回 False 而非抛异常——
    否则历史脏数据会导致整个登录接口 500。
    """
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(_encode(plain_password), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """签发 JWT。"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """解析 JWT，失败抛 ValueError。"""
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise ValueError("Invalid token")