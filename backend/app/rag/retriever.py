"""混合检索：BM25 关键词路 + 向量语义路，再融合重排。

为什么不用单路：
- BM25 擅长精确条款号（「JGJ 80 第 3.1.2 条」这类字面命中），但对口语化提问无能为力；
- 向量检索擅长语义近似（「模板支撑立杆间距该定多少」能召回「立杆间距不小于…」），
  但对数字、条款号这类精确符号容易漂移。

合规审查场景两者都不可缺，因此走双路召回 + RRF 融合。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from app.config import settings
from app.rag.chunker import TextChunk
from app.rag.embedding import CharSemanticEmbedding, _DOMAIN_SYNONYMS as _SYNONYMS

# 中文停用字（检索场景下精简版，够用且避免引入大依赖）
STOPWORDS = {
    "的", "了", "和", "是", "在", "有", "与", "及", "对", "为", "上", "下", "中",
    "请问", "什么", "怎么", "如何", "哪些", "要求", "规定", "规范", "标准",
    "the", "a", "an", "is", "of", "to", "and", "or",
}

# 把查询拆成检索单元：条款号 / 书名号里的规范名 / 其余语义词
CLAUSE_QUERY_PATTERN = re.compile(r"第\s*\d+(?:\.\d+)*\s*条")
DOC_NAME_PATTERN = re.compile(r"《([^》]+)》")
TOKEN_PATTERN = re.compile(r"[一-鿿]+|[A-Za-z][A-Za-z0-9\-]*|\d+(?:\.\d+)*")


@dataclass
class ScoredChunk:
    """召回结果。保留各路原始分数，便于排查「是哪一路命中的」。"""

    chunk: TextChunk
    vector_score: float = 0.0
    bm25_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def final_score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.fused_score

    def to_citation_dict(self) -> dict:
        c = self.chunk
        return {
            "doc_id": c.doc_id,
            "doc_name": c.doc_name,
            "version": c.version,
            "clause_no": c.clause_no,
            "section": c.section,
            "is_mandatory": c.is_mandatory,
            "citation": c.citation,
            "content": c.content,
            "score": round(self.final_score, 4),
        }


def _index_text(chunk: TextChunk) -> str:
    """构造 BM25 索引文本。

    **不只用正文**：条款号与领域标签也是强信号，必须一起进索引。

    实测踩过的坑：`附件2-暗挖工程` 正文只有 26 字
    （「采用矿山法、盾构法、顶管法施工的隧道、洞室工程。」），
    查询「暗挖工程需要专家论证的情形」里的高频词「专家论证」
    在 37 号令多处出现，BM25 被其主导，该条款**直接被挤出 Top30 候选池**——
    而规则加权只能重排已进入候选池的文档，权重再高也救不回来。

    条款号里的「暗挖」「模板」「脚手架」正是查询中最常出现的领域词，
    纳入索引等于给短条款补上了关键词路缺失的竞争力。
    """
    parts = [chunk.content or ""]
    if chunk.clause_no:
        parts.append(chunk.clause_no)
    extra = chunk.extra or {}
    if extra.get("category"):
        parts.append(str(extra["category"]))
    return " ".join(parts)


def tokenize(text: str) -> list[str]:
    """中文按字 bigram + 英文数字按词切分，兼顾中文无空格场景。

    同时注入建筑领域同义扩展词，让「爆破」与「拆除工程」这类
    表述差异在关键词路也能互相召回。
    """
    text = text.lower()
    tokens: list[str] = []
    for match in TOKEN_PATTERN.finditer(text):
        tok = match.group()
        if tok in STOPWORDS:
            continue
        if re.fullmatch(r"[一-鿿]+", tok):
            # 中文单字 + 相邻 bigram
            tokens.append(tok)
            for i in range(len(tok) - 1):
                tokens.append(tok[i : i + 2])
        else:
            tokens.append(tok)

    for trigger, expansion in _SYNONYMS.items():
        if trigger in text:
            tokens.append(expansion)
    return tokens


class BM25Index:
    """轻量 BM25 倒排索引。

    没有引入 rank_bm25 之类的库：规范语料规模有限（万级条款），
    内存索引足够，且能直接控制分词器以适配中文。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._tokens: list[list[str]] = []
        self._chunks: list[TextChunk] = []
        self._df: Counter = Counter()
        self._avg_len: float = 0.0

    def build(self, chunks: Iterable[TextChunk]) -> "BM25Index":
        self._tokens, self._chunks = [], []
        self._df = Counter()
        for chunk in chunks:
            toks = tokenize(_index_text(chunk))
            self._tokens.append(toks)
            self._chunks.append(chunk)
            self._df.update(set(toks))
        lens = [len(t) for t in self._tokens] or [0]
        self._avg_len = sum(lens) / len(lens)
        return self

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        if not self._tokens:
            return []
        n = len(self._tokens)
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        scores: dict[int, float] = {}
        for idx, toks in enumerate(self._tokens):
            tf = Counter(toks)
            dl = len(toks) or 1
            score = 0.0
            for tok in set(q_tokens):
                f = tf.get(tok, 0)
                if f == 0:
                    continue
                df = self._df.get(tok, 0)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = f + self.k1 * (1 - self.b + self.b * dl / max(self._avg_len, 1e-6))
                score += idf * (f * (self.k1 + 1)) / denom
            if score > 0:
                scores[idx] = score

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]

    @property
    def chunks(self) -> list[TextChunk]:
        return self._chunks


