import os
import json
from flask import Flask, render_template, request, session, redirect, url_for, make_response, jsonify, flash
import requests
from urllib.parse import urlencode, urlparse
import pprint
import html
from functools import lru_cache, wraps
from datetime import datetime, timedelta
import logging
from requests.exceptions import RequestException, ProxyError, ConnectionError
from concurrent.futures import ThreadPoolExecutor
import hashlib
from PIL import Image
import io
import base64

app = Flask(__name__)
app.secret_key = os.urandom(24)

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 请求配置
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3
PROXY_ENABLED = False
MAX_WORKERS = 5  # 线程池最大工作线程数
CACHE_TIMEOUT = 3600  # 缓存过期时间（秒）
SOURCE_CHECK_TIMEOUT = 5  # 验证源可用性的超时时间（秒）

# 创建线程池
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# 代理配置
proxies = {
    'http': None,
    'https': None
}

# 内存缓存
cache = {}
source_availability_cache = {}  # 源可用性的缓存

def cache_with_timeout(timeout=CACHE_TIMEOUT):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            now = datetime.now()
            
            # 检查缓存是否存在且未过期
            if key in cache:
                result, timestamp = cache[key]
                if now - timestamp < timedelta(seconds=timeout):
                    return result
            
            # 执行函数并缓存结果
            result = func(*args, **kwargs)
            cache[key] = (result, now)
            return result
        return wrapper
    return decorator

def make_request(url, method='get', timeout=REQUEST_TIMEOUT, **kwargs):
    """
    统一的请求处理函数，包含重试和错误处理
    """
    retry_count = 0
    while retry_count < MAX_RETRIES:
        try:
            kwargs['timeout'] = timeout
            if not PROXY_ENABLED:
                kwargs['proxies'] = proxies
            
            if method.lower() == 'get':
                response = requests.get(url, **kwargs)
            else:
                response = requests.post(url, **kwargs)
            
            response.raise_for_status()
            return response
            
        except (ProxyError, ConnectionError, RequestException) as e:
            logger.error(f"请求错误: {str(e)}")
            retry_count += 1
            if retry_count == MAX_RETRIES:
                raise
                
        except Exception as e:
            logger.error(f"未知错误: {str(e)}")
            raise

def is_source_available(source):
    """
    验证源是否可用
    """
    source_id = source['yuan_name']
    
    # 检查缓存中是否有结果
    now = datetime.now()
    if source_id in source_availability_cache:
        is_available, timestamp = source_availability_cache[source_id]
        # 如果缓存未过期，返回缓存结果
        if now - timestamp < timedelta(seconds=CACHE_TIMEOUT):
            return is_available
    
    try:
        # 构建测试URL
        api_url = f"{source['yuan_url']}{source['yuan_api']}"
        logger.info(f"验证源可用性: {source_id}, URL: {api_url}")
        
        # 尝试请求，使用较短的超时时间
        response = make_request(api_url, timeout=SOURCE_CHECK_TIMEOUT)
        
        # 检查响应是否为JSON格式
        try:
            result = response.json()
            if isinstance(result, dict):
                logger.info(f"源 {source_id} 可用")
                source_availability_cache[source_id] = (True, now)
                return True
        except ValueError:
            logger.warning(f"源 {source_id} 响应非JSON格式")
        
        logger.warning(f"源 {source_id} 响应异常")
        source_availability_cache[source_id] = (False, now)
        return False
        
    except Exception as e:
        logger.error(f"验证源 {source_id} 可用性失败: {str(e)}")
        source_availability_cache[source_id] = (False, now)
        return False

