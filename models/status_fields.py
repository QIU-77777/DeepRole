"""status.md 的已知语义字段名常量。

status.md 字段仍是文件驱动 / 作者可自定义（白名单现读 ## 标题）；这里只收拢
代码里频繁引用的语义字段名，避免散落魔法字符串。不构成 schema，不做结构化建模。
"""

# 角色
IDENTITY = "身份"
MOOD = "心境"
CONCERN = "在意的事"
PLANS = "打算"  # 队列段：存于 intents.json，渲染回此标题供 prompt 注入

# 角色 + narrator 共有
PLAYER_RELATION = "和玩家的关系"

# narrator
CURRENT_TIME = "当前时间"
SCENE = "场景"
CHARACTER_LOCATIONS = "角色位置"
NARRATIVE_FOCUS = "叙事焦点"
RECENT_WORLD_EVENT = "最近世界事件"
PENDING_EVENTS = "待触发事件"  # 队列段：存于 pending_events.json

# state_updater 评估剧情时读取的角色散文字段顺序（打算另从队列渲染）
CHARACTER_STATUS_FIELDS = (IDENTITY, MOOD, CONCERN)