class HybridRetriever:
    """双路召回 + RRF 融合。vector_hits 由外部向量检索器注入。

    现在内置向量路：字符语义 Embedding 提供语义召回，
    与 BM25 关键词路互补（JD RAG 完整链路 63% 的核心要求）。
    """

    # RRF 平滑常数，经验值 60，无需调整
    RRF_K = 60

    # 双路权重走配置：向量路略高，因为字符 n-gram 提供了
    # BM25 拿不到的语义近似召回。调参不必改代码。
    W_BM25 = settings.HYBRID_W_BM25
    W_VECTOR = settings.HYBRID_W_VECTOR

    def __init__(
        self,
        bm25: BM25Index,
        all_chunks: list[TextChunk],
        embedding=None,
        use_vector: bool = True,
    ):
        self.bm25 = bm25
        self.all_chunks = all_chunks
        self._matrix: np.ndarray | None = None

        # embedding 为 None 时默认使用内置字符语义向量路；
        # use_vector=False 可显式关闭，退化为纯 BM25。
        self.use_vector = use_vector
        if self.use_vector:
            emb = embedding or CharSemanticEmbedding()
            # 必须先在语料上 fit，让 IDF 有统计依据
            if hasattr(emb, "fit"):
                emb.fit([c.content for c in all_chunks])
            vectors = emb.embed([c.content for c in all_chunks])
            self._matrix = np.asarray(vectors, dtype=np.float32)
            self._emb = emb

    def _vector_search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """余弦相似度检索（向量已归一化，点积即余弦）。"""
        if self._matrix is None or len(self._matrix) == 0:
            return []
        qv = np.asarray(self._emb.embed_query(query), dtype=np.float32)
        scores = self._matrix @ qv
        idx = np.argsort(-scores)[:top_k]
        return [(int(i), float(scores[i])) for i in idx if scores[i] > 0]

    def retrieve(
        self,
        query: str,
        top_k: int = 20,
        vector_hits: list[tuple[int, float]] | None = None,
    ) -> list[ScoredChunk]:
        """vector_hits: [(chunk下标, 相似度)]，为 None 时自动用内置向量路。"""
        bm25_hits = self.bm25.search(query, top_k=top_k)
        if vector_hits is None and self.use_vector:
            vector_hits = self._vector_search(query, top_k)
        vector_hits = vector_hits or []

        pool: dict[int, ScoredChunk] = {}
        for rank, (idx, score) in enumerate(bm25_hits):
            item = pool.setdefault(idx, ScoredChunk(chunk=self.all_chunks[idx]))
            item.bm25_score = score
            item.extra["bm25_rank"] = rank

        for rank, (idx, score) in enumerate(vector_hits):
            item = pool.setdefault(idx, ScoredChunk(chunk=self.all_chunks[idx]))
            item.vector_score = score
            item.extra["vector_rank"] = rank

        # RRF：只看排名，天然消除两路分数量纲差异
        # 权重反映实际互补性：BM25 擅长精确条款号，向量路擅长语义近似改写。
        # 语义路权重略高，因为关键词路在规范检索上已足够强。
        for idx, item in pool.items():
            rr = 0.0
            if "bm25_rank" in item.extra:
                rr += self.W_BM25 / (self.RRF_K + item.extra["bm25_rank"] + 1)
            if "vector_rank" in item.extra:
                rr += self.W_VECTOR / (self.RRF_K + item.extra["vector_rank"] + 1)
            item.fused_score = rr

        results = sorted(pool.values(), key=lambda x: x.fused_score, reverse=True)
        return results[:top_k]


def extract_clauses(query: str) -> list[str]:
    """从提问里抽出条款号，用于强制召回——合规审查里用户常直接报条号。"""
    return [re.sub(r"\s+", "", m) for m in CLAUSE_QUERY_PATTERN.findall(query)]


