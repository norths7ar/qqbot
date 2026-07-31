from __future__ import annotations

from nonebot_plugin_multi_source_daily.api import get_news_source
from nonebot_plugin_multi_source_daily.api.handlers import get_news_handler
from nonebot_plugin_nmcweather.data_source import get_weather, search_city_code

from qqbot.news import strip_unavailable_detail_hint
from qqbot.web_tools import TavilyClient

NEWS_TYPES = {
    "60秒": "60秒",
    "60s": "60秒",
    "知乎": "知乎热榜",
    "知乎热榜": "知乎热榜",
    "微博": "微博热搜",
    "微博热搜": "微博热搜",
}


async def weather_report(location: str) -> str:
    station_id = await search_city_code(location.strip())
    if not station_id:
        return f"没有找到城市“{location}”。省市同名不明确时请使用“省份-城市”。"
    payload = await get_weather(station_id)
    try:
        real = payload["data"]["real"]
        station = real["station"]
        weather = real["weather"]
        wind = real["wind"]
    except (KeyError, TypeError):
        return "天气源暂时没有返回有效数据。"
    return "\n".join(
        [
            f"{station.get('province', '')}{station.get('city', location)}实时天气",
            f"天气：{_known(weather.get('info'))}",
            f"温度：{_unit(weather.get('temperature'), '℃')}，"
            f"体感 {_unit(weather.get('feelst'), '℃')}",
            f"湿度：{_unit(weather.get('humidity'), '%')}",
            f"风力：{_known(wind.get('direct'))} {_known(wind.get('power'))}",
            f"降水量：{_unit(weather.get('rain'), 'mm')}",
            f"发布时间：{_known(real.get('publish_time'))}",
        ]
    )


async def news_headlines(source: str) -> str:
    news_type = NEWS_TYPES.get(source.strip())
    if news_type is None:
        return "新闻类型只支持：60秒、知乎、微博。"
    message = await get_news_source(news_type).fetch(
        format_type="text",
        force_refresh=False,
    )
    return strip_unavailable_detail_hint(message.extract_plain_text())


async def news_detail(
    source: str,
    index: int,
    *,
    tavily: TavilyClient,
) -> str:
    news_type = NEWS_TYPES.get(source.strip())
    if news_type not in {"知乎热榜", "微博热搜"}:
        return "新闻详情目前支持知乎和微博。"
    handler = get_news_handler(news_type)
    if handler is None:
        return f"没有找到{news_type}的数据源。"
    item = await handler.get_news_item_by_index(index)
    if item is None:
        return f"没有找到{news_type}第 {index} 条。请先重新查看榜单。"
    if not item.url or item.url == "#":
        return f"第 {index} 条没有可访问的原文链接。"
    extracted = await tavily.extract(item.url, query=item.title)
    return f"标题：{item.title}\n{extracted}"


def _known(value: object) -> str:
    text = str(value or "").strip()
    return "未知" if text in {"", "9999", "9999.0"} else text


def _unit(value: object, unit: str) -> str:
    text = _known(value)
    return text if text == "未知" else f"{text}{unit}"
