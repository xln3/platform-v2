"""domain.brandrank.adapter：analytics.answer/citation_fact 行 → 她的 brand_list/信源记录。"""
from domain.brandrank import adapter


def _answer(**kw):
    base = {
        "pub_id": "ans_x", "model": "doubao", "query_text": "保险公司推荐",
        "region": "北京", "mode": "normal", "response_text": "正文",
    }
    base.update(kw)
    return base


# ── mode 词表映射（V2 采集取值：normal|deep_think，与旧库一致）────────────────
def test_mode_label_mapping():
    assert adapter.mode_label("normal") == "快速"
    assert adapter.mode_label("deep_think") == "思考"
    assert adapter.mode_label("future_mode") == "future_mode"   # 未知原样透出，不静默归类
    assert adapter.mode_label("") == ""


# ── rec_type 沿用她的文件名规则（作用于 query 文本）─────────────────────
def test_rec_type_from_query():
    assert adapter.rec_type_from_query("人寿保险产品推荐") == "产品"
    assert adapter.rec_type_from_query("靠谱保险公司有哪些") == "公司"
    assert adapter.rec_type_from_query("产品与公司都要") == "产品"     # 她的 if/elif 顺序：产品优先
    assert adapter.rec_type_from_query("今天天气如何") is None
    assert adapter.rec_type_from_query("") is None


# ── answer → 她的 brand_list 记录 ─────────────────────────────────
def test_answer_to_brand_record_shape():
    rec = adapter.answer_to_brand_record(
        _answer(mode="deep_think", region="上海", query_text="保险产品排行",
                model="deepseek"), ["中意人寿", "中国平安"])
    assert rec["brands"] == ["中意人寿", "中国平安"]
    assert rec["query"] == "保险产品排行"
    assert rec["thinking_mode"] == "思考"                        # deep_think→思考
    assert rec["mode_raw"] == "deep_think"                       # 原值留痕
    assert rec["ip"] == "上海"
    assert rec["rec_type"] == "产品"
    assert rec["engine"] == "deepseek"
    assert rec["answer_pub_id"] == "ans_x"


def test_answer_to_brand_record_empty_region_not_defaulted():
    """region='' 如实 ''——绝不套用她数据专属的缺省'北京'。"""
    rec = adapter.answer_to_brand_record(_answer(region=""), [])
    assert rec["ip"] == ""


# ── citation_fact → 信源记录（V2 无 sitename 列：host 归一化兜底链）─────────
def test_citation_to_source_entry_host_normalized():
    row = {"ordinal": 2, "host": "www.Zhihu.com",
           "canonical_url": "https://www.zhihu.com/a", "original_url": "https://www.zhihu.com/a"}
    out = adapter.citation_to_source_entry(row)
    assert out == {"sitename": "zhihu.com", "url": "https://www.zhihu.com/a", "index": 2}


def test_citation_to_source_entry_url_host_fallback():
    """host 缺失 → canonical_url 解析主机名；再缺 → original_url；三无 → （未知）。"""
    row = {"ordinal": 1, "host": "",
           "canonical_url": "https://www.163.com/c", "original_url": ""}
    assert adapter.citation_to_source_entry(row)["sitename"] == "163.com"
    row2 = {"ordinal": 3, "host": None,
            "canonical_url": "", "original_url": "https://baijiahao.baidu.com/s"}
    out2 = adapter.citation_to_source_entry(row2)
    assert out2["sitename"] == "baijiahao.baidu.com"
    assert out2["url"] == "https://baijiahao.baidu.com/s"
    row3 = {"ordinal": 4, "host": "", "canonical_url": "", "original_url": ""}
    assert adapter.citation_to_source_entry(row3)["sitename"] == adapter.UNKNOWN_SITENAME


def test_citation_to_source_entry_bad_ordinal_defensive():
    """ordinal 非正整数 → index 兜底 1（citation_fact.ordinal 恒 1-based，此分支纯防御）。"""
    row = {"ordinal": 0, "host": "zhihu.com", "canonical_url": "https://zhihu.com/a",
           "original_url": ""}
    assert adapter.citation_to_source_entry(row)["index"] == 1
    row2 = {"ordinal": None, "host": "zhihu.com", "canonical_url": "", "original_url": ""}
    assert adapter.citation_to_source_entry(row2)["index"] == 1