def extract_doc_names(query: str) -> list[str]:
    return DOC_NAME_PATTERN.findall(query)


# 领域限定词 → 该领域的标志词。
#
# 为什么需要这一层：查询「暗挖工程需要专家论证的情形」时，
# 「专家论证」是泛化词，会把第12/13 条这类程序性条款一起召回，
# 而真正要的是「暗挖工程属于超规模」那一条（建办质31号第十项）。
# 结果是目标条款被挤到第 7 名——检索到了"专家论证"，却没检索到"暗挖"。
#
# 合规审查的查询几乎都带场景限定（「模板立杆间距」「基坑监测」），
# 不区分场景的排序在真实使用中会持续给出"看似相关但答非所问"的结果。
DOMAIN_SCENARIOS: dict[str, list[str]] = {
    "foundation_pit": ["基坑", "深基坑", "土方开挖", "边坡", "支护", "降水"],
    "formwork": ["模板", "支撑体系", "满堂支撑", "立杆", "剪刀撑"],
    "lifting": ["塔吊", "起重", "吊装", "安装拆卸"],
    "scaffold": ["脚手架", "悬挑架", "爬架", "连墙件", "落地式"],
    "demolition": ["拆除", "爆破", "文物"],
    "underground": ["暗挖", "隧道", "矿山法", "盾构", "顶管", "洞室", "围岩"],
}


def extract_scenes(query: str) -> list[str]:
    """识别查询中的领域限定词。"""
    found = []
    for scene, markers in DOMAIN_SCENARIOS.items():
        if any(m in query for m in markers):
            found.append(scene)
    return found


# ---------------------------------------------------------------------------
# 规模档位信号（建办质〔2018〕31号 附件1 / 附件2）
#
# 为什么要单独识别：两档的数值阈值差很多（基坑 3m vs 5m、模板 5m vs 8m、
# 吊装 10kN vs 100kN），拿错档位会让审查结论直接反向。
# 而字符 n-gram 分不出这层语义——「超过一定规模的危大工程」与
# 「危大工程范围」在字符层面几乎无差别（实测分差仅 0.0001），
# 只能靠确定性规则补上。
# ---------------------------------------------------------------------------

# 查询里出现这些词，说明用户在问「超规模」这一档
OVER_SCALE_MARKERS = [
    "超过一定规模", "超规模", "一定规模", "什么规模", "多大",
    "需要专家论证", "需不需要专家论证", "要不要专家论证", "专家论证", "论证",
    "规模以上",
    # 「深基坑」本身就是超规模的官方用词（建办质31号附件2 用「深基坑工程」）
    "深基坑",
]

# 否定语境：出现这些词说明用户其实在问「未达到超规模」的情况。
# 必须**先于**正向标记判定——「不需要专家论证」里含「专家论证」，
# 若先判正向会得出完全相反的结论（实测踩过）。
NEGATION_MARKERS = [
    "不需要专家论证", "无需专家论证", "不必论证", "不用论证",
    "未超过", "没超过", "不超过", "未达到", "没达到", "低于",
    "不超", "算不算超", "是否超",
]

# 出现这些词，说明用户在问普通危大工程这一档
NORMAL_SCALE_MARKERS = [
    "属于危大工程", "是危大工程", "危大工程范围", "属于危险性较大",
    "是否属于危大", "算不算危大",
]


def extract_scale_intent(query: str) -> str | None:
    """判断查询关心哪一档规模。

    返回 "over" / "normal" / None（未提及规模，不做倾向性加权）。

    **判定顺序至关重要**：否定语境必须最先判。
    「不需要专家论证」这句里含有「专家论证」，若先做正向匹配
    会把否定句判成「问超规模」，进而给出去掉降权的结果——
    在合规审查里这是结论反向的错误，不能靠调权重补。
    """
    # 1) 否定优先
    if any(neg in query for neg in NEGATION_MARKERS):
        return "normal"

    # 2) 超规模正向标记
    if any(m in query for m in OVER_SCALE_MARKERS):
        return "over"

    # 3) 普通危大标记
    if any(m in query for m in NORMAL_SCALE_MARKERS):
        return "normal"

    return None


