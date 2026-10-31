"""用户账号类工具：账号信息与收藏统计。"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from music_api import MusicAPIError, call_music_api, get_cookie_from_config


@tool
def get_user_account_info(config: RunnableConfig = None) -> str:
    """
    获取当前登录用户的网易云音乐账号基本信息。
    当你需要称呼用户、确认用户的 VIP 状态，或者查询用户听歌数量时，请调用此工具。

    (注意：此工具不需要传入任何参数，直接调用即可)
    """
    cookie = get_cookie_from_config(config)

    try:
        data = call_music_api("/user/account", cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常，无法连接到本地的音乐 API 服务。请检查端口是否开启。错误信息：{e}"

    # 【数据瘦身】绝不能把原始 JSON 直接扔给 AI，提取核心字段组织成精简文本
    if data.get("code") == 200:
        profile = data.get("profile")

        # 如果 profile 为 None，说明 cookie 无效或未登录（游客身份也没有档案）
        if not profile:
            return "获取失败：未能读取到用户档案，请确认用户是否已登录或 Cookie 是否已过期。"

        nickname = profile.get("nickname", "未知昵称")
        vip_type = profile.get("vipType", 0)
        listen_songs = profile.get("listenSongs", 0)

        # 判断 VIP 状态（网易云的 vipType 大于 0 通常即为付费会员）
        vip_status = "黑胶 VIP" if vip_type > 0 else "普通用户"

        return (
            f"查询成功。以下是当前登录用户的信息：\n"
            f"- 用户昵称：{nickname}\n"
            f"- 会员状态：{vip_status}\n"
            f"- 累计听歌数量：{listen_songs} 首"
        )
    else:
        return f"获取失败，API 接口返回状态码：{data.get('code')}"


@tool
def get_user_subcount_info(config: RunnableConfig = None) -> str:
    """
    获取当前登录用户的各项收藏数量统计信息。
    当用户询问“我有多少个歌单”、“我收藏了多少歌手/MV”、“我的云音乐收藏统计”等问题时，请调用此工具。
    (注意：此工具不需要传入任何参数，直接调用即可)
    """
    cookie = get_cookie_from_config(config)

    try:
        data = call_music_api("/user/subcount", cookie)
    except MusicAPIError as e:
        return f"工具调用发生异常：{e}"

    # 【数据瘦身】提取网易云 API 返回的核心统计字段
    if data.get("code") == 200:
        created_playlist = data.get("createdPlaylistCount", 0)  # 创建的歌单数
        sub_playlist = data.get("subPlaylistCount", 0)  # 收藏的歌单数
        artist_count = data.get("artistCount", 0)  # 收藏的歌手数
        mv_count = data.get("mvCount", 0)  # 收藏的MV数
        dj_count = data.get("djRadioCount", 0)  # 收藏的电台/播客数

        return (
            f"查询成功。以下是当前登录用户的收藏统计数据：\n"
            f"- 创建的歌单数量：{created_playlist} 个\n"
            f"- 收藏的歌单数量：{sub_playlist} 个\n"
            f"- 收藏的歌手数量：{artist_count} 个\n"
            f"- 收藏的 MV 数量：{mv_count} 个\n"
            f"- 订阅的电台/播客：{dj_count} 个"
        )
    else:
        return f"获取统计信息失败，API 接口返回状态码：{data.get('code')}"
