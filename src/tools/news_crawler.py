import os
import sys
import json
from datetime import datetime, timedelta
import time
import pandas as pd
from urllib.parse import urlparse

# 先加载 .env，再导入 LLM 客户端（避免 openrouter_config 模块级初始化的时序问题）
from dotenv import load_dotenv

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_project_root, ".env"), override=True)

from src.utils.llm_clients import LLMClientFactory

# 导入新的搜索模块
try:
    from src.crawler.search import google_search_sync, SearchOptions
except ImportError:
    print("警告: 无法导入新的搜索模块，将回退到 akshare")
    google_search_sync = None
    SearchOptions = None

# 保留 akshare 作为备用
try:
    import akshare as ak
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("警告: akshare 不可用")
    ak = None


# ── MX Search（东方财富妙想搜索）──
_mx_api_key = os.getenv("MX_APIKEY")
_mx_base_url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search"


def _mx_search(query: str) -> list:
    """使用 MX 妙想搜索获取新闻列表"""
    if not _mx_api_key:
        return []
    try:
        # requests 在模块头部 try 块导入，失败时为 None
        if requests is None:
            return []
        resp = requests.post(
            _mx_base_url,
            headers={"Content-Type": "application/json", "apikey": _mx_api_key},
            json={"query": query},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        items = []
        try:
            inner = data.get("data", {}).get("data", {})
            items = inner.get("llmSearchResponse", {}).get("data", [])
        except (KeyError, TypeError):
            return []

        news_list = []
        for item in items:
            title = item.get("title", "")
            content = item.get("content", "") or item.get("trunk", "") or title
            date_str = item.get("date", "")[:10] if item.get("date") else ""
            ins_name = item.get("insName", "") or item.get("informationType", "") or "妙想资讯"

            news_list.append({
                "title": title,
                "content": content,
                "source": ins_name,
                "url": item.get("url", ""),
                "keyword": "",
                "publish_time": f"{date_str} 00:00:00" if len(date_str) == 10 else "",
                "search_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })
        return news_list
    except Exception as e:
        print(f"MX 搜索失败: {e}")
        return []


def get_stock_news_via_mx(symbol: str, max_news: int = 10) -> list:
    """使用 MX 妙想搜索获取个股新闻"""
    symbol_clean = symbol.replace("HK", "").replace("KR", "").lstrip("0")
    query = f"{symbol} 股票 新闻"
    results = _mx_search(query)

    news_list = []
    for item in results[:max_news]:
        news_list.append({
            "title": item["title"],
            "content": item["content"],
            "source": item["source"],
            "url": item["url"],
            "keyword": symbol,
            "publish_time": item.get("publish_time", ""),
            "search_time": item.get("search_time", datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
        })
    return news_list


def build_search_query(symbol: str, date: str = None) -> str:
    """
    构建针对股票新闻的 Google 搜索查询

    Args:
        symbol: 股票代码，如 "300059"
        date: 截止日期，格式 "YYYY-MM-DD"

    Returns:
        构建好的搜索查询字符串
    """
    # 基础查询：股票代码 + 新闻关键词
    base_query = f"{symbol} 股票 新闻 财经"

    # 添加时间限制（搜索指定日期之前的新闻）
    if date:
        try:
            # 解析日期并计算一周前的日期作为开始时间
            end_date = datetime.strptime(date, "%Y-%m-%d")
            start_date = end_date - timedelta(days=7)  # 搜索过去一周的新闻

            # Google 搜索时间语法：after:YYYY-MM-DD before:YYYY-MM-DD
            base_query += f" after:{start_date.strftime('%Y-%m-%d')} before:{date}"
        except ValueError:
            print(f"日期格式错误: {date}，忽略时间限制")

    # 限制新闻网站 - 只选择主要的财经网站
    news_sites = [
        "site:sina.com.cn",
        "site:163.com",
        "site:eastmoney.com",
        "site:cnstock.com",
        "site:hexun.com"
    ]

    # 添加网站限制
    query = f"{base_query} ({' OR '.join(news_sites)})"

    return query


def extract_domain(url: str) -> str:
    """从 URL 提取域名作为新闻来源"""
    try:
        parsed = urlparse(url)
        return parsed.netloc
    except Exception:
        return "未知来源"


def convert_search_results_to_news_format(search_results, symbol: str) -> list:
    """
    将搜索结果转换为现有新闻格式

    Args:
        search_results: Google 搜索结果
        symbol: 股票代码

    Returns:
        符合现有格式的新闻列表
    """
    news_list = []

    for result in search_results:
        # 过滤掉明显不相关的结果
        if any(keyword in result.title.lower() for keyword in ['招聘', '求职', '广告', '登录', '注册']):
            continue

        # 尝试从snippet中提取时间信息
        publish_time = None
        if result.snippet:
            # 查找常见的时间模式
            import re
            time_patterns = [
                r'(\d{1,2}天前)',
                r'(\d{1,2}小时前)',
                r'(\d{4}-\d{2}-\d{2})',
                r'(\d{4}年\d{1,2}月\d{1,2}日)',
                r'(\d{2}-\d{2})'
            ]

            for pattern in time_patterns:
                match = re.search(pattern, result.snippet)
                if match:
                    time_str = match.group(1)
                    try:
                        # 处理相对时间
                        if '天前' in time_str:
                            days = int(time_str.replace('天前', ''))
                            publish_date = datetime.now() - timedelta(days=days)
                            publish_time = publish_date.strftime(
                                '%Y-%m-%d %H:%M:%S')
                        elif '小时前' in time_str:
                            hours = int(time_str.replace('小时前', ''))
                            publish_date = datetime.now() - timedelta(hours=hours)
                            publish_time = publish_date.strftime(
                                '%Y-%m-%d %H:%M:%S')
                        # YYYY-MM-DD格式
                        elif '-' in time_str and len(time_str) == 10:
                            publish_time = f"{time_str} 00:00:00"
                        break
                    except Exception:
                        continue

        news_item = {
            "title": result.title,
            "content": result.snippet or result.title,
            "source": extract_domain(result.link),
            "url": result.link,
            "keyword": symbol,
            "search_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')  # 搜索时间
        }

        # 只有当能提取到发布时间时才添加，否则不包含这个字段
        if publish_time:
            news_item["publish_time"] = publish_time

        news_list.append(news_item)

    return news_list


def get_stock_news_via_sina(symbol: str, max_news: int = 10) -> list:
    """直接从新浪财经爬取个股新闻"""
    from src.tools.api import get_stock_prefix
    prefix = get_stock_prefix(symbol)
    url = f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{prefix}{symbol}.phtml"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    news_list = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'gbk'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        news_container = soup.find('div', {'class': 'datelist'})
        if not news_container:
            return []
            
        items = news_container.find_all('a')
        for item in items[:max_news * 2]:
            title = item.get_text().strip()
            link = item.get('href')
            
            # 提取日期
            date_str = ""
            for sibling in item.next_siblings:
                if isinstance(sibling, str) and "(" in sibling and ")" in sibling:
                    date_str = sibling.strip("() ")
                    break
            
            if not date_str:
                date_str = datetime.now().strftime("%Y-%m-%d")
            
            if len(date_str) == 5:
                date_str = f"{datetime.now().year}-{date_str}"
            
            publish_time = f"{date_str} 00:00:00" if len(date_str) == 10 else date_str
            
            if title and link:
                news_list.append({
                    "title": title,
                    "content": title,
                    "publish_time": publish_time,
                    "source": "新浪财经",
                    "url": link if link.startswith('http') else f"https://finance.sina.com.cn{link}",
                    "keyword": symbol
                })
        
        return news_list[:max_news]
    except Exception as e:
        print(f"新浪新闻抓取失败: {e}")
        return []

def get_stock_news_via_akshare(symbol: str, max_news: int = 10) -> list:
    """使用 akshare 获取股票新闻的原始方法"""
    if ak is None:
        return []

    try:
        # 尝试接口 1: 东方财富 (可能会 JSON 解析失败)
        news_df = ak.stock_news_em(symbol=symbol)
        if news_df is None or len(news_df) == 0:
            return get_stock_news_via_sina(symbol, max_news)

    except Exception as e:
        return get_stock_news_via_sina(symbol, max_news)

    # 实际可获取的新闻数量
    available_news_count = len(news_df)
    if available_news_count < max_news:
        max_news = available_news_count

    # 获取指定条数的新闻
    news_list = []
    for _, row in news_df.head(int(max_news * 1.5)).iterrows():
        try:
            content = row["新闻内容"] if "新闻内容" in row and not pd.isna(
                row["新闻内容"]) else ""
            if not content:
                content = row["新闻标题"]

            content = content.strip()
            if len(content) < 10:
                continue

            news_item = {
                "title": row["新闻标题"].strip(),
                "content": content,
                "publish_time": row["发布时间"],
                "source": row["文章来源"].strip(),
                "url": row["新闻链接"].strip(),
                "keyword": row["关键词"].strip() if "关键词" in row and not pd.isna(row["关键词"]) else symbol
            }
            news_list.append(news_item)
        except Exception:
            continue

    news_list.sort(key=lambda x: x["publish_time"], reverse=True)
    return news_list[:max_news]


def get_stock_news(symbol: str, max_news: int = 10, date: str = None) -> list:
    """获取并处理个股新闻
    """

    # 限制最大新闻条数
    max_news = min(max_news, 100)

    # 获取当前日期或使用指定日期
    cache_date = date if date else datetime.now().strftime("%Y-%m-%d")

    # 构建新闻文件路径
    news_dir = os.path.join("src", "data", "stock_news")
    print(f"新闻保存目录: {news_dir}")

    # 确保目录存在
    try:
        os.makedirs(news_dir, exist_ok=True)
        print(f"成功创建或确认目录存在: {news_dir}")
    except Exception as e:
        print(f"创建目录失败: {e}")
        return []

    # 缓存文件名包含日期信息
    news_file = os.path.join(news_dir, f"{symbol}_news_{cache_date}.json")
    print(f"新闻文件路径: {news_file}")

    # 检查缓存是否存在且有效
    cached_news = []
    cache_valid = False

    if os.path.exists(news_file):
        try:
            # 检查缓存文件的修改时间（时效性检查）
            file_mtime = os.path.getmtime(news_file)
            current_time = time.time()
            # 缓存有效期：当天的缓存在当天有效，历史日期的缓存始终有效
            if date:  # 如果指定了历史日期，缓存始终有效
                cache_valid = True
            else:  # 如果是当天数据，检查是否在同一天创建
                cache_date_obj = datetime.fromtimestamp(file_mtime).date()
                today = datetime.now().date()
                cache_valid = cache_date_obj == today

            if cache_valid:
                with open(news_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    cached_news = data.get("news", [])

                    if len(cached_news) >= max_news:
                        print(
                            f"使用缓存的新闻数据: {news_file} (缓存数量: {len(cached_news)})")
                        return cached_news[:max_news]
                    else:
                        print(
                            f"缓存的新闻数量({len(cached_news)})不足，需要获取更多新闻({max_news}条)")
            else:
                print(f"缓存文件已过期，将重新获取新闻")

        except Exception as e:
            print(f"读取缓存文件失败: {e}")
            cached_news = []

    print(f'开始获取{symbol}的新闻数据...')

    # 计算需要获取的新闻数量
    need_more_news = max_news - len(cached_news)
    fetch_count = max(need_more_news, max_news)  # 至少获取请求的数量

    # ── Google 搜索已禁用（Google 在当前环境不可用）──
    # 注释掉优先 Google 搜索逻辑，保留代码备用地
    # if google_search_sync and SearchOptions:
    #     try:
    #         print("使用 Google 搜索获取新闻...")
    #         ...
    #     except Exception as e:
    #         print(f"Google 搜索获取新闻时出错: {e}，回退到 akshare")

    # 直接使用 akshare 获取新闻
    # ── MX 优先，akshare 回退 ──
    new_news_list = []
    if _mx_api_key:
        print("使用 MX 妙想搜索获取新闻...")
        new_news_list = get_stock_news_via_mx(symbol, fetch_count)
        if new_news_list:
            print(f"通过 MX 搜索成功获取到 {len(new_news_list)} 条新闻")
        else:
            print("MX 搜索未返回有效结果，回退到 akshare")

    if not new_news_list:
        print("使用 akshare 获取新闻...")
        new_news_list = get_stock_news_via_akshare(symbol, fetch_count)

    # 合并缓存和新获取的新闻，去重
    if cached_news and new_news_list:
        # 创建已有新闻的标题集合用于去重
        existing_titles = {news['title'] for news in cached_news}

        # 过滤掉重复的新闻
        unique_new_news = [
            news for news in new_news_list
            if news['title'] not in existing_titles
        ]

        # 合并新闻列表
        combined_news = cached_news + unique_new_news
        print(
            f"合并缓存新闻({len(cached_news)}条)和新获取新闻({len(unique_new_news)}条)，总计{len(combined_news)}条")
    else:
        combined_news = new_news_list or cached_news

    # 按发布时间排序（如果有发布时间信息）
    try:
        combined_news.sort(key=lambda x: x.get(
            "publish_time", ""), reverse=True)
    except Exception:
        pass  # 如果排序失败，保持原顺序

    # 只保留指定条数的新闻
    final_news_list = combined_news[:max_news]

    # 保存到文件（只有当获取到新数据时才保存）
    if new_news_list or not cache_valid:
        try:
            save_data = {
                "date": cache_date,
                "method": "mx" if new_news_list and _mx_api_key else "akshare",
                "query": f"{symbol} 股票 新闻",
                "news": combined_news,  # 保存所有新闻，不只是返回的部分
                "cached_count": len(cached_news),
                "new_count": len(new_news_list),
                "total_count": len(combined_news),
                "last_updated": datetime.now().isoformat()
            }
            with open(news_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            print(f"成功保存{len(combined_news)}条新闻到文件: {news_file}")
        except Exception as e:
            print(f"保存新闻数据到文件时出错: {e}")

    return final_news_list


def get_news_sentiment(news_list: list, num_of_news: int = 5,
                      use_structured_output: bool = True) -> float:
    """分析新闻情感得分

    Args:
        news_list (list): 新闻列表
        num_of_news (int): 用于分析的新闻数量，默认为5条
        use_structured_output (bool): 是否使用 json_schema 结构化输出（默认开）

    Returns:
        float: 情感得分，范围[-1, 1]，-1最消极，1最积极
    """
    if not news_list:
        return 0.0

    # 检查是否有缓存的情感分析结果
    cache_file = "src/data/sentiment_cache.json"
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)

    # 生成新闻内容的唯一标识（含结构化输出标识，避免缓存版本冲突）
    cache_suffix = "_v2" if use_structured_output else "_v1"
    news_key = "|".join([
        f"{news['title']}|{news['content'][:100]}|{news['publish_time']}"
        for news in news_list[:num_of_news]
    ]) + cache_suffix

    # 检查缓存
    if os.path.exists(cache_file):
        print("发现情感分析缓存文件")
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                if news_key in cache:
                    print("使用缓存的情感分析结果")
                    return cache[news_key]
                print("未找到匹配的情感分析缓存")
        except Exception as e:
            print(f"读取情感分析缓存出错: {e}")
            cache = {}
    else:
        print("未找到情感分析缓存文件，将创建新文件")
        cache = {}

    # 准备系统消息
    system_message = {
        "role": "system",
        "content": """你是一个专业的A股市场分析师，擅长解读新闻对股票走势的影响。你需要分析一组新闻的情感倾向，并给出一个介于-1到1之间的分数：
        - 1表示极其积极（例如：重大利好消息、超预期业绩、行业政策支持）
        - 0.5到0.9表示积极（例如：业绩增长、新项目落地、获得订单）
        - 0.1到0.4表示轻微积极（例如：小额合同签订、日常经营正常）
        - 0表示中性（例如：日常公告、人事变动、无重大影响的新闻）
        - -0.1到-0.4表示轻微消极（例如：小额诉讼、非核心业务亏损）
        - -0.5到-0.9表示消极（例如：业绩下滑、重要客户流失、行业政策收紧）
        - -1表示极其消极（例如：重大违规、核心业务严重亏损、被监管处罚）

        分析时重点关注：
        1. 业绩相关：财报、业绩预告、营收利润等
        2. 政策影响：行业政策、监管政策、地方政策等
        3. 市场表现：市场份额、竞争态势、商业模式等
        4. 资本运作：并购重组、股权激励、定增配股等
        5. 风险事件：诉讼仲裁、处罚、债务等
        6. 行业地位：技术创新、专利、市占率等
        7. 舆论环境：媒体评价、社会影响等

        请确保分析：
        1. 新闻的真实性和可靠性
        2. 新闻的时效性和影响范围
        3. 对公司基本面的实际影响
        4. A股市场的特殊反应规律"""
    }

    # 准备新闻内容
    news_content = "\n\n".join([
        f"标题：{news['title']}\n"
        f"来源：{news['source']}\n"
        f"时间：{news['publish_time']}\n"
        f"内容：{news['content']}"
        for news in news_list[:num_of_news]
    ])

    user_message = {
        "role": "user",
        "content": f"请分析以下A股上市公司相关新闻的情感倾向：\n\n{news_content}"
    }

    # ── json_schema 结构化输出 ──
    if use_structured_output:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "sentiment_analysis",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "score": {
                            "type": "number",
                            "description": "情感分数，-1到1，-1最消极，1最积极",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "置信度，0到1，0最低，1最高",
                        },
                        "key_factors": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "影响情感的关键因素列表，最多3条",
                        },
                    },
                    "required": ["score", "confidence", "key_factors"],
                    "additionalProperties": False,
                },
            },
        }

        try:
            client = LLMClientFactory.create_client()
            result = client.get_completion(
                [system_message, user_message],
                response_format=response_format,
            )
            if result is None:
                print("Error: LLM returned None")
                return 0.0

            parsed = _json.loads(result)
            sentiment_score = float(parsed["score"])
            confidence = float(parsed.get("confidence", 0.5))
            factors = parsed.get("key_factors", [])

            sentiment_score = max(-1.0, min(1.0, sentiment_score))
            print(f"情感分析: score={sentiment_score:.2f}, "
                  f"confidence={confidence:.2f}, factors={factors}")

            cache[news_key] = sentiment_score
            try:
                with open(cache_file, 'w', encoding='utf-8') as f:
                    _json.dump(cache, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"Error writing cache: {e}")

            return sentiment_score

        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            print(f"Error parsing structured output: {e}")
            print(f"Raw result: {result}")
            # Fall through to unstructured fallback


    # ── 旧版 fallback：裸 float 解析 ──
    user_message["content"] += "\n\n请直接返回一个数字，范围是-1到1，无需解释。"

    try:
        client = LLMClientFactory.create_client()
        result = client.get_completion([system_message, user_message])
        if result is None:
            print("Error: PI error occurred, LLM returned None")
            return 0.0

        try:
            sentiment_score = float(result.strip())
        except ValueError as e:
            print(f"Error parsing sentiment score: {e}")
            print(f"Raw result: {result}")
            return 0.0

        sentiment_score = max(-1.0, min(1.0, sentiment_score))

        cache[news_key] = sentiment_score
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error writing cache: {e}")

        return sentiment_score

    except Exception as e:
        print(f"Error analyzing news sentiment: {e}")
        return 0.0
