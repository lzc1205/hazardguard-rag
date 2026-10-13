"""Embedding 抽象层。

对应 JD「RAG 完整链路」（63%）中向量化的环节。

**本项目实际使用的方案：字符级 n-gram 语义 Embedding**
（`CharSemanticEmbedding`，纯 numpy，零外部依赖，512 维）。

为什么不用现成的向量模型：
- BGE-M3 之类的模型要下 2GB 权重，本项目磁盘受限（详见 requirements.txt）
- 调远程 embedding API 要花钱，而法规检索的查询量并不高
- 中文 bigram/trigram 本身就是语义单位，配合 TF-IDF 加权
  对「立杆间距」「超规模」这类固定术语的区分度足够

代价与应对：字符向量对**同义改写**（「不能」vs「不得」）的泛化能力弱于
真实语义模型，因此检索采用「字符向量 + BM25」**双路 RRF 融合互补**，
而不是用字符向量替代关键词路。

若要切换到 OpenAI 兼容的向量接口，把 `EMBEDDING_PROVIDER` 设为 `openai`
即可（`EmbeddingFactory` 会读取该配置，无需改代码），
但**必须全量重建向量库**，否则新旧语义空间混存会导致检索失真。
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections import Counter

import numpy as np

# 中文停用字 + 标点
STOP_CHARS = set("的了和是在有与及对为上下中请问什么怎么如何哪些要求规定规范标准的地得着过和或")

# 建筑危大工程领域同义扩展表。
# 每项为「触发词 -> 同义特征」，命中触发词时注入同义特征，
# 使「爆破」与「拆除工程」、「危大」与「危险性较大」这类表述能够互相召回。
#
# 刻意不把「隧道」「洞室」列为 underground 的触发词：
# JGJ 80-2016 工具式模板工程里就有「隧道模」，纯关键词匹配会把它
# 误判成暗挖工程，导致跨领域误召回。
# 场景归属改由 knowledge_entries.category 字段提供（数据源可信）。
_DOMAIN_SYNONYMS: dict[str, str] = {
    "爆破": "demolition_explosion",
    "拆除": "demolition_explosion",
    "危大": "hazardous_work",
    "危险性较大": "hazardous_work",
    "超规模": "overscale_expert",
    "超过一定规模": "overscale_expert",
    "专家论证": "overscale_expert",
    "基坑": "foundation_pit",
    "深基坑": "foundation_pit",
    "模板": "formwork",
    "支撑": "formwork_shoring",
    "脚手架": "scaffold",
    "悬挑": "cantilever_scaffold",
    "起重": "lifting",
    "吊装": "lifting",
    "暗挖": "underground_mining",
    "验收": "acceptance",
    "监测": "monitoring",
    "审批": "approval",
    "签字": "approval_signature",
}


class BaseEmbedding(ABC):
    """Embedding 抽象基类。"""

    dimension: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。"""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """单条查询向量化。"""


