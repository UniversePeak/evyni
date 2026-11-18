# Evyni

Evyni 是一个可以自己部署、自己定义角色的 AI 伙伴。我们追求极致的拟人化。你说“面试挪到周六了”，它应该改掉周五的约定。你刚发来消息，它可以顺着这句话聊，把原本准备好的问候留到以后。朋友分享好消息时，一句带笑意的语音，有时比一段工整的文字更合适。

v0.1 提供本地对话、飞书私聊、可编辑记忆、跨重启提醒、主动关心和可选语音。模型由部署者选择。Evyni 有自己的运行入口、对话循环、调度器和 SQLite 存储。项目源于在 Hermes 上做陪伴 Agent 的实践，本版运行时代码独立实现，安装只需 Python 3.11 或更高版本。

## 开始聊天

在仓库目录运行以下命令。建议使用独立 Python 虚拟环境。

```sh
python -m pip install .
evyni init
evyni chat
```

`init` 会打印角色文件的位置。用自己的话写下它怎么称呼你、说话有多简短、你喜欢怎样被关心。下一轮对话就会读取修改。默认角色使用中文，也会跟随用户的语言。

自然对话需要支持 Chat Completions 和 JSON 对象输出的模型服务。启动前设置以下环境变量，例如在 PowerShell 里：

```powershell
$env:EVYNI_BASE_URL = "http://localhost:8000/v1"
$env:EVYNI_MODEL = "your-served-model"
# 服务需要认证时设置 EVYNI_API_KEY
evyni chat
```

Linux 和 macOS 用 `export EVYNI_MODEL=...` 等方式设置相同变量。没有模型也可以在聊天窗口直接试以下命令，将示例日期换成将来的时间。时间默认按运行机器的本地时区解释，也支持带 UTC 偏移的 ISO 时间。

```text
/remember 称呼=阿青
/memory
/remind 2030-08-09T17:30 | 今天面试怎么样？
/agenda
/move <上一步显示的编号> 2030-08-10T17:30
/quit
```

重新运行 `evyni chat` 并输入 `/agenda`，可以看到更新后的同一项约定。接入模型后，这些操作也可以在自然对话里完成。模型每轮可提出一次记忆、约定或偏好更改，运行时保存成功后才会发出确认。

## 记忆、时机和声音

| 能力 | 用户可以怎么用 |
|---|---|
| 记忆 | 用 `/memory` 查看，`/remember 名称=新内容` 更正，`/forget 名称` 移除。每轮按词语匹配度和更新时间选取记忆，并结合最近对话。`/why` 显示上一轮提供给模型的记忆名称。 |
| 约定 | 提醒有稳定编号，可改期、取消。`/agenda` 列出时间、原因和投递状态。运行对应渠道时会补发已到期的明确提醒。 |
| 主动关心 | `/initiative on` 后，伙伴可以针对聊过的具体事件安排后续问候。默认安静时段为本地 22 点至次日 8 点，用户刚说话的 10 分钟内会暂缓，两次主动关心至少相隔 6 小时。超时一天的关心会过期。`/initiative off` 会撤销待发的主动关心。明确约好的提醒按约定时间处理。 |
| 声音 | `/voice on` 允许伙伴根据语境选择声音或文字。角色决定措辞，TTS 音色由部署者选择。链接和代码保留为文字，合成失败时用原文继续对话。`/voice off` 随时切回文字。 |

语音用于表达语气、停顿和亲近感。配置支持 `/audio/speech` 且能返回 Ogg/Opus 的服务，设置 `EVYNI_TTS_MODEL` 和 `EVYNI_TTS_VOICE`。可用 `EVYNI_TTS_BASE_URL`、`EVYNI_TTS_API_KEY` 单独指定服务。终端显示文字并将音频保存到数据目录，装有 `ffplay` 时自动播放。飞书里会收到可点开的语音消息。v0.1 的用户输入为文字。

如果语音模型支持 `instructions`，设置 `EVYNI_TTS_DIRECTIONS=1` 可让角色为每条语音决定读法。比如一句“到家了就好”，可以轻声说，在句末停一下。朗读提示单独传给语音服务，听到的仍是回复原文。

## 飞书私聊

1. 安装扩展 `python -m pip install ".[feishu]"`。在飞书自建应用里启用机器人，以长连接方式订阅 `im.message.receive_v1`。
2. 给应用开通读取用户发给机器人的单聊消息和以应用身份发送消息的权限。语音需要上传文件资源权限。发布应用版本，让目标用户可以使用。
3. 设置 `EVYNI_FEISHU_APP_ID`、`EVYNI_FEISHU_APP_SECRET` 和 `EVYNI_FEISHU_OWNER_OPEN_ID`。最后一项是获准与伙伴私聊的用户在该应用下的 open_id。
4. 保持 `evyni feishu` 运行，给机器人发 `/status`。先确认文字送达，再配置模型和语音。飞书收件先写入本地队列，重试使用同一消息标识。

## 数据与开发

默认数据目录为 `~/.evyni`。使用 `EVYNI_HOME` 或 `evyni --home PATH chat` 可选择另一个目录。角色设定在 `persona.md`，消息、记忆、约定及投递状态在 `evyni.sqlite3`，本地语音在 `audio/`。终端和飞书各自维护对话与记忆。更正或遗忘会刷新提供给模型的历史窗口，已有聊天记录仍保存在本机。使用远程模型时，角色、选中的记忆、近期对话和待办会发送给你配置的模型服务。语音服务会收到需要朗读的回复。

源码在 `src/evyni/`。`companion.py` 管理角色、对话和用户操作，`store.py` 管理本地数据，`delivery.py` 判断何时投递，`providers.py` 连接模型与语音服务，`feishu.py` 连接飞书。可在干净环境里用虚构数据重放改期、静默、遗忘和投递故障场景：

```sh
python -m pip install -e ".[feishu]"
python -m unittest discover -s tests -v
```

项目采用 [MIT 许可证](LICENSE)。