@cache_with_timeout(timeout=3600)  # 缓存1小时
def load_api_config(check_availability=True):
    """
    加载API配置，并验证源是否可用
    """
    try:
        with open('tata.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        if not check_availability:
            return config
        
        # 验证每个源的可用性
        available_sources = []
        for source in config:
            if is_source_available(source):
                available_sources.append(source)
        
        logger.info(f"总共 {len(config)} 个源，可用 {len(available_sources)} 个")
        
        if not available_sources and config:
            # 如果没有可用源，返回所有源（避免没有源可选）
            logger.warning("没有可用源，返回所有源")
            return config
            
        return available_sources
    except Exception as e:
        logger.error(f"加载API配置失败: {str(e)}")
        return []

def init_app():
    """
    初始化应用程序
    """
    global default_source
    
    # 加载配置并找到第一个可用源作为默认源
    sources = load_api_config()
    if sources and len(sources) > 0:
        default_source = sources[0]['yuan_name']
        logger.info(f"设置默认源: {default_source}")
    else:
        default_source = None
        logger.warning("没有找到可用源")

# 初始化默认源
default_source = None
init_app()

@cache_with_timeout(timeout=1800)  # 缓存30分钟
def fetch_categories():
    """
    从API获取分类信息，处理层级关系
    """
    source = get_selected_source()
    api_url = f"{source['yuan_url']}{source['yuan_api']}"
    logger.info(f"获取分类请求 URL: {api_url}")
    
    try:
        response = make_request(api_url)
        data = response.json()
        
        if not data or 'class' not in data or not data['class']:
            logger.warning("API未返回分类数据，使用默认分类")
            return get_default_categories()
            
        categories = data['class']
        main_categories = {}
        result = []
        
        # 第一次遍历：收集所有主分类
        for cat in categories:
            type_id = str(cat.get('type_id', ''))
            type_pid = str(cat.get('type_pid', '0'))
            
            if type_pid == '0':
                cat['sub'] = []
                main_categories[type_id] = cat
                result.append(cat)
        
        # 第二次遍历：将子分类添加到对应的主分类中
        for cat in categories:
            type_pid = str(cat.get('type_pid', '0'))
            if type_pid != '0' and type_pid in main_categories:
                main_categories[type_pid]['sub'].append({
                    'type_id': cat.get('type_id'),
                    'type_name': cat.get('type_name'),
                    'type_pid': type_pid
                })
        
        # 按照type_id排序
        result.sort(key=lambda x: int(x.get('type_id', 0)))
        for cat in result:
            if cat.get('sub'):
                cat['sub'].sort(key=lambda x: int(x.get('type_id', 0)))
        
        # 如果没有获取到分类，返回默认分类
        if not result:
            return get_default_categories()
            
        return result
        
    except Exception as e:
        logger.error(f"获取分类失败: {str(e)}")
        return get_default_categories()

def get_default_categories():
    """
    返回默认的分类结构
    """
    return [
        {
            "type_id": "1",
            "type_name": "电影",
            "type_pid": "0",
            "sub": [
                {"type_id": "6", "type_name": "动作片", "type_pid": "1"},
                {"type_id": "7", "type_name": "喜剧片", "type_pid": "1"},
                {"type_id": "8", "type_name": "爱情片", "type_pid": "1"},
                {"type_id": "9", "type_name": "科幻片", "type_pid": "1"},
                {"type_id": "10", "type_name": "恐怖片", "type_pid": "1"},
                {"type_id": "11", "type_name": "剧情片", "type_pid": "1"},
                {"type_id": "12", "type_name": "战争片", "type_pid": "1"}
            ]
        },
        {
            "type_id": "2",
            "type_name": "电视剧",
            "type_pid": "0",
            "sub": [
                {"type_id": "13", "type_name": "国产剧", "type_pid": "2"},
                {"type_id": "14", "type_name": "港台剧", "type_pid": "2"},
                {"type_id": "15", "type_name": "日韩剧", "type_pid": "2"},
                {"type_id": "16", "type_name": "欧美剧", "type_pid": "2"},
                {"type_id": "20", "type_name": "海外剧", "type_pid": "2"}
            ]
        },
        {
            "type_id": "3",
            "type_name": "综艺",
            "type_pid": "0",
            "sub": []
        },
        {
            "type_id": "4",
            "type_name": "动漫",
            "type_pid": "0",
            "sub": []
        }
    ]

def fetch_videos(page=1, category=None):
    """
    获取视频列表，支持分类筛选
    """
    source = get_selected_source()
    logger.info(f"使用数据源: {source['yuan_name']}, URL: {source['yuan_url']}")
    
    params = {
        'ac': 'detail',
        'pg': page
    }
    
    # 如果指定了分类，添加分类参数
    if category:
        params[source['yuan_cat']] = category
        logger.info(f"按分类过滤: {category}")
    
    api_url = f"{source['yuan_url']}{source['yuan_api']}{urlencode(params)}"
    logger.info(f"获取视频列表请求 URL: {api_url}")
    
    try:
        response = make_request(api_url)
        data = response.json()
        
        # 检查JSON格式和结构
        if not isinstance(data, dict):
            logger.error(f"API返回数据格式不正确，应为字典，实际为: {type(data)}")
            return []
            
        logger.info(f"API响应状态: {data.get('code', '未知')}, 总数: {data.get('total', '未知')}")
        
        # 尝试不同的键名获取视频列表
        videos = []
        if 'list' in data:
            videos = data['list']
        elif 'data' in data and isinstance(data['data'], dict) and 'list' in data['data']:
            videos = data['data']['list']
        elif 'data' in data and isinstance(data['data'], list):
            videos = data['data']
        
        if not videos:
            logger.warning(f"未找到视频列表，API返回数据结构: {list(data.keys())}")
            return []
            
        logger.info(f"获取到 {len(videos)} 个视频")
        
        # 检查第一个视频的数据结构（如果有的话）
        if videos and len(videos) > 0:
            sample_video = videos[0]
            logger.info(f"视频数据样例字段: {list(sample_video.keys())}")
        
        # 处理视频数据，确保数据完整
        processed_videos = []
        for video in videos:
            # 确保基本字段存在
            if 'vod_id' not in video or not video['vod_id']:
                logger.warning(f"跳过无ID的视频: {video}")
                continue
                
            # 处理图片URL
            if 'vod_pic' in video:
                video['vod_pic'] = get_valid_image_url(video['vod_pic'])
            else:
                video['vod_pic'] = 'https://via.placeholder.com/300x400?text=No+Image'
            
            # 确保有分类名称
            if 'type_name' not in video or not video['type_name']:
                video['type_name'] = '未知'
                
            # 确保有时间字段
            if 'vod_time' not in video:
                video['vod_time'] = '未知'
                
            processed_videos.append(video)
                
        return processed_videos
    except Exception as e:
        logger.error(f"获取视频列表失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return []

def search_videos(keyword, page=1, category=None):
    """
    搜索视频，支持分类筛选
    """
    source = get_selected_source()
    logger.info(f"搜索视频，使用数据源: {source['yuan_name']}")
    
    params = {
        'ac': 'detail',
        'wd': keyword,
        'pg': page
    }
    
    # 如果指定了分类，添加分类参数
    if category:
        params[source['yuan_cat']] = category
        logger.info(f"搜索时按分类过滤: {category}")
        
    api_url = f"{source['yuan_url']}{source['yuan_api']}{urlencode(params)}"
    logger.info(f"搜索请求 URL: {api_url}")
    
    try:
        response = make_request(api_url)
        data = response.json()
        
        # 检查JSON格式和结构
        if not isinstance(data, dict):
            logger.error(f"搜索API返回数据格式不正确，应为字典，实际为: {type(data)}")
            return []
            
        logger.info(f"搜索API响应状态: {data.get('code', '未知')}, 总数: {data.get('total', '未知')}")
        
        # 尝试不同的键名获取视频列表
        videos = []
        if 'list' in data:
            videos = data['list']
        elif 'data' in data and isinstance(data['data'], dict) and 'list' in data['data']:
            videos = data['data']['list']
        elif 'data' in data and isinstance(data['data'], list):
            videos = data['data']
        
        if not videos:
            logger.warning(f"搜索未找到视频，关键词: {keyword}, API返回数据结构: {list(data.keys())}")
            return []
            
        logger.info(f"搜索到 {len(videos)} 个视频")
        
        # 处理视频数据，确保数据完整
        processed_videos = []
        for video in videos:
            # 确保基本字段存在
            if 'vod_id' not in video or not video['vod_id']:
                continue
                
            # 处理图片URL
            if 'vod_pic' in video:
                video['vod_pic'] = get_valid_image_url(video['vod_pic'])
            else:
                video['vod_pic'] = 'https://via.placeholder.com/300x400?text=No+Image'
            
            # 确保有分类名称
            if 'type_name' not in video or not video['type_name']:
                video['type_name'] = '未知'
                
            # 确保有时间字段
            if 'vod_time' not in video:
                video['vod_time'] = '未知'
                
            processed_videos.append(video)
                
        return processed_videos
    except Exception as e:
        logger.error(f"搜索请求失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return []

def process_image_url(url):
    """
    处理图片URL，添加缓存和压缩
    """
    if not url:
        return url
        
    # 生成缓存键
    cache_key = hashlib.md5(url.encode()).hexdigest()
    
    # 检查缓存
    if cache_key in cache:
        return cache[cache_key]
    
    try:
        # 获取图片
        response = make_request(url)
        img = Image.open(io.BytesIO(response.content))
        
        # 压缩图片
        img.thumbnail((800, 800))  # 限制最大尺寸
        
        # 转换为base64
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85)
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        # 缓存结果
        cache[cache_key] = f"data:image/jpeg;base64,{img_str}"
        return cache[cache_key]
        
    except Exception as e:
        logger.error(f"图片处理失败: {str(e)}")
        return url

def get_valid_image_url(url):
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.scheme:
        return f"http://{url}"
    return url

# 搜索历史记录存储
search_history = []
search_cache = {}

def add_to_search_history(keyword):
    global search_history
    if keyword and keyword not in search_history:
        search_history.insert(0, keyword)
        if len(search_history) > 10:  # 只保留最近10条搜索记录
            search_history.pop()

def get_search_suggestions(query):
    suggestions = []
    
    # 添加历史搜索建议
    if search_history:
        history_suggestions = [h for h in search_history if query.lower() in h.lower()]
        suggestions.extend(history_suggestions[:3])
    
    # 添加热门搜索建议（这里可以根据实际情况从数据库或API获取）
    hot_searches = ["热门电影", "最新电视剧", "动漫", "综艺"]
    if query:
        hot_suggestions = [h for h in hot_searches if query.lower() in h.lower()]
        suggestions.extend(hot_suggestions)
    
    return list(set(suggestions))[:5]  # 去重并限制返回数量

@app.route('/')
def index():
    """
    首页路由，显示视频列表和分类
    """
    try:
        # 获取请求参数
        keyword = request.args.get('wd', '')
        category = request.args.get('t', '')
        page = int(request.args.get('pg', '1'))
        sub_category = False  # 添加标记，表示当前分类是否是子分类

        # 获取当前选择的数据源
        source = get_selected_source()
        selected_source = source['yuan_name']
        
        # 将选择的源保存到session（如果不存在）
        if 'selected_source' not in session or not session['selected_source']:
            session['selected_source'] = selected_source
            logger.info(f"将数据源保存到session: {selected_source}")

        # 获取分类数据
        categories = fetch_categories()
        
        # 判断当前分类是否是子分类
        if category:
            for cat in categories:
                if cat.get('sub'):
                    for sub in cat['sub']:
                        if sub['type_id'] == category:
                            sub_category = True
                            break
                    if sub_category:
                        break
        
        # 获取视频数据
        if keyword:
            videos = cached_search_videos(keyword, page, category)
            logger.info(f"搜索关键词 '{keyword}' 返回 {len(videos)} 个结果")
        else:
            videos = fetch_videos(page, category)
            logger.info(f"获取视频列表返回 {len(videos)} 个结果")
        
        # 处理AJAX请求（无限滚动加载）
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('video_list.html', videos=videos)
        
        # 获取所有可用源
        sources = get_sources()
        
        # 渲染完整页面
        return render_template('index.html', 
                             videos=videos, 
                             sources=sources, 
                             selected_source=selected_source,
                             keyword=keyword,
                             category=category,
                             page=page,
                             categories=categories,
                             sub_category=sub_category)
                             
    except Exception as e:
        logger.error(f"渲染首页时出错: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        # 返回错误页面
        return render_template('error.html', error="加载页面时出错，请刷新重试")

@app.route('/search')
def search():
    keyword = request.args.get('wd', '')
    page = request.args.get('pg', 1, type=int)
    category = request.args.get('t', '')
    
    if keyword:
        add_to_search_history(keyword)
        videos = search_videos(keyword, page, category)
    else:
        videos = fetch_videos(page)
    
    return jsonify({
        'videos': videos,
        'page': page,
        'has_more': len(videos) > 0
    })

# 获取视频详情，通过读取 tata 配置信息拼接 URL
def fetch_video_details(vod_id):
    source = get_selected_source()
    params = {
        'ac': 'detail',
        'ids': vod_id
    }
    url = f"{source['yuan_url']}{source['yuan_api']}{urlencode(params)}"
    
    try:
        response = make_request(url)
        return response.json()
    except Exception as e:
        logger.error(f"获取视频详情失败: {str(e)}")
        return None

@app.route('/play/<int:vod_id>')
def play(vod_id):
    video_details = fetch_video_details(vod_id)
    if 'list' in video_details and video_details['list']:
        video = video_details['list'][0]
        play_urls = video['vod_play_url'].split('#')  
        episodes = [{'index': i + 1, 'url': url.split('$')[1]} for i, url in enumerate(play_urls) if 'm3u8' in url]
        
        # 格式化 vod_content 中的 HTML 标签
        video['vod_content'] = html.unescape(video['vod_content'])
        
        # 获取推荐视频
        recommended_videos = get_recommended_videos(video)
        
        return make_response(render_template('play.html', video=video, episodes=episodes, first_episode_url=episodes[0]['url'] if episodes else '', recommended_videos=recommended_videos))

    return "视频未找到", 404

@app.route('/change_source', methods=['POST'])
def change_source():
    selected_source = request.form.get('source')
    session['selected_source'] = selected_source  # Save the selected source in the session
    
    # 清除缓存，确保获取新数据源的内容
    global cache
    cache.clear()
    
    # 获取原始查询参数，保持其他参数不变
    page = request.args.get('pg', '1')
    keyword = request.args.get('wd', '')
    category = request.args.get('t', '')
    
    # 构建重定向URL，保留原有查询参数
    redirect_url = url_for('index')
    params = []
    if keyword:
        params.append(f"wd={keyword}")
    if category:
        params.append(f"t={category}")
    if page and page != '1':
        params.append(f"pg={page}")
    
    if params:
        redirect_url += '?' + '&'.join(params)
    
    return redirect(redirect_url)

def get_selected_source():
    """
    获取当前选择的数据源信息
    """
    try:
        config = load_api_config()  # 这是源列表
        
        # 从session中获取选择的源名称，如果没有则使用默认的
        selected_source_name = session.get('selected_source')
        
        if not selected_source_name and default_source:
            selected_source_name = default_source
        
        logger.info(f"从session中获取数据源: {selected_source_name}")
        
        # 如果有选择的源名称，查找对应的源信息
        if selected_source_name:
            for source in config:
                if source['yuan_name'] == selected_source_name:
                    logger.info(f"已找到选择的数据源: {source['yuan_name']}")
                    return source
        
        # 如果没有找到选择的源，或者没有选择源，使用第一个源
        if config and len(config) > 0:
            logger.info(f"使用默认第一个数据源: {config[0]['yuan_name']}")
            return config[0]
        
        # 最后的备选方案
        logger.warning("未找到有效数据源，使用备用源")
        return {
            "yuan_name": "华为",
            "yuan_url": "https://cjhwba.com", 
            "yuan_api": "/api.php/provide/vod/?", 
            "yuan_cat": "t", 
            "yuan_detail": "ac"
        }
    except Exception as e:
        logger.error(f"获取数据源时出错: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        # 返回一个备用源
        return {
            "yuan_name": "华为",
            "yuan_url": "https://cjhwba.com", 
            "yuan_api": "/api.php/provide/vod/?", 
            "yuan_cat": "t", 
            "yuan_detail": "ac"
        }

# 添加新的路由
@app.route('/search_suggestions')
def search_suggestions():
    query = request.args.get('q', '')
    # 这里你需要实现一个函数来获取搜索建议
    # 这只是一个示例，你需要根据你的数据源来实现实际的搜索建议功能
    suggestions = get_search_suggestions(query)
    return jsonify(suggestions)

def get_sources():
    """获取所有可用的数据源列表"""
    try:
        return load_api_config(check_availability=True)
    except Exception as e:
        logger.error(f"获取源列表失败: {str(e)}")
        return [{"yuan_name": "华为", "yuan_url": "https://cjhwba.com", "yuan_api": "/api.php/provide/vod/?", "yuan_cat": "t", "yuan_detail": "ac"}]

def get_recommended_videos(current_video, limit=4):
    # 这里我们使用相同类型的视频作为推荐
    source = get_selected_source()
    params = {
        'ac': 'detail',
        't': current_video.get('type_id', ''),
        'pg': 1,
        'limit': limit
    }
    api_url = f"{source['yuan_url']}{source['yuan_api']}{urlencode(params)}"
    
    try:
        response = make_request(api_url)
        data = response.json()
        recommended = [video for video in data.get('list', []) if video['vod_id'] != current_video['vod_id']]
        return recommended[:limit]
    except Exception as e:
        logger.error(f"获取推荐视频失败: {str(e)}")
        return []

@app.route('/api_test')
def api_test():
    """
    测试API路由，用于检查API响应，帮助调试
    """
    try:
        source = get_selected_source()
        source_name = source['yuan_name']
        api_url = source['yuan_url'] + source['yuan_api']
        
        # 基本信息
        basic_info = {
            'source': source_name,
            'api_url': api_url,
            'yuan_cat': source['yuan_cat'],
            'yuan_detail': source['yuan_detail']
        }
        
        # 测试获取分类
        categories_params = {}
        categories_url = f"{api_url}{urlencode(categories_params)}"
        
        # 测试获取视频列表
        videos_params = {'ac': 'detail', 'pg': 1}
        videos_url = f"{api_url}{urlencode(videos_params)}"
        
        # 测试搜索
        search_params = {'ac': 'detail', 'wd': '电影', 'pg': 1}
        search_url = f"{api_url}{urlencode(search_params)}"
        
        return render_template('api_test.html', 
                              basic_info=basic_info,
                              categories_url=categories_url,
                              videos_url=videos_url,
                              search_url=search_url)
    except Exception as e:
        logger.error(f"API测试页面出错: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"<pre>API测试出错: {str(e)}</pre>"

@app.route('/proxy_api')
def proxy_api():
    """
    代理API请求，避免跨域问题
    """
    try:
        url = request.args.get('url')
        if not url:
            return jsonify({"error": "URL参数缺失"}), 400
            
        logger.info(f"代理API请求: {url}")
        response = make_request(url)
        return jsonify(response.json())
    except Exception as e:
        logger.error(f"代理API请求失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route('/refresh_sources')
def refresh_sources():
    """手动刷新源可用性"""
    try:
        # 清除源可用性缓存
        global source_availability_cache
        source_availability_cache = {}
        
        # 重新加载并验证源
        sources = load_api_config(check_availability=True)
        available_count = len(sources)
        
        # 清空应用缓存
        global cache
        cache.clear()
        
        # 重新初始化应用
        init_app()
        
        return jsonify({
            "success": True,
            "message": f"已刷新源可用性，共 {available_count} 个可用源",
            "sources": [{"name": source['yuan_name'], "url": source['yuan_url']} for source in sources]
        })
    except Exception as e:
        logger.error(f"刷新源可用性失败: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"刷新源失败: {str(e)}"
        }), 500

@app.route('/source_status')
def source_status():
    """
    查看源状态路由
    """
    try:
        # 加载所有源
        all_sources = []
        with open('tata.json', 'r', encoding='utf-8') as f:
            all_sources = json.load(f)
            
        # 检查每个源的可用性状态
        sources_status = []
        for source in all_sources:
            source_id = source['yuan_name']
            is_available = False
            
            # 查看缓存中的状态
            if source_id in source_availability_cache:
                is_available, timestamp = source_availability_cache[source_id]
                cache_age = datetime.now() - timestamp
                cache_minutes = int(cache_age.total_seconds() / 60)
            else:
                cache_minutes = None
            
            sources_status.append({
                'name': source_id,
                'url': source['yuan_url'],
                'available': is_available,
                'cache_age': f"{cache_minutes} 分钟前" if cache_minutes is not None else "未检查"
            })
            
        return render_template('source_status.html', 
                              sources=sources_status, 
                              total_count=len(all_sources),
                              available_count=sum(1 for s in sources_status if s['available']))
    except Exception as e:
        logger.error(f"查看源状态出错: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"<pre>查看源状态出错: {str(e)}</pre>"

@app.route('/source_manage')
def source_manage():
    """
    源管理页面
    """
    try:
        # 获取所有源数据
        with open('tata.json', 'r', encoding='utf-8') as f:
            all_sources = json.load(f)
            
        return render_template('source_manage.html', sources=all_sources)
    except Exception as e:
        logger.error(f"获取源管理页面失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"<pre>获取源管理页面失败: {str(e)}</pre>"

@app.route('/add_source', methods=['POST'])
def add_source():
    """
    添加新源
    """
    try:
        # 获取表单数据
        yuan_name = request.form.get('yuan_name')
        yuan_url = request.form.get('yuan_url')
        yuan_api = request.form.get('yuan_api')
        yuan_cat = request.form.get('yuan_cat')
        yuan_detail = request.form.get('yuan_detail')
        
        # 验证必填字段
        if not all([yuan_name, yuan_url, yuan_api, yuan_cat, yuan_detail]):
            flash('所有字段都是必填的', 'danger')
            return redirect(url_for('source_manage'))
        
        # 读取现有源
        with open('tata.json', 'r', encoding='utf-8') as f:
            sources = json.load(f)
        
        # 检查源名是否重复
        for source in sources:
            if source['yuan_name'] == yuan_name:
                flash(f'源名称 "{yuan_name}" 已存在', 'danger')
                return redirect(url_for('source_manage'))
        
        # 添加新源
        new_source = {
            "yuan_name": yuan_name,
            "yuan_url": yuan_url,
            "yuan_api": yuan_api,
            "yuan_cat": yuan_cat,
            "yuan_detail": yuan_detail
        }
        sources.append(new_source)
        
        # 保存到文件
        with open('tata.json', 'w', encoding='utf-8') as f:
            json.dump(sources, f, ensure_ascii=False, indent=4)
        
        # 清除缓存
        global cache, source_availability_cache
        cache.clear()
        source_availability_cache.clear()
        
        # 重新初始化应用
        init_app()
        
        flash(f'成功添加源 "{yuan_name}"', 'success')
        return redirect(url_for('source_manage'))
    except Exception as e:
        logger.error(f"添加源失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        flash(f'添加源失败: {str(e)}', 'danger')
        return redirect(url_for('source_manage'))

@app.route('/edit_source', methods=['POST'])
def edit_source():
    """
    编辑源
    """
    try:
        # 获取表单数据
        index = int(request.form.get('index'))
        yuan_name = request.form.get('yuan_name')
        yuan_url = request.form.get('yuan_url')
        yuan_api = request.form.get('yuan_api')
        yuan_cat = request.form.get('yuan_cat')
        yuan_detail = request.form.get('yuan_detail')
        
        # 验证必填字段
        if not all([yuan_name, yuan_url, yuan_api, yuan_cat, yuan_detail]):
            flash('所有字段都是必填的', 'danger')
            return redirect(url_for('source_manage'))
        
        # 读取现有源
        with open('tata.json', 'r', encoding='utf-8') as f:
            sources = json.load(f)
        
        # 检查索引是否有效
        if index < 0 or index >= len(sources):
            flash('无效的源索引', 'danger')
            return redirect(url_for('source_manage'))
        
        # 检查源名是否重复 (排除当前编辑的源)
        old_name = sources[index]['yuan_name']
        if yuan_name != old_name:
            for i, source in enumerate(sources):
                if i != index and source['yuan_name'] == yuan_name:
                    flash(f'源名称 "{yuan_name}" 已存在', 'danger')
                    return redirect(url_for('source_manage'))
        
        # 更新源
        sources[index] = {
            "yuan_name": yuan_name,
            "yuan_url": yuan_url,
            "yuan_api": yuan_api,
            "yuan_cat": yuan_cat,
            "yuan_detail": yuan_detail
        }
        
        # 保存到文件
        with open('tata.json', 'w', encoding='utf-8') as f:
            json.dump(sources, f, ensure_ascii=False, indent=4)
        
        # 清除缓存
        global cache, source_availability_cache
        cache.clear()
        source_availability_cache.clear()
        
        # 如果编辑的是当前选择的源，更新session
        if session.get('selected_source') == old_name:
            session['selected_source'] = yuan_name
        
        # 如果编辑的是默认源，更新默认源
        global default_source
        if default_source == old_name:
            default_source = yuan_name
        
        flash(f'成功更新源 "{yuan_name}"', 'success')
        return redirect(url_for('source_manage'))
    except Exception as e:
        logger.error(f"编辑源失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        flash(f'编辑源失败: {str(e)}', 'danger')
        return redirect(url_for('source_manage'))

@app.route('/delete_source', methods=['POST'])
def delete_source():
    """
    删除源
    """
    try:
        # 获取源名称
        source_name = request.form.get('source_name')
        
        if not source_name:
            flash('源名称不能为空', 'danger')
            return redirect(url_for('source_manage'))
        
        # 读取现有源
        with open('tata.json', 'r', encoding='utf-8') as f:
            sources = json.load(f)
        
        # 查找要删除的源
        source_found = False
        for i, source in enumerate(sources):
            if source['yuan_name'] == source_name:
                del sources[i]
                source_found = True
                break
        
        if not source_found:
            flash(f'找不到源 "{source_name}"', 'danger')
            return redirect(url_for('source_manage'))
        
        # 保存到文件
        with open('tata.json', 'w', encoding='utf-8') as f:
            json.dump(sources, f, ensure_ascii=False, indent=4)
        
        # 清除缓存
        global cache, source_availability_cache
        cache.clear()
        source_availability_cache.clear()
        
        # 如果删除的是当前选择的源，更新session
        if session.get('selected_source') == source_name:
            if sources:
                session['selected_source'] = sources[0]['yuan_name']
            else:
                session.pop('selected_source', None)
        
        # 如果删除的是默认源，更新默认源
        global default_source
        if default_source == source_name:
            if sources:
                default_source = sources[0]['yuan_name']
            else:
                default_source = None
        
        # 重新初始化应用
        if not sources:
            flash('已删除最后一个源，请添加新源', 'warning')
        else:
            init_app()
            flash(f'成功删除源 "{source_name}"', 'success')
            
        return redirect(url_for('source_manage'))
    except Exception as e:
        logger.error(f"删除源失败: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        flash(f'删除源失败: {str(e)}', 'danger')
        return redirect(url_for('source_manage'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