class CharSemanticEmbedding(BaseEmbedding):
    """字符 n-gram 语义 Embedding。

    原理：中文没有空格分词，词级语义向量依赖大模型。
    但法规检索有个特点——**专业术语高度固定**（「立杆间距」「超规模」
    这类词反复出现），字符级特征已能有效区分语义。

    做法：
    1. 提取 1/2/3-gram 字符特征（中文 bigram/trigram 是语义单位）
    2. TF 加权 + IDF 抑制常见字
    3. L2 归一化后点积即余弦相似度

    优势：零依赖、毫秒级、可解释、离线可用。
    局限：同义改写（如「不能」vs「不得」）的泛化能力弱于真实语义模型，
    因此本项目采用「字符向量 + BM25」双路互补，而非替代。
    """

    dimension = 512

    def __init__(self, dimension: int = 512):
        self.dimension = dimension
        self._idf: dict[str, float] = {}
        self._doc_freq: Counter = Counter()
        self._total_docs = 0
        self._fitted = False

    def _features(self, text: str) -> Counter:
        """提取字符 n-gram 特征。

        除字符 n-gram 外，额外注入领域同义扩展——
        建筑领域大量使用术语变体（「爆破」vs「拆除工程」、
        「危大」vs「危险性较大」），纯字符特征无法建立这些联系。
        """
        t = re.sub(r"\s+", "", text.lower())
        feats: Counter = Counter()
        for n in (2, 3):
            for i in range(len(t) - n + 1):
                gram = t[i : i + n]
                if gram in STOP_CHARS and n == 2:
                    continue
                feats[f"{n}:{gram}"] += 1
        # 单字只保留非停用字，用于兜底
        for ch in t:
            if ch not in STOP_CHARS:
                feats[f"1:{ch}"] += 1

        # 领域同义扩展：命中即额外注入同义特征，降低措辞差异造成的漏召回
        for token, expansion in _DOMAIN_SYNONYMS.items():
            if token in text:
                feats[f"syn:{expansion}"] += 2
        return feats

    def fit(self, corpus: list[str]) -> "CharSemanticEmbedding":
        """统计 IDF。必须先 fit 再 embed，否则 idf 全为 1。"""
        self._doc_freq = Counter()
        self._total_docs = len(corpus)
        for text in corpus:
            self._doc_freq.update(set(self._features(text).keys()))
        self._idf = {
            gram: math.log((self._total_docs + 1) / (df + 1)) + 1.0
            for gram, df in self._doc_freq.items()
        }
        self._fitted = True
        return self

    def _id(self, gram: str) -> int:
        """特征哈希到向量维度。"""
        return int(hashlib.md5(gram.encode()).hexdigest(), 16) % self.dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._fitted:
            self.fit(texts)
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = np.zeros(self.dimension, dtype=np.float32)
        feats = self._features(text)
        if not feats:
            return vec.tolist()
        max_tf = max(feats.values())
        for gram, tf in feats.items():
            idf = self._idf.get(gram, 1.0)
            # 次线性 TF 缩放 + IDF 加权
            weight = (1 + math.log(tf)) / max_tf * idf
            vec[self._id(gram)] += weight
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


class OpenAIEmbedding(BaseEmbedding):
    """OpenAI 兼容 Embedding（DeepSeek / 通义千问 / OpenAI 均可）。

    **默认不启用**：本项目实际跑的是 CharSemanticEmbedding。
    只有把 EMBEDDING_PROVIDER 设为 openai 并配好 API Key 时才会启用。

    ⚠️ 换 Embedding 模型后必须**全量重建向量库**（执行
    `POST /api/v1/vector/reindex`）。新旧模型维度不同、语义空间不同，
    混存会导致检索结果完全失真——`upsert_embedding` 会校验维度，
    但维度相同的模型混存同样有问题，所以务必重建。
    """

    dimension = 1536

    def __init__(self, base_url: str, api_key: str, model: str = "text-embedding-3-large"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": texts},
            timeout=60.0,
        )
        resp.raise_for_status()
        return [d["embedding"] for d in resp.json()["data"]]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class EmbeddingFactory:
    """按配置创建 Embedding 实例。

    这是 EMBEDDING_PROVIDER 唯一的生效入口——所有需要向量化的地方
    都应通过 `EmbeddingFactory.create()` 获取实例，不要直接
    `CharSemanticEmbedding()`，否则配置形同虚设。

    用法：
        emb = EmbeddingFactory.create()          # 读 .env 配置
        emb = EmbeddingFactory.create("char")     # 显式指定
    """

    @staticmethod
    def create(provider: str | None = None, **kwargs) -> BaseEmbedding:
        from app.config import settings

        provider = (provider or settings.EMBEDDING_PROVIDER or "char").lower()

        if provider in ("char", "local_char", "default"):
            # 维度默认取配置（EMBEDDING_DIMENSION=512），
            # 调用方可显式覆盖——向量库要求与 VECTOR 列定义严格一致
            kwargs.setdefault("dimension", settings.EMBEDDING_DIMENSION)
            return CharSemanticEmbedding(**kwargs)

        if provider in ("openai", "remote"):
            # OpenAI 兼容模式下复用 LLM 的 base_url 与 key：
            # 通义千问、DeepSeek 等都提供 /embeddings 端点
            return OpenAIEmbedding(
                base_url=kwargs.get(
                    "base_url", settings.LLM_BASE_URL
                ),
                api_key=kwargs.get("api_key", settings.LLM_API_KEY),
                model=kwargs.get("model", settings.EMBEDDING_MODEL),
            )

        raise ValueError(
            f"未知的 EMBEDDING_PROVIDER: {provider}（可选 char | openai）"
        )


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度。输入已 L2 归一化时等价于点积。"""
    return float(np.dot(a, b))