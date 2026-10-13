class AppException(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400, errors: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.errors = errors
        super().__init__(message)

class NotFoundException(AppException):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(code=40400, message=message, status_code=404)

class UnauthorizedException(AppException):
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(code=40101, message=message, status_code=401)

class ForbiddenException(AppException):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(code=40301, message=message, status_code=403)

class ValidationException(AppException):
    def __init__(self, message: str = "Validation failed", errors: dict | None = None):
        super().__init__(code=40001, message=message, status_code=400, errors=errors)

class ConflictException(AppException):
    def __init__(self, message: str = "Resource conflict"):
        super().__init__(code=40900, message=message, status_code=409)
