根据您的代码，我可以列出以下API及其参数和返回实例：
获取视频列表 API:
URL: {source['yuan_url']}{source['yuan_api']}
方法: GET
参数:
ac: 'detail'
pg: 页码 (整数)
t: 分类 ID (可选，来自 source['cat'])
返回实例:
```
     {
       "list": [
         {
           "vod_id": "12345",
           "vod_name": "示例电影",
           "vod_pic": "http://example.com/image.jpg",
           "vod_year": "2023",
           "vod_remarks": "HD",
           "vod_content": "这是一部示例电影的简介...",
           "type_id": "1"
         },
         // 更多视频...
       ],
       "class": [
         {
           "type_id": "1",
           "type_name": "电影"
         },
         // 更多分类...
       ]
     }
```
2. 搜索视频 API:
URL: {source['yuan_url']}{source['yuan_api']}
方法: GET
参数:
ac: 'detail'
wd: 搜索关键词
pg: 页码 (整数)
t: 分类 ID (可选)
返回实例: 与获取视频列表 API 相同
获取视频详情 API:
URL: {source['yuan_url']}{source['yuan_api']}
方法: GET
参数:
ac: 'detail'
ids: 视频 ID
返回实例:
```
     {
       "list": [
         {
           "vod_id": "12345",
           "vod_name": "示例电影",
           "vod_pic": "http://example.com/image.jpg",
           "vod_year": "2023",
           "vod_remarks": "HD",
           "vod_content": "这是一部示例电影的详细简介...",
           "vod_actor": "演员A,演员B",
           "vod_director": "导演X",
           "vod_play_url": "第01集$http://example.com/ep1.m3u8#第02集$http://example.com/ep2.m3u8"
         }
       ]
     }
```
获取搜索建议 API (这是一个本地实现，不是外部 API):
URL: '/search_suggestions'
方法: GET
参数:
q: 搜索查询
返回实例:
```
     [
       "查询 电影",
       "查询 电视剧",
       "查询 动漫"
     ]
```
请注意，这些 API 的具体实现和返回格式可能会根据您使用的实际数据源有所不同。您可能需要根据实际情况调整代码中的解析逻辑。