def _chunk_is_over_scale(chunk: TextChunk) -> bool | None:
    """条款是否属于超规模档位。

    只认**结构化标记**，不做正文推断：
    1. 入库时标注的 is_over_scale
    2. 条款号形如「附件2-xxx」/「附件1-xxx」

    **刻意不按正文数值推断**：实测「第12条」这类 37 号令的程序性条款里
    也可能出现「50m」「100kN」等数字（专家库人数、监测报告要求等），
    会被误判成超规模条款并被加权，反而挤掉真正的答案——
    实测导致 Recall@5 从 95.83%掉到 83.33%。
    宁可返回 None（不做倾向性加权），也不能猜错档位。

    返回 None 表示「该条款与规模档位无关」，调用方应跳过加权而非当成 False。
    """
    extra = chunk.extra or {}

    if extra.get("is_over_scale") is not None:
        return bool(extra["is_over_scale"])

    clause = chunk.clause_no or ""
    if "附件2" in clause:
        return True
    if "附件1" in clause:
        return False

    return None


def _chunk_scenes(chunk: TextChunk) -> set[str]:
    """条款所属领域。

    优先用 category 字段（入库时已标注），退化到正文关键词匹配——
    内存索引场景下没有 category，需要靠文本判断。

    注意：TextChunk 没有 title 字段，只能用 doc_name + content。
    """
    cat = chunk.extra.get("category") if chunk.extra else None
    if cat:
        return {cat}
    text = f"{chunk.content} {chunk.doc_name or ''}"
    return {s for s, ms in DOMAIN_SCENARIOS.items() if any(m in text for m in ms)}


def rule_based_boost(query: str, items: list[ScoredChunk]) -> list[ScoredChunk]:
    """确定性规则加权。

    命中四类信号时提升排序：
    1. 提问含条款号 → 该条款的块直接置顶（避免语义漂移到相近条文）
    2. 提问点名规范名 → 同文档块加权
    3. 提问含领域限定词 → 同领域块加权，不同领域的降权
    4. 强制性条文 → 略微加权（合规审查里漏检成本高于误报）

    第3 条是实测 Recall@5从 91.67% 提到 95%+ 的关键：
    没有它，「暗挖工程专家论证」会召回一堆泛泛的论证程序条款。
    """
    clauses = extract_clauses(query)
    doc_names = extract_doc_names(query)
    scenes = extract_scenes(query)
    scale_intent = extract_scale_intent(query)

    # 条款号是最强的信号，命中即可短路
    if clauses:
        for item in items:
            if item.chunk.clause_no in clauses:
                item.fused_score += 1.5
                item.extra["rule_boost"] = 1.5

    if not clauses and not doc_names and not scenes and not scale_intent:
        return items

    for item in items:
        boost = 0.0

        if clauses and item.chunk.clause_no in clauses:
            boost += 1.0
        if doc_names and any(d in item.chunk.doc_name for d in doc_names):
            boost += 0.3

        # 领域一致性：命中强加权，冲突降权
        #
        # 负向权重的意义：宁可让"领域不对"的条款掉出 Top5，
        # 也不要让它挤占名额——用户问暗挖，给出脚手架条款是错的，
        # 少给一条只是让人多问一句。
        #
        # +0.8 而非 +0.4，是因为实测发现：查询同时含泛化词（"专家论证"）
        # 和场景词（"暗挖"）时，泛化词会主导 BM25 排序，
        # 弱加权不足以把场景条款拉进 Top5（实测目标条款排在第 7 名）。
        if scenes:
            chunk_scene = _chunk_scenes(item.chunk)
            if chunk_scene & set(scenes):
                boost += 0.8
            elif chunk_scene:
                boost -= 0.5

        # 规模档位一致性（建办质〔2018〕31号 附件1 vs 附件2）
        #
        # **只加权、不降权**——这是被评测数据逼出来的结论：
        # 初版对错档位条款给 -1.0 降权，实测 Recall@5 从 95.83% 掉到 91.67%、
        # MRR 从 80.21% 掉到 66.81%。原因是很多查询同时需要两档条款
        # （例如「模板工程什么情况下属于超规模」既要超规模阈值、
        # 也可能引用普通档的适用范围），一压就整条掉出 Top5。
        #
        # 负向加权的代价不对称：漏掉一条次要条款只是多问一句，
        # 但把唯一正确的条款压出去会让 Agent 拿错阈值直接给出错误结论。
        #
        # 正向权重给 1.0：错档位条款的原始分本就与正确条款接近
        # （实测分差仅 0.0001 量级），弱于 1.0 拉不动排序。
        if scale_intent:
            chunk_scale = _chunk_is_over_scale(item.chunk)
            if chunk_scale is True and scale_intent == "over":
                boost += 1.0
            elif chunk_scale is False and scale_intent == "normal":
                boost += 0.6

        if item.chunk.is_mandatory:
            boost += 0.1

        if boost:
            item.fused_score += boost
            item.extra["rule_boost"] = round(item.extra.get("rule_boost", 0) + boost, 3)

    return sorted(items, key=lambda x: x.fused_score, reverse=True)