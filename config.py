"""
配置文件 - 所有API密钥、SESSION、基础配置等
"""
import os


def _load_local_env():
    """读取本地 .env（不入库），仅开发/本机使用；云端请用平台环境变量"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass
_load_local_env()


# ==================== 哥斯拉（拍照效率） ====================
GODZILLA_CONFIG = {
    'base_url': 'https://wirelessgate.aihuishou.com/creative-monster-server',
    'session': os.environ.get('GODZILLA_SESSION', ''),
    'operation_center_id': 3,
    'machine_type': 1,
    'page_size': 100,
    'total_duration_key': '总时长',
}

# ==================== 魔镜（质检/自动化效率） ====================
MIRROR_CONFIG = {
    'base_url': 'https://wirelessgate.aihuishou.com/creative-mirror-service',
    'session': os.environ.get('MIRROR_SESSION', ''),
    'api_path': '/fe/v1/screenDefectReport/page',
    'page_size': 500,  # 魔镜支持较大分页，减少请求次数
    'place_id': 6,  # 优检成都检测中心
    'max_pages': 50,  # 最大拉取页数，防止死循环
}

# ==================== Watcher（abdavinci报表，综合效率） ====================
WATCHER_CONFIG = {
    'base_url': 'https://abdavinci.aihuishou.com/api/v3/statistics/share/data',
    'session': os.environ.get('WATCHER_SESSION', ''),
    'filter_field': '运营中心',
    'filter_value': '优检成都检测中心',
    'godzilla_detail': {
        'widget_id': '10554',
        'share_token': 'DA1C3E7DAF7EC46FDC39861F361C78B7517FC6CEF4F1ED425FFF75A2CB7527D3ABD4F0725AF12462EE9485E57D5234014F4761B271367851F7DD854883B20C7C3707204AFBFC9E06F119D421B3DE080486A5B9D71B7755824F98B3878C6273DD12602E993DC452DFD3206FCE8D555B6A96E1D555B9BE0B42BDCF7341B83F0E0580D97E83AC874258546F3F827123F6865A26BAAFA0F0421235E814D33428BD29E55DFD91AEF01561B47504A5245FC11F9B88FE62F9E78B8591EC9821E5CF0695B0EF702120C2E18CED158C10FA6299C4666E8F5F76F90D961CB9E315EF296E01FDF126EBA00AC738D21B99DE85FAFA0C2AA54B324B7455D554697478C910FFABCD6260BEA156D51F325F29B2C548129D',
        'dashboard_id': '2512',
        'role_ids': '366BE0BD5EA97D3B82AD54E7159C296C',
        'page_size': 10000,
        'date_param_name': 'date_begin',
        'groups': ['采集日期', '操作人员工号', '操作人员姓名', '岗位标签', '服务标准'],
        'aggregators': [
            {'column': '总耗时长', 'func': 'sum'},
            {'column': '纯设备运行时长', 'func': 'sum'},
            {'column': '延迟时长s', 'func': 'sum'},
        ],
        'fields': {
            'date': '采集日期',
            'employee_no': '操作人员工号',
            'employee_name': '操作人员姓名',
            'duration': '纯设备运行时长',
            'total_duration': '总耗时长',
            'delay': '延迟时长s',
            'position_tag': '岗位标签',
            'service_std': '服务标准',
        },
    },
    'mirror_detail': {
        'widget_id': '6177',
        'share_token': 'DA1C3E7DAF7EC46FDC39861F361C78B7517FC6CEF4F1ED425FFF75A2CB7527D3ABD4F0725AF12462EE9485E57D523401581F91A424306D780FA7D6DE89407F7E2A1DFFC1C1158D535E8DBB9C20429E7E37CB413A44466116A62F7BF54FABD54F12602E993DC452DFD3206FCE8D555B6ACFF43955EC16C7C344CE2DD77A62926580D97E83AC874258546F3F827123F6865A26BAAFA0F0421235E814D33428BD29319BE63B76DCD2E6CA5B71F910FDE410A8974AFEEA5D2AC4558AE590F86A84BCE5090DDA945786B49CD951A28C3FA36DA393C139BFDA09D243F72E1DBEF7882B29D6251E75C2B4602E6F27B4AF96C509261C4FF61068AE1216026FCB1641CA2CC30D83B7635DFFCF4C268B7A259AF668',
        'filter_field': '运营中心名称',
        'filter_value': '优检成都检测中心',
    },
    'attendance_detail': {
        'widget_id': '10930',
        'share_token': 'DA1C3E7DAF7EC46FDC39861F361C78B7517FC6CEF4F1ED425FFF75A2CB7527D3ABD4F0725AF12462EE9485E57D52340151FE509E20C0EBD9D9525C2DC47FB0D354E5788B5C01B112E206B98028CDD939E87F5A440742132F7DEBD097905B083E12602E993DC452DFD3206FCE8D555B6ADF9BD1FAE0136506BE94D40D86AE8ACD80D97E83AC874258546F3F827123F6865A26BAAFA0F0421235E814D33428BD298E3776EC78DDA522150D891C82C34CD4B1CAEC03FE84E72C345054A529121E1A16BBC48B005A989BAEEDD5994DBAAFFA947291E00984128488F0B35BFA9E4B452E8DA291E85E8246A60B2A612E518619A7CD5FFD17B0710EFA9640549317CC477D3122E7C8F126CFF54B9EB34CF099BE',
        'filter_field': 'C3',
        'filter_value': '成都运作中心',
        'role_ids': '0E18F8F445A62FF29E287D24B691C409',
        'date_param_name': 'date_begin',
    },
    # ===== 拍照魔方（拍机堂拍照 PJTPZ，步骤一拍照方式='拍照魔方'）=====
    # 数据源：拍照质检看板（abdavinci）widget 3535「拍照明细」
    # 说明：share_token 为看板级 token（与 9252 共用），widget_id=3535, dashboard_id=1325
    # acw_tc 为阿里云 WAF 挑战 cookie，短时效；过期后需用户重新提供浏览器 Cookie 里的 acw_tc
    'photo_detail': {
        'widget_id': '3535',
        'share_token': 'DA1C3E7DAF7EC46FDC39861F361C78B7517FC6CEF4F1ED425FFF75A2CB7527D3ABD4F0725AF12462EE9485E57D523401DBA42DAA32F77BD3C5646D5E6B0D6046EF8BE28F2523EB588AA67989F43D29E3E17443F06FD6DC62C81D0F3F0C377A05DC68C882B79751D921B6789B588FB4924FAD0CA24518E219D9F1FF25A70A727F54BF53EBC9406E399366EBF2140ED6D4AFE4BEEDE0A74EE59C6C1A5E7DEFFF69AD9849112D9F0DD7BD51D1F260C27017B0638DB153E48B806C50578F9958EE60CF8B49EF982933C94DC5354E02AC0E9B8D1CE0690ED3CCB4304F1FD1D335E201D6791D4898125212DBFA5457FC99907DE98AA294BE26320B08A4F84A923D93764B45B15D35DA33E7FB578BEB8B78247A',
        # 注意：不在此写死 session。该 share 看板的 widget 用 auto_relogin 得到的
        # yashi 会话即可访问（实测可用）；写死张雅诗本人会话反而会因过期后重试
        # 仍用旧值而归零。acw_tc 为 WAF 挑战 cookie，初始值会被 417 响应下发的新值刷新。
        'acw_tc': '2f6fc15617843668316755724e009ad8967ae2097d42ce6fcc811067d62f04',
        'dashboard_id': '1325',
        'role_ids': 'D5DBD25BF904098010B81BC4995EF327',
        'date_param_name': 'date_begin',
        'page_size': 500,
        'filters': [
            '"运营中心名称" in (\'优检成都检测中心\')',
            '"步骤一拍照方式" in (\'拍照魔方\')',
        ],
        'groups': ['拍照移交完成日期', '步骤一拍照方式', '服务标准', '步骤一处理人'],
        'aggregators': [
            {'column': '物品量', 'func': 'udf', 'udf': 'count(distinct "物品编号")'},
            {'column': '拍照时长H', 'func': 'udf', 'udf': 'round(sum("拍照时长")*1.00/3600,2)'},
            {'column': '步骤一时长', 'func': 'sum'},
            # 首末台时间跨度（用于纯拍照魔方人员工时计算：第一台→最后一台，>13:30 剔除1小时）
            {'column': '步骤一完成时间_first', 'func': 'udf', 'udf': 'min("步骤一完成时间")'},
            {'column': '步骤一完成时间_last', 'func': 'udf', 'udf': 'max("步骤一完成时间")'},
        ],
        'fields': {
            'employee_name': '步骤一处理人',
            'count': '物品量',
            'duration': '拍照时长H',
            'op_duration': '步骤一时长',
            'service_standard': '服务标准',
            'date': '拍照移交完成日期',
            # 实际拍照方式（用于护栏：只保留「拍照魔方」，其余方式由各自数据源负责，避免重复统计）
            'photo_method_raw': '步骤一拍照方式',
            # 首末台完成时间（per 聚合组：该人员当日 first/last 台步骤一完成时间）
            'first_time': '步骤一完成时间_first',
            'last_time': '步骤一完成时间_last',
        },
    },
    # ===== 瑕疵图拍照明细（按人员，拍照质检看板 widget 3535 拍照明细）=====
    # 与 photo_detail 同表(rpt_centre_photograph_operate_detail)，但过滤条件改为
    # 是否完成瑕疵拍照='是'，并按 步骤一处理人 聚合 → 得到每人瑕疵图拍照量（带姓名）。
    # 用途：瑕疵图由拍照组任意人员拍摄，需按姓名并入对应人员综合处理量/效率，
    #       而非仅绑在「瑕疵图」岗位人员（如欧紫月）一人身上。
    'flaw_photo_detail': {
        'widget_id': '3535',
        'share_token': 'DA1C3E7DAF7EC46FDC39861F361C78B7517FC6CEF4F1ED425FFF75A2CB7527D3ABD4F0725AF12462EE9485E57D523401DBA42DAA32F77BD3C5646D5E6B0D6046EF8BE28F2523EB588AA67989F43D29E3E17443F06FD6DC62C81D0F3F0C377A05DC68C882B79751D921B6789B588FB4924FAD0CA24518E219D9F1FF25A70A727F54BF53EBC9406E399366EBF2140ED6D4AFE4BEEDE0A74EE59C6C1A5E7DEFFF69AD9849112D9F0DD7BD51D1F260C27017B0638DB153E48B806C50578F9958EE60CF8B49EF982933C94DC5354E02AC0E9B8D1CE0690ED3CCB4304F1FD1D335E201D6791D4898125212DBFA5457FC99907DE98AA294BE26320B08A4F84A923D93764B45B15D35DA33E7FB578BEB8B78247A',
        'acw_tc': '2f6fc15617843668316755724e009ad8967ae2097d42ce6fcc811067d62f04',
        'dashboard_id': '1325',
        'role_ids': 'D5DBD25BF904098010B81BC4995EF327',
        'date_param_name': 'date_begin',
        'page_size': 500,
        'filters': [
            '"运营中心名称" in (\'优检成都检测中心\')',
            '"是否完成瑕疵拍照" in (\'是\')',
        ],
        'groups': ['拍照移交完成日期', '步骤一处理人', '服务标准'],
        'aggregators': [
            {'column': '物品量', 'func': 'udf', 'udf': 'count(distinct "物品编号")'},
        ],
        'fields': {
            'employee_name': '步骤一处理人',
            'count': '物品量',
        },
    },
    # ===== 瑕疵图拍照完成量（拍照质检看板 widget 7757）=====
    # 用户提供的 cURL：过滤 是否完成瑕疵拍照='是' 且 运营中心名称='优检成都检测中心'
    # 按 创建日期/运营中心名称 聚合，count(1) 得当日瑕疵图拍照完成量（体积指标，无人员维度，用于卡片总量）
    'flaw_photo': {
        'widget_id': '7757',
        'share_token': 'DA1C3E7DAF7EC46FDC39861F361C78B7517FC6CEF4F1ED425FFF75A2CB7527D3ABD4F0725AF12462EE9485E57D523401DBA42DAA32F77BD3C5646D5E6B0D6046EF8BE28F2523EB588AA67989F43D29E3E17443F06FD6DC62C81D0F3F0C377A05DC68C882B79751D921B6789B588FB4924FAD0CA24518E219D9F1FF25A70A727F54BF53EBC9406E399366EBF2140ED6D4AFE4BEEDE0A74EE59C6C1A5E7DEFFF69AD9849112D9F0DD7BD51D1F260C27017B0638DB153E48B806C50578F9958EE60CF8B49EF982933C94DC5354E02AC0E9B8D1CE0690ED3CCB4304F1FD1D335E201D6791D4898125212DBFA5457FC99907DE98AA294BE26320B08A4F84A923D93764B45B15D35DA33E7FB578BEB8B78247A',
        'acw_tc': '2f6fc15617843668316755724e009ad8967ae2097d42ce6fcc811067d62f04',
        'dashboard_id': '1325',
        'role_ids': 'D5DBD25BF904098010B81BC4995EF327',
        'date_param_name': 'date_begin',
        'page_size': 500,
        'filters': [
            '"是否完成瑕疵拍照" in (\'是\')',
            '"运营中心名称" in (\'优检成都检测中心\')',
        ],
        'groups': ['创建日期', '运营中心名称'],
        'aggregators': [
            {'column': '瑕疵图拍照完成量', 'func': 'udf', 'udf': 'count(1)'},
        ],
        'fields': {},
    },
}

# ==================== 金山文档（APP效率登记表） ====================
KDOCS_CONFIG = {
    # 方式1：API Token（推荐，可自动读取所有文档）
    'token': '',  # 填入WPS金山文档Token
    
    # 方式2：文件夹分享链接（自动遍历文件夹内所有表格）
    # 格式：https://www.kdocs.cn/join/xxxxx
    'folder_share_url': 'https://www.kdocs.cn/join/g93lfym',
    'teamid': '2437879392',
    'cookie': 'V02SZ_LELVXce-ogw9Nyjwm3MnP9sfQ00ad3728f0011aa69d4',
    
    # 方式3：单文件分享链接（兜底，手动指定）
    # 格式：{ '表格名称': '分享链接URL' }
    'app_efficiency_links': {},
    
    # 29个人员APP登记表（自动从文件夹发现）
    'staff_registers': {},
}

# ==================== APP效率达标标准 ====================
APP_ANDROID_STANDARD = 26.3  # 安卓装跑 UPPH 标准
APP_IOS_STANDARD = 40.5      # 苹果测跑 UPPH 标准

# ==================== X光（X-Ray）设备效率 ====================
# 成都X光设备列表（以设备编号为维度，非人员维度）
XRAY_CHENGDU_MACHINES = ['DESKTOP-L63QQ6A', 'XRayBig0005']
# X光单台 UPPH 达标标准(台/小时)。业务给定达标目标 = 659.34
XRAY_STANDARD_UPH = 659.34
# X光系统真实接口(apiEndpoint)，取自前端 JS: m.Z.apiEndpoint
XRAY_BASE_URL = os.environ.get('XRAY_BASE_URL', 'https://wirelessgate.aihuishou.com/creative-x-ray-server')
# X光系统登录 SESSION（portal.aihuishou.com 登录后浏览器 Cookie 中的 SESSION 值；有效期有限，失效需重新提供）
XRAY_SESSION = os.environ.get('XRAY_SESSION', '')
# 旧 CAS 账号（已弃用，X光改用 SESSION 认证）
XRAY_USERNAME = os.environ.get('XRAY_USERNAME', '')
XRAY_PASSWORD = os.environ.get('XRAY_PASSWORD', '')

# ==================== 光合（lightcore）SESSION 认证 ====================
# 光合系统真实接口: {base}/fe/take_picture_task/search  (GET)
# 认证: 在 Cookie 中携带 SESSION（与 X光 同源 wirelessgate.aihuishou.com，SESSION cookie 通用）
# 可在环境变量 GUANGHE_SESSION 中以英文逗号分隔提供多个 token，依次回退尝试
GUANGHE_BASE_URL = os.environ.get('GUANGHE_BASE_URL', 'https://wirelessgate.aihuishou.com/creative-lightcore')
GUANGHE_SESSIONS = [s.strip() for s in os.environ.get(
    'GUANGHE_SESSION',
    'OTgwZTA3MWEtMWM4NC00MmRkLWIzMzEtNzA3NWVhNjM0NDg1,OWY1OWY4OTItNmQxNi00NzhkLWExZDktNmQ2NjZhNjZhYjBh'
).split(',') if s.strip()]

# ==================== 拍照效率达标标准（哥斯拉/光合/拍照魔方合并计算用） ====================
# 哥斯拉拍照：按服务标准代码区分全职/兼职目标（台/小时）
# 业务标准值（2026-07-17 用户给定）：
#   拍拍拍照(PPPZ)           全职130 兼职120
#   拍拍门店拍照(PPMDPZ)     全职130 兼职120
#   拍机堂拍照(PJTPZ)哥斯拉  全职191.5 兼职191.5
#   C2B门店入哥斯拉(ZJ0074)  全职130 兼职120
#   QTZJ 哥斯拉侧未明确服务名，沿用 130/120 默认
PHOTO_SERVICE_STANDARDS = {
    'QTZJ': {'fulltime_standard': 130.0, 'parttime_standard': 120.0},   # 哥斯拉侧未明确服务名(默认)
    'ZJ0074': {'fulltime_standard': 130.0, 'parttime_standard': 120.0},  # C2B门店入哥斯拉
    'PPMDPZ': {'fulltime_standard': 130.0, 'parttime_standard': 120.0},  # 拍拍门店拍照
    'PPPZ': {'fulltime_standard': 130.0, 'parttime_standard': 120.0},    # 拍拍拍照
    'PJTPZ': {'fulltime_standard': 245.0, 'parttime_standard': 245.0},  # 拍机堂拍照(哥斯拉系统) 达标标准=245
    'C2B': {'fulltime_standard': 130.0, 'parttime_standard': 120.0},     # C2B入哥斯拉(兼容)
}
# 哥斯拉(创意怪兽)拍照数据源「只统计」的拍照类目白名单（serviceStandardId -> 中文名）。
# 其余类目（如 QTZJ 全托质检=手机质检）不计入拍照效率。
GODZILLA_PHOTO_CATEGORIES = {
    'PPPZ':   '拍拍拍照',
    'PPMDPZ': '拍拍门店拍照',
    'PJTPZ':  '拍机堂拍照',
    'ZJ0074': 'C2B门店入哥斯拉',
    'C2B':    'C2B门店入哥斯拉',
}
# 拍照魔方（PJTPZ via Watcher/拍照魔方方式）统一标准（台/小时）：全职/兼职均 191.5
PJTPZ_PHOTO_CUBE_STANDARD = 191.5
# 光合（lightcore）按品类达标标准（台/小时），业务给定 2026-07-17
# 品类名称 -> 达标 UPH（全职/兼职统一，用户未区分）
GUANGHE_CATEGORY_STANDARDS = {
    # 组A = 27.4（多数3C / 影像 / 配件）
    '平板电脑': 27.4, '镜头': 27.4, '单反（微单）机身': 27.4, '单反（微单）套机': 27.4,
    '拍立得': 27.4, '数码相机': 27.4, '运动相机': 27.4, '智能音箱': 27.4,
    '智能手写笔': 27.4, '摄像机': 27.4, 'CPU': 27.4, '无人机': 27.4,
    '投影仪': 27.4, '拍照配件/云台': 27.4, '显卡': 27.4,
    # 组B = 笔记本
    '笔记本': 10.7,
    # 组C = 智能手表 / 耳机耳麦
    '智能手表': 7.9, '耳机/耳麦': 7.9,
    # 组D = 游戏机
    '游戏机': 7.9,
}
# lightcore productCategoryId -> 品类名称（通过采样 productName 反推确认）
#   5 = 笔记本（天选/MacBook/ThinkPad），6 = 平板电脑（iPad/MatePad），64 = 智能手表（华为 WATCH）
# 其余品类ID待补充：光合品类字典接口(/fe/category/list)后端返回500不可用；
# 遇到新ID时归入 GUANGHE_DEFAULT_STANDARD（多数3C归入27.4组）
GUANGHE_CATEGORY_ID_TO_NAME = {
    5: '笔记本',
    6: '平板电脑',
    64: '智能手表',
}
# 未匹配到具体品类的默认标准（多数3C归入 27.4 组）
GUANGHE_DEFAULT_STANDARD = 27.4

# ==================== 瑕疵图拍照达标标准（台/小时） ====================
# 业务给定：C2B瑕疵图=63.6, 拍拍瑕疵图=36.7
FLAW_PHOTO_STANDARDS = {
    'C2B':   63.6,    # C2B瑕疵图
    '拍拍':  36.7,    # 拍拍瑕疵图（含 PPPZ / PPMDPZ 来源）
}

# ==================== 效率计算配置 ====================
EFFICIENCY_CONFIG = {
    'lunch_break_hour': 13,
    'lunch_break_duration_hours': 1,
    'fulltime_standard': 58,  # 哥斯拉全职标准 UPH
    'parttime_standard': 52,  # 哥斯拉兼职标准 UPH
    'mirror_fulltime_standard': 183.7,  # 魔镜标准（不分全职兼职）
    'mirror_parttime_standard': 183.7,  # 魔镜标准（不分全职兼职）
    'work_start_hour': 8,
    'work_end_hour': 21,
    'exclude_hours': [12],  # 排除午休时段
}

# ==================== 缓存配置 ====================
CACHE_TTL_SECONDS = 300  # 5分钟缓存

# ==================== 数据库路径 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'efficiency.db')

# ==================== 员工花名册路径 ====================
STAFF_ROSTER_PATH = os.path.join(BASE_DIR, 'staff_roster.json')

# ==================== 飞书多维表格（人员管理） ====================
FEISHU_CONFIG = {
    'app_id': 'cli_aab80a5475a1dcc3',
    'app_secret': os.environ.get('FEISHU_APP_SECRET', ''),
    'base_token': 'SuLebPlCQaeJhPssMZncz4wEnsg',
    'table_id': 'tbljuasjgQRZDN0I',
    'view_id': 'vewajINTG2',
}

# ==================== 飞书云文档（效率趋势） ====================
# 使用Yami应用（飞书渠道），云文档/邮件权限齐全
EFFICIENCY_TREND_CONFIG = {
    'app_id': 'cli_aab8ee79953a5beb',
    'app_secret': os.environ.get('FEISHU_TREND_APP_SECRET', ''),
    'spreadsheet_token': 'EnRQs4LwPh7tU0tzrhecKwzznEf',
    'output_sheet_id': 'Ha88TR',   # 产出sheet（读真实数值）
    'attend_sheet_id': 'L5vzjP',   # 出勤sheet（读真实数值）
    # 大仓辅助效率达成（折线图用）- 成都
    'baseline_row': 152,   # 基准行
    'current_row': 153,    # 当前行
    'date_row': 1,         # 日期所在行
    # 10个效率指标卡片（成都，基准行, 当前行）
    # 规律：每个指标30行(10城市*3行)，成都在第7组=起始行+18
    'metric_rows': {
        'app': {'name': 'APP效率达成', 'baseline_row': 368, 'current_row': 369},
        'godzilla': {'name': '哥斯拉效率达成', 'baseline_row': 506, 'current_row': 507},
        'mirror': {'name': '魔镜效率达成', 'baseline_row': 533, 'current_row': 534},
        'xray': {'name': 'Xray效率达成', 'baseline_row': 452, 'current_row': 453},
        'charge_transfer': {'name': '充电移交效率达成', 'baseline_row': 476, 'current_row': 477},
        'b_photo': {'name': 'B端拍照效率达成', 'baseline_row': 398, 'current_row': 399},
        'c_photo': {'name': 'C端拍照效率达成', 'baseline_row': 422, 'current_row': 423},
        'defect': {'name': '瑕疵图效率达成', 'baseline_row': 560, 'current_row': 561},
        'privacy': {'name': '隐私清除效率达成', 'baseline_row': 329, 'current_row': 330},
        'info_repair': {'name': '信息维修效率达成', 'baseline_row': 587, 'current_row': 588},
    },
    # 指标卡片顺序
    'metric_order': ['app', 'godzilla', 'mirror', 'xray', 'charge_transfer',
                     'b_photo', 'c_photo', 'defect', 'privacy', 'info_repair'],
}
TREND_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trend_cache.json')
METRIC_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'metric_cache.json')

# ==================== 飞书邮件（运营质量指标） ====================
# 使用Yami应用（飞书渠道），邮箱权限齐全
QUALITY_MAIL_CONFIG = {
    'app_id': 'cli_aab8ee79953a5beb',
    'app_secret': os.environ.get('FEISHU_TREND_APP_SECRET', ''),
    # 用户身份token保存路径（首次授权后自动保存）
    'mail_token_path': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'mail_user_token.json'),
    # 邮箱地址（用户飞书邮箱）
    'mailbox': 'yashi.zhang@atrenew.com',
    # 两类邮件搜索关键词
    'app_mail_keyword': 'APP执行、失误',
    'reshoot_mail_keyword': '运中门店重拍率数据',
    # 要提取的指标（邮件正文中的表格或文本）
    'metrics': {
        # APP类
        'app_execution': {'name': 'APP执行率', 'mail_type': 'app'},
        'app_coverage': {'name': 'APP覆盖率', 'mail_type': 'app'},
        'app_error': {'name': 'APP失误率', 'mail_type': 'app'},
        # 重拍类
        'godzilla_reshoot_rate': {'name': '哥斯拉重拍率', 'mail_type': 'reshoot'},
        'godzilla_reshoot_count': {'name': '哥斯拉重拍量', 'mail_type': 'reshoot'},
        'reshoot_7d_rate': {'name': '近7天重拍率', 'mail_type': 'reshoot'},
    },
    # 城市筛选
    'city_filter': '成都',
}
QUALITY_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'quality_cache.json')
