"""W5 纯文本口径单元测试：归一化 / 意图分类 / 近义聚类 / 回答问句抽取。"""

from __future__ import annotations

from geo_platform.variants import textutil


def test_normalize_strips_punct_whitespace_and_modal_particles() -> None:
    assert textutil.normalize_query(" 上海 律师 推荐吗？ ") == "上海律师推荐"
    assert textutil.normalize_query("ABC 保险怎么样啊！") == "abc保险怎么样"
    assert textutil.normalize_query("重疾险，怎么选呢？") == "重疾险怎么选"
    assert textutil.normalize_query("？？？") == ""


def test_classify_intent_six_buckets() -> None:
    assert textutil.classify_intent("重疾险和医疗险哪个好") == "对比"
    assert textutil.classify_intent("重疾险怎么选") == "选购"
    assert textutil.classify_intent("中意人寿口碑怎么样") == "口碑"
    assert textutil.classify_intent("重疾险适合什么场景") == "场景"
    assert textutil.classify_intent("重疾险推荐有哪些") == "推荐"
    assert textutil.classify_intent("上海哪里有好律师") == "地域"


def test_classify_intent_precedence_region_keyword_beats_region_axis() -> None:
    # 固定优先级：推荐先于地域命中——地域锚定交给 region 轴，意图仍是推荐。
    assert textutil.classify_intent("上海重疾险推荐", regions=("上海",)) == "推荐"
    # 无更强意图关键词时，命中项目地域词 → 地域。
    assert textutil.classify_intent("上海的律师事务所", regions=("上海",)) == "地域"


def test_classify_intent_unclassified_is_honest() -> None:
    assert textutil.classify_intent("保险") == textutil.UNCLASSIFIED
    assert textutil.classify_intent("中意人寿") == textutil.UNCLASSIFIED


def test_cluster_merges_near_duplicates() -> None:
    clusters = textutil.cluster_texts(
        [("重疾险推荐有哪些", 3), ("重疾险推荐有哪些好", 1), ("医疗险怎么选", 2)]
    )
    assert len(clusters) == 2
    merged = next(c for c in clusters if len(c.members) == 2)
    assert merged.representative == "重疾险推荐有哪些"  # 用量最高者为代表
    assert merged.total_count == 4
    assert merged.cluster_id.startswith("clu_")


def test_cluster_merges_normalized_identical_into_one_member() -> None:
    # 归一化后完全相同的文本先合并计数（幂等键同口径），簇成员只留一条。
    clusters = textutil.cluster_texts([("重疾险推荐有哪些", 3), ("重疾险推荐有哪些吗？", 1)])
    assert len(clusters) == 1
    assert clusters[0].members == ("重疾险推荐有哪些",)
    assert clusters[0].total_count == 4


def test_cluster_merges_prefix_extension() -> None:
    clusters = textutil.cluster_texts(
        [("北京人寿保险怎么选择", 1), ("北京人寿保险怎么选", 1)]
    )
    assert len(clusters) == 1
    assert clusters[0].total_count == 2


def test_cluster_never_merges_different_cities() -> None:
    clusters = textutil.cluster_texts([("上海律师推荐", 1), ("北京律师推荐", 1)])
    assert len(clusters) == 2


def test_cluster_threshold_constant() -> None:
    assert textutil.CLUSTER_SIMILARITY_THRESHOLD == 0.75
    assert textutil.jaccard("上海律师推荐", "北京律师推荐") < 0.75


def test_extract_user_questions_positive() -> None:
    answer = (
        "您可以考虑以下几点。重疾险怎么选？首先要看保障范围。\n1. 哪家保险公司靠谱？这是常见疑问。"
    )
    questions = textutil.extract_user_questions(answer)
    assert "重疾险怎么选？" in questions
    assert "哪家保险公司靠谱？" in questions


def test_extract_user_questions_negative_answer_tone() -> None:
    answer = "这是一款很好的产品，值得购买。保障范围非常全面，性价比很高。"
    assert textutil.extract_user_questions(answer) == []


def test_extract_user_questions_length_filter() -> None:
    long_question = "那么" + "非常" * 40 + "长的问题是什么呢？"
    assert textutil.extract_user_questions(long_question) == []
    assert textutil.extract_user_questions("好吗？") == []  # 低于最小长度


def test_extract_user_questions_dedupes_by_normalized() -> None:
    answer = "重疾险怎么选？另一方面，重疾险怎么选吗？"
    assert textutil.extract_user_questions(answer) == ["重疾险怎么选？"]
