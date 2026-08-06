"""使用旧 spec + 新 prompt 重新跑一次 character_factory，对照输出质量。"""

import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

# 展开 .env 中的变量引用 ($VAR / ${VAR})
for key in list(os.environ):
    val = os.environ[key]
    if val and ("$" in val):
        os.environ[key] = os.path.expandvars(val)

from app.agent_factory import get_character_factory_agent
from repository.sdk_runner import run_structured_agent
from app.llm_schema import LLMNewCharacterProfile, LLMNewCharacterRequest
from repository.llm.config import get_llm_config
from repository.config import AGENT_RUN_TIMEOUT_SECONDS

# 与原始 trace 完全一致的输入
USER_INPUT = """<spec>
background_hint: 北原悠的姐姐，比悠年长几岁，在同城上大学。接到电话后语气带着担心但克制，平时独立、很少让别人看出自己慌了。
name_hint: 北原栞
initial_location: 正在前往综合医院的路上
</spec>

<story_setting>
<world>
故事发生在现代日本的私立高中「城川学园」。

城川学园位于东京近郊，是一所升学率很高、社团活动也很强的学校。学生大多来自普通家庭和中产家庭，学校表面上强调「自由、自主、青春」，但实际上非常重视成绩、社团成果和对外形象。

这个世界的基本规则是：
1. 学生的评价不仅看成绩，也看社团表现、班级贡献和老师印象。
2. 每个学生都必须参加至少一个社团，社团活动会影响推荐入学和奖学金。
3. 学校每年有几次大型活动，班级和社团会因此形成强烈竞争。
4. 学生之间的恋爱不被禁止，但如果影响成绩、社团或学校形象，就会被老师介入。
5. 学校里有一些默认的等级秩序：成绩好的人、社团王牌、学生会成员、受欢迎的人，天然拥有更多话语权。

这个社会最重视「努力、合群、不给别人添麻烦」，最忌讳「破坏集体气氛」「拖累团队」「把私人情绪带进公共场合」。

普通人的日常生活是：
早上乘电车上学，进教室后和熟人寒暄，上课、午休、社团、补习或打工。放学后的教室、屋顶、社团活动室、车站前便利店、河边步道，是学生们最常发生关系变化的地方。

学校里的主要校园活动包括：
1. 四月入学式：新生进入学校，社团开始招新，很多关系从这里开始。
2. 五月体育祭：班级之间竞争激烈，擅长运动的人会突然变得耀眼。
3. 七月期末考试：成绩压力上升，补习、约定、互相帮忙会让角色关系变近。
4. 八月暑期合宿：社团集中训练，也是很多秘密和冲突爆发的时期。
5. 九月文化祭：每个班级要准备节目，比如女仆咖啡厅、鬼屋、舞台剧、乐队演出。文化祭是学校最重要的活动，也是恋爱、告白、误会和和解最容易发生的时候。
6. 十月修学旅行：学生离开日常环境，关系会被重新洗牌。
7. 十二月圣诞活动：学生会举办校内点灯仪式，社团和班级会组织小型聚会。
8. 二月情人节：巧克力会让暧昧关系变得明显，也会让被隐藏的感情暴露。
9. 三月毕业式：前辈离开学校，留下的人必须面对关系变化和自己的未来。

这个世界的核心矛盾是：
学校鼓励学生拥有「闪闪发光的青春」，但每个人都必须在成绩、社团、家庭期待、人际关系和自我形象之间维持平衡。

它对角色的影响是：
角色不能只按照自己的心情行动。喜欢一个人、讨厌一个人、想退出社团、想逃避比赛、想说出真心，都必须考虑班级气氛、社团责任、老师评价和未来前途。很多冲突不是来自坏人，而是来自「大家都知道应该这样做」的压力。
</world>
</story_setting>

<world_now>
当前时间：4月5日 15:40
当前场景：保健室
</world_now>"""


async def main():
    print("=" * 60)
    print("使用更新后的 prompt 重新跑 character_factory")
    print("=" * 60)
    print()
    print("--- 输入 spec ---")
    print(USER_INPUT)
    print()

    config = get_llm_config()
    agent = get_character_factory_agent()

    try:
        result = await run_structured_agent(
            agent=agent,
            user_input=USER_INPUT,
            output_type=LLMNewCharacterProfile,
            timeout_seconds=AGENT_RUN_TIMEOUT_SECONDS,
            workflow_name="test_character_factory",
            trace_metadata={"agent_name": "character_factory_test", "target": "北原栞"},
            usage_agent="character_factory_test",
            usage_phase="test",
            model_name=config["model_id"],
        )
    except Exception as e:
        print(f"出错: {e}")
        return

    print("--- 输出 ---")
    print(f"character_id: {result.character_id}")
    print(f"display_name: {result.display_name}")
    print()
    print(f"--- identity ---\n{result.identity}")
    print()
    print(f"--- goal ---\n{result.goal}")
    print()
    print(f"--- dynamic ---\n{result.dynamic}")
    print()
    print("--- behavior ---")
    for b in result.behavior:
        print(f"  - {b}")
    print()
    print("--- voice ---")
    for v in result.voice:
        print(f'  "{v}"')
    print()
    print("--- initial_status ---")
    print(json.dumps(result.initial_status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
