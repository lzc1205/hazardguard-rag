from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "HazardGuard-RAG"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hazardguard"
    REDIS_URL: str = "redis://localhost:6379/0"
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_API_KEY: str = "sk-xxx"
    # deepseek-chat 会由服务端路由到当前主力模型；
    # 需指定具体版本时用 deepseek-v4-pro 之类
    LLM_MODEL: str = "deepseek-chat"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 1500
    LLM_TIMEOUT: float = 60.0

    # ---------- 向量化 ----------
    # 实际生效的是字符 n-gram（CharSemanticEmbedding，512 维，纯 numpy 零依赖）。
    # 这是本项目**真实使用**的方案，不是占位符——
    # 见 app/rag/embedding.py 的实现说明。
    EMBEDDING_PROVIDER: str = "char"  # char（默认，实际使用）| openai（需自备 API Key）
    # 仅当 EMBEDDING_PROVIDER=openai 时才读这个字段；
    # char 模式下向量维度由 EMBEDDING_DIMENSION 决定，与模型名无关。
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    EMBEDDING_DIMENSION: int = 512

    # ---------- 向量库 ----------
    # 只有 PGVector 一种实现（app/rag/vector_store.py）：
    # 向量存于 PostgreSQL 的 knowledge_chunks.embedding 列
    # （VECTOR(512) + HNSW 索引），随数据库持久化。
    # 曾有 chroma 配置项，但从未实现也从未被读取，已删除——
    # 留着只会让人误以为项目用了 Chroma。
    # 若要接入 Chroma，需要先实现对应的 Store 类再在
    # vector_store.py 里做分支，而不是先写一个空配置项。

    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    UPLOAD_DIR: str = "./data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 100
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    # ---------- 检索 ----------
    DEFAULT_CHUNK_SIZE: int = 512
    DEFAULT_CHUNK_OVERLAP: int = 64
    RETRIEVER_TOP_K: int = 20
    # 混合检索双路权重（RRF 融合）。向量路略高——字符 n-gram 能提供
    # BM25 拿不到的语义近似召回；两者互补而非替代。
    HYBRID_W_VECTOR: float = 0.55
    HYBRID_W_BM25: float = 0.45
    # 规则加权前的候选池倍数。规则加权只能重排「已进入候选池」的文档，
    # 池子太小则目标条款在加权前就被丢弃，加权再高也无效。
    RETRIEVER_POOL_MULTIPLIER: int = 6

    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_PASSWORD: str = "admin123"
    # 允许 .env 里出现未定义的键，避免升级配置项时旧 .env 直接启动失败。
    # 但这会掩盖拼写错误，所以另有 test_config_truth 守护配置与文档一致。
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "allow"}

settings = Settings()
