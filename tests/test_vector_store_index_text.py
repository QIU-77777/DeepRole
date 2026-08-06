"""测试长期记忆索引用文本的字段组合。"""

from models import EpisodeMemory, Understanding
from repository.vector_store import VectorStore


def test_embed_text_includes_title_keywords_and_content():
    episode = EpisodeMemory(
        title="亲昵称呼约定",
        date="10月19日",
        time="放学后",
        location="旧阅览室",
        participants="我、玩家",
        keywords=["小狗", "主人", "亲密称呼"],
        content="他叫我小狗，我叫他主人，这个称呼让互动变得更亲昵。",
        memory_owner="mitsuki",
    )

    embed_text = VectorStore._embed_text(episode)

    assert "亲昵称呼约定" in embed_text
    assert "日期：10月19日" in embed_text
    assert "时间：放学后" in embed_text
    assert "地点：旧阅览室" in embed_text
    assert "在场：我、玩家" in embed_text
    assert "小狗、主人、亲密称呼" in embed_text
    assert "他叫我小狗" in embed_text


def test_understanding_embed_text_includes_subject_keywords_and_content():
    understanding = Understanding(
        subject="对玩家的认知",
        keywords=["玩家", "保护欲"],
        content="玩家在压力下会先确认她是否安全。",
    )

    embed_text = VectorStore._understanding_embed_text(understanding)

    assert "对玩家的认知" in embed_text
    assert "玩家、保护欲" in embed_text
    assert "玩家在压力下" in embed_text
