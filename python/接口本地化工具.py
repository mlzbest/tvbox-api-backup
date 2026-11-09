#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 接口本地化工具 
============================================================
本工具 = 抓取/解密/规范化 + 本地化下载

架构原则：
    阶段一【获取 JSON】
    阶段二【本地化】

配置：
    同目录 api_list.json（优先）或 api_list.py（备用）
    api_list.json 格式：
    {
      "API_LIST": [
        ["更新专用接口", "https://0.12yue.de5.net/tvbox/更新专用接口.json"]
      ],
      "API_MIRRORS": {
        "更新专用接口": [
          "https://0.12yue.de5.net/tvbox/更新专用接口.json",
          "https://0.12yue.de5.net/tvbox/更新专用接口.json"
        ]
      }
    }

统一锁定逻辑：
    sites[] 组内，所有"明显文件链接"（带文件后缀）都下载本地化。

URL 判定：
    不按 http 截断链接，整条下载。

文件名处理：
    - URL 编码（%E6%96%97）自动还原成中文名（斗鱼）
    - 文件名里的特殊符号去掉（只保留中文/字母/数字/点/横杠）
    - 同名编号不用下划线：spider.jar → spider2.jar → spider3.jar
============================================================
"""

# ============================================================
# ★★★★★ 用户设置区（只改这里，下面的代码不要动）★★★★★
# ============================================================

FORCE_REDOWNLOAD = True
OUTPUT_ROOT = "tvbox"

# --- 后缀白名单 ---
FILE_EXT_WHITELIST = [
    ".jar",
    ".js",
    ".py",
    ".json",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".zip",
    ".apk",
    ".node",
]

# --- 后缀黑名单（优先级更高）---
FILE_EXT_BLACKLIST = [
    ".m3u",
    ".xml",
]

# --- 保留链接 ---
KEEP_ONLINE_KEYWORDS = [
    "http://www.饭太硬.net/tvfan/Cloud-drive.txt",
]

# --- sites 之外整体保留在线的字段 ---
KEEP_ONLINE_FIELDS = {
    "wallpaper",
    "parses",
    "rules",
    "lives",
}

# --- spider 特殊处理 ---
SPIDER_FORCE_RENAME = True
SPIDER_LOCAL_NAME = "spider.jar"

# --- 实时动态显示设置 ---
SHOW_SITE_PROGRESS = True
SHOW_DOWNLOAD_DETAIL = True
SHOW_CACHE_HIT = False
SHOW_SKIPPED_LINKS = False

# ============================================================
# ★★★★★ 用户设置区结束 ★★★★★
# ============================================================


# ============================================================
# ========== 第一部分：tvbox_get_api.py 全部逻辑 ==========
# ============================================================

import re
import json
import base64
import os
import sys
import binascii
import gzip
import time
import shutil
import hashlib
import functools
from datetime import datetime, timezone, timedelta
from threading import Thread
from queue import Queue
from urllib.parse import unquote


# ---------- 时间工具 ----------
def beijing_now():
    """返回当前北京时间（脚本统一使用，避免依赖运行机本地时区 UTC）"""
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))


def today_str():
    """当前北京时间，格式 YYYYMMDD，用于 list.txt 日期列"""
    return beijing_now().strftime("%Y%m%d")


# ================== 配置加载（JSON / PY 双版本） ==================
def _load_api_config():
    """
    优先级：
    1. api_list.json（如果存在）
    2. api_list.py（默认）
    """
    json_path = "api_list.json"
    py_module = "api_list"

    # ---- JSON 版 ----
    if os.path.exists(json_path):
        print(f"  📄 使用配置文件: {json_path}")
        with open(json_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        api_list = [tuple(x) for x in cfg.get("API_LIST", [])]
        api_mirrors = cfg.get("API_MIRRORS", {})
        return api_list, api_mirrors

    # ---- PY 版 ----
    try:
        print(f"  📄 使用配置文件: {py_module}.py")
        import importlib
        mod = importlib.import_module(py_module)
        return mod.API_LIST, mod.API_MIRRORS
    except Exception as e:
        print(f"  ⚠ 未找到 {py_module}.py，使用空配置（自测模式）: {e}")
        return [], {}


# ================== 网络库兼容 ==================
try:
    import requests
    HAVE_REQUESTS = True
except Exception:
    HAVE_REQUESTS = False

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
    HAVE_URLLIB = True
except Exception:
    HAVE_URLLIB = False

if HAVE_REQUESTS:
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

# ====================== 调试开关 ======================
DEBUG = "--debug" in sys.argv


def dbg(msg):
    if DEBUG:
        print(f"[DBG] {msg}")


# ======================================================================
# 超时装饰器（通用解决方案）
# ======================================================================
class TimeoutError(Exception):
    pass


def timeout(seconds):
    """函数超时装饰器，支持 Windows 和 Unix"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result_queue = Queue()

            def target():
                try:
                    result = func(*args, **kwargs)
                    result_queue.put(('success', result))
                except Exception as e:
                    result_queue.put(('error', e))

            thread = Thread(target=target)
            thread.daemon = True
            thread.start()
            thread.join(seconds)

            if thread.is_alive():
                raise TimeoutError(f"Function {func.__name__} timed out after {seconds} seconds")

            status, value = result_queue.get()
            if status == 'error':
                raise value
            return value
        return wrapper
    return decorator


# ======================================================================
# AES-128-CBC + PKCS7（纯标准库实现）
# ======================================================================
class AES128:
    RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]
    SBOX = [
        0x63,0x7C,0x77,0x7B,0xF2,0x6B,0x6F,0xC5,0x30,0x01,0x67,0x2B,0xFE,0xD7,0xAB,0x76,
        0xCA,0x82,0xC9,0x7D,0xFA,0x59,0x47,0xF0,0xAD,0xD4,0xA2,0xAF,0x9C,0xA4,0x72,0xC0,
        0xB7,0xFD,0x93,0x26,0x36,0x3F,0xF7,0xCC,0x34,0xA5,0xE5,0xF1,0x71,0xD8,0x31,0x15,
        0x04,0xC7,0x23,0xC3,0x18,0x96,0x05,0x9A,0x07,0x12,0x80,0xE2,0xEB,0x27,0xB2,0x75,
        0x09,0x83,0x2C,0x1A,0x1B,0x6E,0x5A,0xA0,0x52,0x3B,0xD6,0xB3,0x29,0xE3,0x2F,0x84,
        0x53,0xD1,0x00,0xED,0x20,0xFC,0xB1,0x5B,0x6A,0xCB,0xBE,0x39,0x4A,0x4C,0x58,0xCF,
        0xD0,0xEF,0xAA,0xFB,0x43,0x4D,0x33,0x85,0x45,0xF9,0x02,0x7F,0x50,0x3C,0x9F,0xA8,
        0x51,0xA3,0x40,0x8F,0x92,0x9D,0x38,0xF5,0xBC,0xB6,0xDA,0x21,0x10,0xFF,0xF3,0xD2,
        0xCD,0x0C,0x13,0xEC,0x5F,0x97,0x44,0x17,0xC4,0xA7,0x7E,0x3D,0x64,0x5D,0x19,0x73,
        0x60,0x81,0x4F,0xDC,0x22,0x2A,0x90,0x88,0x46,0xEE,0xB8,0x14,0xDE,0x5E,0x0B,0xDB,
        0xE0,0x32,0x3A,0x0A,0x49,0x06,0x24,0x5C,0xC2,0xD3,0xAC,0x62,0x91,0x95,0xE4,0x79,
        0xE7,0xC8,0x37,0x6D,0x8D,0xD5,0x4E,0xA9,0x6C,0x56,0xF4,0xEA,0x65,0x7A,0xAE,0x08,
        0xBA,0x78,0x25,0x2E,0x1C,0xA6,0xB4,0xC6,0xE8,0xDD,0x74,0x1F,0x4B,0xBD,0x8B,0x8A,
        0x70,0x3E,0xB5,0x66,0x48,0x03,0xF6,0x0E,0x61,0x35,0x57,0xB9,0x86,0xC1,0x1D,0x9E,
        0xE1,0xF8,0x98,0x11,0x69,0xD9,0x8E,0x94,0x9B,0x1E,0x87,0xE9,0xCE,0x55,0x28,0xDF,
        0x8C,0xA1,0x89,0x0D,0xBF,0xE6,0x42,0x68,0x41,0x99,0x2D,0x0F,0xB0,0x54,0xBB,0x16,
    ]

    @staticmethod
    def _sub_word(w):
        return (AES128.SBOX[(w >> 24) & 0xFF] << 24 |
                AES128.SBOX[(w >> 16) & 0xFF] << 16 |
                AES128.SBOX[(w >> 8) & 0xFF] << 8 |
                AES128.SBOX[w & 0xFF])

    @staticmethod
    def _rot_word(w):
        return ((w << 8) & 0xFFFFFFFF) | ((w >> 24) & 0xFF)

    @staticmethod
    def _expand_key(key):
        Nk, Nr = 4, 10
        w = [0] * (4 * (Nr + 1))
        for i in range(Nk):
            w[i] = (key[4*i] << 24) | (key[4*i+1] << 16) | (key[4*i+2] << 8) | key[4*i+3]
        for i in range(Nk, 4 * (Nr + 1)):
            temp = w[i - 1]
            if i % Nk == 0:
                temp = AES128._sub_word(AES128._rot_word(temp)) ^ (AES128.RCON[i // Nk] << 24)
            w[i] = w[i - Nk] ^ temp
        out = bytearray(16 * (Nr + 1))
        for i in range(4 * (Nr + 1)):
            out[4*i]   = (w[i] >> 24) & 0xFF
            out[4*i+1] = (w[i] >> 16) & 0xFF
            out[4*i+2] = (w[i] >> 8) & 0xFF
            out[4*i+3] = w[i] & 0xFF
        return bytes(out)

    @staticmethod
    def _xtime(b):
        return ((b << 1) ^ (0x1B if b & 0x80 else 0)) & 0xFF

    @staticmethod
    def _inv_sub_bytes(state):
        inv = [0] * 256
        for i in range(256):
            inv[AES128.SBOX[i]] = i
        return bytes(inv[b] for b in state)

    @staticmethod
    def _inv_shift_rows(state):
        s = list(state)
        for row, shift in [(1, 1), (2, 2), (3, 3)]:
            base = [s[row + 4*c] for c in range(4)]
            base = base[-shift:] + base[:-shift]
            for c in range(4):
                s[row + 4*c] = base[c]
        return bytes(s)

    @staticmethod
    def _inv_mix_columns(state):
        def mul(a, b):
            r = 0
            while b:
                if b & 1:
                    r ^= a
                a = AES128._xtime(a)
                b >>= 1
            return r
        s = list(state)
        for c in range(4):
            i = 4*c
            a0, a1, a2, a3 = s[i], s[i+1], s[i+2], s[i+3]
            s[i]   = mul(a0,0x0e) ^ mul(a1,0x0b) ^ mul(a2,0x0d) ^ mul(a3,0x09)
            s[i+1] = mul(a0,0x09) ^ mul(a1,0x0e) ^ mul(a2,0x0b) ^ mul(a3,0x0d)
            s[i+2] = mul(a0,0x0d) ^ mul(a1,0x09) ^ mul(a2,0x0e) ^ mul(a3,0x0b)
            s[i+3] = mul(a0,0x0b) ^ mul(a1,0x0d) ^ mul(a2,0x09) ^ mul(a3,0x0e)
        return bytes(s)

    @staticmethod
    def _decrypt_block(block, rk):
        Nr = 10
        state = bytes(a ^ b for a, b in zip(block, rk[16*Nr:16*(Nr+1)]))
        for r in range(Nr-1, 0, -1):
            state = AES128._inv_shift_rows(state)
            state = AES128._inv_sub_bytes(state)
            state = bytes(a ^ b for a, b in zip(state, rk[16*r:16*(r+1)]))
            state = AES128._inv_mix_columns(state)
        state = AES128._inv_shift_rows(state)
        state = AES128._inv_sub_bytes(state)
        state = bytes(a ^ b for a, b in zip(state, rk[0:16]))
        return state

    @staticmethod
    def decrypt_cbc(ciphertext, key, iv):
        """AES-128-CBC 解密 + 严格 PKCS7 去填充"""
        assert len(key) == 16 and len(iv) == 16
        assert len(ciphertext) % 16 == 0
        rk = AES128._expand_key(key)
        plaintext = bytearray()
        prev = iv
        for i in range(0, len(ciphertext), 16):
            block = ciphertext[i:i+16]
            decrypted = AES128._decrypt_block(block, rk)
            plain_block = bytes(a ^ b for a, b in zip(decrypted, prev))
            plaintext += plain_block
            prev = block
        if len(plaintext) > 0:
            pad = plaintext[-1]
            if 1 <= pad <= 16 and len(plaintext) >= pad:
                if all(b == pad for b in plaintext[-pad:]):
                    plaintext = plaintext[:-pad]
        return bytes(plaintext).decode("utf-8", errors="replace")

    @staticmethod
    def _encrypt_block(block, rk):
        Nr = 10
        state = bytes(a ^ b for a, b in zip(block, rk[0:16]))
        for r in range(1, Nr):
            state = AES128._sub_bytes(state)
            state = AES128._shift_rows(state)
            state = AES128._mix_columns(state)
            state = bytes(a ^ b for a, b in zip(state, rk[16*r:16*(r+1)]))
        state = AES128._sub_bytes(state)
        state = AES128._shift_rows(state)
        state = bytes(a ^ b for a, b in zip(state, rk[16*Nr:16*(Nr+1)]))
        return state

    @staticmethod
    def _sub_bytes(state):
        return bytes(AES128.SBOX[b] for b in state)

    @staticmethod
    def _shift_rows(state):
        s = list(state)
        for row, shift in [(1, 1), (2, 2), (3, 3)]:
            base = [s[row + 4*c] for c in range(4)]
            base = base[shift:] + base[:shift]
            for c in range(4):
                s[row + 4*c] = base[c]
        return bytes(s)

    @staticmethod
    def _mix_columns(state):
        def mul(a, b):
            r = 0
            while b:
                if b & 1:
                    r ^= a
                a = AES128._xtime(a)
                b >>= 1
            return r
        s = list(state)
        for c in range(4):
            i = 4*c
            a0, a1, a2, a3 = s[i], s[i+1], s[i+2], s[i+3]
            s[i]   = AES128._xtime(a0) ^ (AES128._xtime(a1) ^ a1) ^ a2 ^ a3
            s[i+1] = a0 ^ AES128._xtime(a1) ^ (AES128._xtime(a2) ^ a2) ^ a3
            s[i+2] = a0 ^ a1 ^ AES128._xtime(a2) ^ (AES128._xtime(a3) ^ a3)
            s[i+3] = (AES128._xtime(a0) ^ a0) ^ a1 ^ a2 ^ AES128._xtime(a3)
        return bytes(s)

    @staticmethod
    def _encrypt_cbc(plaintext, key, iv):
        assert len(key) == 16 and len(iv) == 16
        rk = AES128._expand_key(key)
        prev = iv
        out = bytearray()
        for i in range(0, len(plaintext), 16):
            block = plaintext[i:i+16]
            xored = bytes(a ^ b for a, b in zip(block, prev))
            encrypted = AES128._encrypt_block(xored, rk)
            out += encrypted
            prev = encrypted
        return bytes(out)


# ======================================================================
# ★ 配置区 —— 从外部文件加载
# ======================================================================
RAW_API_LIST, API_MIRRORS = _load_api_config()

# 请求指纹池
TVBOX_UAS = [
    ("okhttp/3.15",                               "com.iptvbox"),
    ("okhttp/4.9.3",                               "com.iptvbox"),
    ("TVBox/1.0.0",                                "com.iptvbox"),
    ("com.github.tvbox",                           "com.iptvbox"),
    ("Dalvik/2.1.0 (Linux; U; Android 9; Pixel 3 XL Build/PQ3A.190801.002)", "com.iptvbox"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", ""),
]

HEADERS_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
}
OUTPUT_DIR = "output"
LIST_TXT = "list.txt"
MAX_DEPTH = 5
REQUEST_TIMEOUT = 20
TOTAL_TIMEOUT = 45


# ======================================================================
# ★ URL 规范化 + 按接口名分组
# ======================================================================
def normalize_url(url):
    """
    URL 规范化：
    - 去掉代理前缀中的双 https：http://proxy.com/https://raw.xxx  → 取最右侧协议起点
    - 去掉末尾单斜杠（路径部分一致时去重）
    """
    if not url:
        return url
    u = url.strip()
    matches = list(re.finditer(r"https?://", u))
    if len(matches) >= 2:
        u = u[matches[-1].start():]
    if u.endswith("/") and u.count("/") > 2:
        u = u.rstrip("/")
    return u


def build_api_list(raw_api_list, api_mirrors):
    """
    把原始 API_LIST（[(name, url), ...]）按接口名分组：
    - 同名条目 + API_MIRRORS 中的镜像 → 合并成一个 URL 列表（去重、规范化）
    """
    from collections import OrderedDict
    grouped = OrderedDict()
    for item in raw_api_list:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        name, url = item[0], item[1]
        if not url:
            continue
        norm = normalize_url(url)
        grouped.setdefault(name, OrderedDict())
        grouped[name][norm] = None

    for name, mirrors in api_mirrors.items():
        if not isinstance(mirrors, list):
            mirrors = [mirrors]
        grouped.setdefault(name, OrderedDict())
        for u in mirrors:
            if not u:
                continue
            norm = normalize_url(u)
            grouped[name][norm] = None

    result = []
    for name, url_dict in grouped.items():
        urls = list(url_dict.keys())
        result.append((name, urls))
    return result


API_LIST = build_api_list(RAW_API_LIST, API_MIRRORS)


# ======================================================================
# 通用工具
# ======================================================================
def right_padding(s, ch, length):
    if len(s) >= length:
        return s[:length]
    return s + (ch * (length - len(s)))


def is_json(text):
    if not text:
        return False
    t = text.strip()
    return t.startswith("{") or t.startswith("[")


def collapse_whitespace(text):
    if text is None:
        return ""
    if not re.search(r"\s", text):
        return text
    return re.sub(r"\s+", " ", text).strip()


def filter_json(text):
    try:
        obj = json.loads(text)
    except Exception:
        return text
    if not isinstance(obj, dict):
        return json.dumps(obj, ensure_ascii=False, indent=2)
    keep = {"video", "sites", "lives", "parses", "rules",
            "spider", "wallpaper", "livePlayHeaders", "md5", "name", "homeSite",
            "homeLogo", "homeBg", "homeSearch", "homeRec", "searchable",
            "logo"}
    filtered = {k: v for k, v in obj.items() if k in keep}
    if not filtered:
        filtered = obj
    return json.dumps(filtered, ensure_ascii=False, indent=2)


def get_base_url(source_url):
    if not source_url:
        return ""
    idx = source_url.rfind("/")
    if idx <= 0:
        return ""
    return source_url[:idx + 1]


def is_absolute_url(value):
    if not isinstance(value, str):
        return True
    v = value.strip()
    if not v:
        return True
    if v.startswith(("http://", "https://", "data:", "file://", "//")):
        return True
    return False


def resolve_url(rel_path, base_url):
    if not rel_path or is_absolute_url(rel_path):
        return rel_path
    if not base_url:
        return rel_path
    from urllib.parse import urljoin
    return urljoin(base_url, rel_path)


# ======================================================================
# ★ 修复后的 absolutize_json —— ext 只补全"像路径"的值
# ======================================================================
def absolutize_json(text, source_url):
    """将 JSON 中的所有相对 URL 转换为绝对 URL"""
    if not source_url:
        return text
    try:
        obj = json.loads(text)
    except Exception:
        return text

    base = get_base_url(source_url)
    if not base:
        return text

    top_url_fields = {
        "spider", "wallpaper", "homeLogo", "homeBg",
        "homeSite", "livePlayHeaders", "logo", "homeSearch",
        "homeRec", "md5"
    }

    spider_prefixes = ("csp_", "json_", "nodejs_", "py_", "js_", "http_")

    def should_resolve(val):
        if not val or not isinstance(val, str):
            return False
        val = val.strip()
        if not val:
            return False
        if val.startswith(("http://", "https://", "data:", "file://", "//")):
            return False
        if any(val.startswith(p) for p in spider_prefixes):
            return False
        if val.isdigit():
            return False
        return True

    def resolve_if_needed(val):
        return resolve_url(val, base) if should_resolve(val) else val

    # ★★★ 递归处理 ext 对象 ★★★
    def looks_like_path(v):
        """
        判断 ext 里的值是否像【文件路径】，而不是纯标识符。
        - 像路径："./sub/site.php"、"sub/x.php"、"site.php" → 需要补全
        - 纯标识符："ly"、"huaxin"、"danqing" → 不补全（它们是接口参数）
        """
        if not isinstance(v, str):
            return False
        v = v.strip()
        if not v:
            return False
        if v.startswith(("http://", "https://", "//", "data:", "file://")):
            return False   # 已是绝对地址，不用补
        if "/" in v:
            return True    # 含斜杠 → 当路径
        if "." in v and not v.replace(".", "").isdigit():
            return True    # 含点且不是纯数字 → 当路径
        return False       # "ly" / "huaxin" 这类纯标识符 → 不补

    def resolve_ext_object(ext):
        if isinstance(ext, str):
            return resolve_if_needed(ext) if looks_like_path(ext) else ext
        if isinstance(ext, list):
            return [resolve_if_needed(v) if looks_like_path(v) else v for v in ext]
        if isinstance(ext, dict):
            for k, v in ext.items():
                if isinstance(v, str):
                    ext[k] = resolve_if_needed(v) if looks_like_path(v) else v
                elif isinstance(v, list):
                    ext[k] = [resolve_if_needed(i) if looks_like_path(i) else i for i in v]
                elif isinstance(v, dict):
                    resolve_ext_object(v)
        return ext

    if isinstance(obj, dict):
        for field in top_url_fields:
            if field in obj and should_resolve(obj[field]):
                obj[field] = resolve_url(obj[field], base)

        if "sites" in obj and isinstance(obj["sites"], list):
            for site in obj["sites"]:
                if not isinstance(site, dict):
                    continue
                if "ext" in site:
                    site["ext"] = resolve_ext_object(site["ext"])
                for field in ("jar", "playUrl", "logo", "url", "epg"):
                    if field in site and should_resolve(site[field]):
                        site[field] = resolve_url(site[field], base)
                if "api" in site and isinstance(site["api"], str):
                    api_val = site["api"].strip()
                    if _looks_like_url(api_val) and should_resolve(api_val):
                        site["api"] = resolve_url(api_val, base)

        if "lives" in obj and isinstance(obj["lives"], list):
            for live in obj["lives"]:
                if not isinstance(live, dict):
                    continue
                for field in ("url", "logo", "epg", "playUrl"):
                    if field in live and should_resolve(live[field]):
                        live[field] = resolve_url(live[field], base)

        if "parses" in obj and isinstance(obj["parses"], list):
            for parse in obj["parses"]:
                if not isinstance(parse, dict):
                    continue
                for field in ("url", "logo"):
                    if field in parse and should_resolve(parse[field]):
                        parse[field] = resolve_url(parse[field], base)

        if "rules" in obj and isinstance(obj["rules"], list):
            for rule in obj["rules"]:
                if not isinstance(rule, dict):
                    continue
                if "url" in rule and should_resolve(rule["url"]):
                    rule["url"] = resolve_url(rule["url"], base)

    return json.dumps(obj, ensure_ascii=False, indent=2)


def _looks_like_url(value):
    if not value:
        return False
    if value.startswith(("http://", "https://", "//", "data:")):
        return True
    if "/" in value:
        return True
    if "." in value and not value.startswith("."):
        parts = value.split(".")
        if len(parts) >= 2 and all(p for p in parts):
            return True
    return False


# ======================================================================
# list.txt —— 去重汇总
# ======================================================================
def fmt_size(num_bytes):
    if num_bytes is None:
        return "-"
    try:
        num_bytes = int(num_bytes)
    except (ValueError, TypeError):
        return "-"
    if num_bytes < 0:
        return "-"
    if num_bytes < 1024:
        return f"{num_bytes}B"
    return f"{num_bytes / 1024:.1f}K"


def _file_key(name):
    return re.sub(r"[^\w\u4e00-\u9fff]", "_", name)

_SUFFIX_RE = re.compile(r"(?:线路|一线|二线|三线|vip线|专线|备用|主线路?|测试|勿传|vip|line)\s*$", re.I)

def _note_of(name):
    if not name:
        return ""
    base = _file_key(name)
    cleaned = _SUFFIX_RE.sub("", base)
    return cleaned if cleaned else base


def load_list_txt(path=LIST_TXT):
    latest = {}
    if not os.path.exists(path):
        return latest
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n").rstrip("\r").strip()
            if not line:
                continue
            parts = re.split(r"\t|\|", line, maxsplit=3)
            if len(parts) < 2:
                continue
            file_name = parts[0].strip()
            date_str = parts[1].strip()
            size_str = parts[2].strip() if len(parts) >= 3 else "-"
            url_str = parts[3].strip() if len(parts) >= 4 else ""
            if not file_name or not date_str:
                continue
            latest[file_name] = (date_str, size_str, url_str)
    return latest


def save_list_txt(latest, path=LIST_TXT):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for file_name, rec in sorted(latest.items(), key=lambda kv: kv[1][0], reverse=True):
            date_str, size_str, url_str = rec if len(rec) == 3 else (rec[0], rec[1], "")
            f.write(f"{file_name}|{date_str}|{size_str}|{url_str}\n")


def update_list_txt(results, path=LIST_TXT):
    today = today_str()
    latest = load_list_txt(path)

    for info in results:
        name = info.get("name")
        if not name:
            continue
        file_name = _file_key(name) + ".json"
        if info.get("ok"):
            date_str = info.get("date") or today
            size_k = fmt_size(info.get("bytes"))
            success_url = info.get("success_url", "")
            latest[file_name] = (date_str, size_k, success_url)
        else:
            if file_name not in latest:
                latest[file_name] = (today, "-", "")

    save_list_txt(latest, path)

    print("\n" + "=" * 62)
    print(f"  list.txt 更新记录（去重，每接口一行）  ({path})")
    print("=" * 62)
    if not latest:
        print("  （暂无成功记录）")
    for file_name, rec in sorted(latest.items(), key=lambda kv: kv[1][0], reverse=True):
        date_str, size_str, url_str = rec if len(rec) == 3 else (rec[0], rec[1], "")
        flag = "最新" if date_str == today else "历史"
        print(f"  {file_name}|{date_str}|{size_str}|{url_str}  [{flag}]")
    print("=" * 62)
    return latest


# ======================================================================
# 解密相关
# ======================================================================
def _try_decrypt_2423_hex(stripped):
    idx2423 = stripped.index("2423")
    idx2324 = stripped.index("2324")
    key_hex = stripped[idx2423 + 4: idx2324]
    try:
        key_raw = bytes.fromhex(key_hex).decode("latin-1", errors="ignore")
    except Exception:
        key_raw = key_hex
    key_str = right_padding(key_raw, "0", 16)
    data_start = idx2324 + 4
    data_end = len(stripped) - 26
    if data_end <= data_start:
        raise ValueError("hex形态: data区间非法")
    data_hex = stripped[data_start: data_end]
    content_rstrip = stripped.rstrip()
    ts_hex = content_rstrip[len(content_rstrip) - 26:]
    try:
        ts_bytes = bytes.fromhex(ts_hex)
    except Exception:
        ts_bytes = ts_hex.encode("utf-8")
    iv_str = right_padding(ts_bytes.decode("latin-1"), "0", 16)
    key_bytes = key_str.encode("utf-8")[:16]
    iv_bytes = iv_str.encode("utf-8")[:16]
    cipher_bytes = binascii.unhexlify(data_hex)
    return AES128.decrypt_cbc(cipher_bytes, key_bytes, iv_bytes)


def _try_decrypt_2423_plain(S):
    idx2324 = S.index("2324")
    p_doll = S.index("$#")
    p_sharp = S.index("#$")
    data_hex = S[idx2324 + 4: p_doll]
    data_hex = re.sub(r"[^0-9a-fA-F]", "", data_hex)
    if len(data_hex) % 2 != 0:
        data_hex = data_hex[:-1]
    key = right_padding(S[p_doll + 2: p_sharp], "0", 16)
    iv = right_padding(S[len(S) - 13:], "0", 16)
    key_bytes = key.encode("latin-1")[:16]
    iv_bytes = iv.encode("latin-1")[:16]
    cipher_bytes = bytes.fromhex(data_hex)
    return AES128.decrypt_cbc(cipher_bytes, key_bytes, iv_bytes)


def find_result(raw_text, _raw_bytes=None, _depth=0):
    if _raw_bytes is None and raw_text is not None:
        _raw_bytes = raw_text.encode("utf-8", errors="ignore")
    content = raw_text if raw_text is not None else ""
    if not content and _raw_bytes:
        content = _raw_bytes.decode("utf-8", errors="ignore")
    if is_json(content):
        return content
    star_idx = None
    if _raw_bytes is not None:
        pos = _raw_bytes.find(b"**")
        if pos >= 8:
            star_idx = pos
    if star_idx is None:
        m = re.search(r"[A-Za-z0-9]{8}\*\*", content)
        if m:
            star_idx = content.index(m.group()) + 10
    if star_idx is not None:
        if _raw_bytes is not None:
            b64_bytes = _raw_bytes[star_idx + 2:]
            b64_bytes = bytes(b for b in b64_bytes if b not in (0x09, 0x0a, 0x0d, 0x20))
            try:
                decoded = base64.b64decode(b64_bytes + b"==").decode("utf-8", errors="ignore")
                return find_result(decoded, _depth=_depth + 1)
            except Exception as e:
                dbg(f"字节壳base64解码失败: {e}")
                content = b64_bytes.decode("latin-1", errors="ignore")
        else:
            b64 = re.sub(r"[^A-Za-z0-9+/=]", "", content[star_idx:])
            try:
                decoded = base64.b64decode(b64 + "==").decode("utf-8", errors="ignore")
                return find_result(decoded, _depth=_depth + 1)
            except Exception:
                content = b64
    stripped = collapse_whitespace(content).strip()
    has_delim = "$#" in stripped and "#$" in stripped
    has_2423_structure = stripped.startswith("2423") and "2324" in stripped
    if stripped.startswith("2423") and (has_delim or has_2423_structure):
        last_err = None
        try:
            result = _try_decrypt_2423_hex(stripped)
            return find_result(result, _depth=_depth + 1)
        except Exception as e:
            last_err = e
        try:
            result = _try_decrypt_2423_plain(stripped)
            return find_result(result, _depth=_depth + 1)
        except Exception as e2:
            raise RuntimeError(f"2423 双形态均解密失败: hex={last_err} / plain={e2}")
    clean = re.sub(r"\s", "", content)
    if re.match(r"^[A-Za-z0-9+/=]+$", clean) and len(clean) > 50:
        try:
            decoded = base64.b64decode(clean + "==").decode("utf-8", errors="ignore")
            if is_json(decoded):
                return decoded
        except Exception:
            pass
    if _raw_bytes is not None:
        try:
            decompressed = gzip.decompress(_raw_bytes).decode("utf-8", errors="ignore")
            if is_json(decompressed):
                return decompressed
        except Exception:
            pass
    return content


# ======================================================================
# 网络请求
# ======================================================================
def fetch_url(url, ua, xrw=""):
    headers = dict(HEADERS_BASE)
    headers["User-Agent"] = ua
    if xrw:
        headers["X-Requested-With"] = xrw

    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.netloc and any(ord(c) > 127 for c in parsed.netloc):
            try:
                import idna
                netloc = idna.encode(parsed.netloc).decode('ascii')
                url = url.replace(parsed.netloc, netloc)
                dbg(f"域名 punycode 转换: {parsed.netloc} -> {netloc}")
            except Exception as e:
                dbg(f"punycode转换失败: {e}")
    except Exception as e:
        dbg(f"URL解析失败: {e}")

    if HAVE_REQUESTS:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT,
                        allow_redirects=True, verify=False)
        if r.status_code == 200 and len(r.content) > 20:
            return r.content
        raise RuntimeError(f"HTTP {r.status_code}")
    elif HAVE_URLLIB:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read()
            if len(data) > 20:
                return data
            raise RuntimeError("empty body")
    else:
        raise RuntimeError("无可用网络库")


def try_fetch(url):
    last_err = None
    for ua, xrw in TVBOX_UAS:
        for attempt in range(2):
            try:
                raw = fetch_url(url, ua, xrw)
                if raw.lstrip().startswith(b"<"):
                    dbg(f"{url} 拿到HTML首页, 轮询下一组")
                    break
                return raw, ua
            except Exception as e:
                last_err = e
                dbg(f"{url} UA={ua} 尝试 {attempt+1} 失败: {e}")
                if attempt == 0:
                    time.sleep(0.5)
    raise RuntimeError(str(last_err))


@timeout(TOTAL_TIMEOUT)
def try_fetch_all(urls):
    errs = []
    for u in urls:
        try:
            raw, ua = try_fetch(u)
            return raw, ua, u
        except Exception as e:
            errs.append(f"{u} -> {e}")
            dbg(f"URL {u} 失败: {e}")
    raise RuntimeError(" | ".join(errs))


# ======================================================================
# ★★★ 给本地化工具用的适配接口 ★★★
# ======================================================================
def fetch_json(url, *args, **kwargs):
    """
    单接口抓取函数，供本地化阶段调用。

    返回值：(data, used_url)
        data     = 解析后的 dict（失败时 None）
        used_url = 实际连通成功的 URL（失败时 None）

    调用方式：
        data, used_url = fetch_json(urls)
    """
    if isinstance(url, (list, tuple)):
        urls = list(url)
    else:
        urls = [url]

    try:
        raw, ua, used_url = try_fetch_all(urls)
    except Exception as e:
        dbg(f"try_fetch_all 失败: {e}")
        return None, None

    try:
        decrypted = find_result("", _raw_bytes=raw)
        decrypted = extract_json(decrypted)
        decrypted = absolutize_json(decrypted, used_url)
    except Exception as e:
        dbg(f"解密/清洗失败: {e}")
        return None, used_url

    try:
        data = json.loads(decrypted)
    except Exception as e:
        dbg(f"JSON 解析失败: {e}")
        try:
            data = json.loads(filter_json(decrypted))
        except Exception:
            return None, used_url

    return data, used_url


def fetch(url, *args, **kwargs):
    return fetch_json(url, *args, **kwargs)


def get_json(url, *args, **kwargs):
    return fetch_json(url, *args, **kwargs)


# ======================================================================
# 主流程（tvbox_get_api.py 原本的独立运行入口，保留）
# ======================================================================
def clean_json_comments(text):
    if not text:
        return text
    if text.startswith('\ufeff'):
        text = text[1:]
    lines = text.split('\n')
    cleaned_lines = []
    in_block_comment = False
    for line in lines:
        if in_block_comment:
            if '*/' in line:
                in_block_comment = False
                line = line[line.index('*/') + 2:]
            else:
                continue
        if '/*' in line:
            before, after = line.split('/*', 1)
            if '*/' in after:
                line = before + after[after.index('*/') + 2:]
            else:
                line = before
                in_block_comment = True
        if '//' in line:
            in_string = False
            string_char = None
            for i, char in enumerate(line):
                if char in ('"', "'") and (i == 0 or line[i-1] != '\\'):
                    if not in_string:
                        in_string = True
                        string_char = char
                    elif char == string_char:
                        in_string = False
                elif char == '/' and i + 1 < len(line) and line[i+1] == '/' and not in_string:
                    line = line[:i]
                    break
        if line.strip():
            cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)


def extract_json(text):
    if not text:
        return text
    text = clean_json_comments(text)
    start = -1
    end = -1
    for i, char in enumerate(text):
        if char in '{[':
            start = i
            break
    if start == -1:
        return text
    for i in range(len(text) - 1, -1, -1):
        if text[i] in '}]':
            end = i + 1
            break
    if end == -1 or end <= start:
        return text
    json_text = text[start:end]
    try:
        json.loads(json_text)
        return json_text
    except Exception:
        return text


@timeout(TOTAL_TIMEOUT + 10)
def process(name, urls) -> dict:
    if isinstance(urls, str):
        urls = [urls]
    print(f"\n▶ [{name}] 尝试 {len(urls)} 个源")

    success_url = ""
    t0 = time.time()
    try:
        raw, ua, used_url = try_fetch_all(urls)
        success_url = used_url
    except TimeoutError:
        raise RuntimeError(f"抓取超时（{TOTAL_TIMEOUT}秒）")

    print(f"  ✓ 下载成功 ({len(raw)} 字节, UA={ua})")
    print(f"  源地址: {success_url}")

    decrypted = find_result("", _raw_bytes=raw)
    decrypted = extract_json(decrypted)

    try:
        obj = json.loads(decrypted)
        formatted = filter_json(decrypted)
        status = "JSON"
    except Exception as e:
        dbg(f"JSON解析失败: {e}")
        try:
            obj = json.loads(decrypted)
            formatted = json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            formatted = decrypted
        status = "TEXT"

    if status == "JSON":
        formatted = absolutize_json(formatted, success_url)

    safe = re.sub(r"[^\w\u4e00-\u9fff]", "_", name)
    path = os.path.join(OUTPUT_DIR, f"{safe}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(formatted)

    elapsed_ms = int((time.time() - t0) * 1000)
    print(f"  ✓ {status} | {len(formatted)} 字符 -> {path}")

    try:
        obj = json.loads(formatted)
        if isinstance(obj, dict):
            keys = [k for k in obj.keys() if k in {
                "sites", "lives", "parses", "rules", "spider", "wallpaper"}]
            print(f"  字段: {keys}")
            if "sites" in obj and isinstance(obj["sites"], list):
                print(f"  sites 数量: {len(obj['sites'])}")
    except Exception as e:
        dbg(f"预览解析失败: {e}")

    preview = "\n".join(formatted.split("\n")[:5])
    print(f"  预览:\n  {'~'*50}\n  " + preview.replace("\n", "\n  "))
    print(f"  {'~'*50}")

    return {
        "name": name,
        "status": status,
        "file": path,
        "ua": ua,
        "ok": status == "JSON",
        "note": _note_of(name),
        "bytes": len(formatted),
        "time_ms": elapsed_ms,
        "success_url": success_url,
    }


def main_fetch_only():
    """tvbox_get_api.py 原本的独立运行入口（只抓取，不本地化）"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = beijing_now().strftime("%Y%m%d_%H%M%S")
    summary = []

    print("=" * 62)
    print(f"  TVBox 接口一键抓取  {ts}")
    print("=" * 62)

    for name, urls in API_LIST:
        try:
            info = process(name, urls)
            summary.append(info)
        except TimeoutError as e:
            print(f"  ✗ 超时: {e}")
            summary.append({"name": name, "status": "TIMEOUT", "file": None, "ok": False, "success_url": ""})
        except Exception as e:
            print(f"  ✗ 全部失败: {e}")
            summary.append({"name": name, "status": "FAILED", "file": None, "ok": False, "success_url": ""})

    update_list_txt(summary, LIST_TXT)

    report = os.path.join(OUTPUT_DIR, "SUMMARY.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write(f"TVBox 接口抓取报告  {ts}\n")
        f.write("=" * 62 + "\n\n")
        for it in summary:
            f.write(f"[{it['name']}] {it.get('ua','')}\n")
            f.write(f"  状态: {it['status']}\n")
            f.write(f"  文件: {it.get('file')}\n")
            f.write(f"  成功URL: {it.get('success_url','')}\n\n")

    print("\n" + "=" * 62)
    print("  汇总")
    print("=" * 62)
    for it in summary:
        icon = "✓" if it.get("ok") else "✗"
        print(f"  {icon} {it['name']:8s} | {it['status']:10s} | {it.get('file','')}")
    print(f"\n  报告: {report}")
    print(f"  更新日志: {LIST_TXT}")
    print("=" * 62)


# ============================================================
# ========== 第二部分：localize_tool6.9.py 全部逻辑 ==========
# ============================================================

HERE = os.path.dirname(os.path.abspath(__file__))
HERE_OVERRIDE = None


def _here():
    env = os.environ.get("LOCALIZE_TEST_WORK")
    if env:
        return env
    if HERE_OVERRIDE is not None:
        return HERE_OVERRIDE
    return HERE


def _short(url, width=70):
    if not isinstance(url, str):
        return str(url)
    if len(url) <= width:
        return url
    return url[:width-20] + "..." + url[-15:]


def _log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}", flush=True)


# ----------------------------------------------------------------------
# 核心：文件链接判定
# ----------------------------------------------------------------------
def _url_path(url):
    """取 URL 尾段路径（去掉查询串），并做 URL 解码 + 去特殊符号"""
    if not isinstance(url, str):
        return ""
    no_q = url.split("?")[0].split("#")[0]
    tail = no_q.rsplit("/", 1)[-1]
    return tail


def is_file_url(url):
    if not isinstance(url, str):
        return False
    low = url.lower()
    if not low.startswith(("http://", "https://")):
        return False
    for kw in KEEP_ONLINE_KEYWORDS:
        if kw and kw in url:
            return False
    tail = _url_path(url).lower()
    if any(tail.endswith(e.lower()) for e in FILE_EXT_BLACKLIST):
        return False
    return any(tail.endswith(e.lower()) for e in FILE_EXT_WHITELIST)


# ★ 文件名允许保留的字符：
#   中文、英文字母、数字、点、横杠
#   其他（如 : ? * " < > | \ / % # 空格 等）全部去掉
_SAFE_NAME_RE = re.compile(r"[^\w\u4e00-\u9fff.\-]", re.UNICODE)


def sanitize_filename(name):
    """
    ★ 清洗文件名：
      1. URL 解码（%E6%96%97 → 斗鱼）
      2. 去掉特殊符号（只保留中文/字母/数字/点/横杠）
      3. 去掉开头结尾的点（避免隐藏文件/无扩展名）
    """
    if not name:
        return name
    # 1. URL 解码
    try:
        decoded = unquote(name)
    except Exception:
        decoded = name
    # 2. 去特殊符号
    safe = _SAFE_NAME_RE.sub("", decoded)
    # 3. 首尾点清理
    safe = safe.strip(".")
    # 4. 兜底：如果清空了，用 hash
    if not safe or "." not in safe:
        safe = hashlib.md5(name.encode("utf-8")).hexdigest()[:12]
    return safe


def url_to_name(url):
    """
    从 URL 推导本地文件名。
    步骤：
      1. 取路径尾段（含 ;md5; 的部分先切掉）
      2. URL 解码 + 去特殊符号
    """
    name = _url_path(url)
    if ";md5;" in name:
        name = name.split(";md5;")[0]
    name = sanitize_filename(name)
    return name


def split_md5(url):
    if not isinstance(url, str):
        return url, ""
    if ";md5;" in url:
        base, _, md5part = url.partition(";md5;")
        return base, ";md5;" + md5part
    return url, ""


def _make_numbered_name(base_name, seq):
    """
    ★ 编号不加下划线：
      spider.jar + 2 → spider2.jar
      spider.jar + 3 → spider3.jar
      abc + 2 → abc2
    """
    if "." in base_name:
        stem, ext = base_name.rsplit(".", 1)
        return f"{stem}{seq}.{ext}"
    return f"{base_name}{seq}"


# ----------------------------------------------------------------------
# 下载器（带同名编号）
# ----------------------------------------------------------------------
class Downloader:
    def __init__(self, api_dir, force):
        self.api_dir = api_dir
        self.lib_dir = os.path.join(api_dir, "lib")
        os.makedirs(self.lib_dir, exist_ok=True)
        self.force = force
        self.cache = {}
        self.name_owner = {}
        self.stats = {"downloaded": 0, "kept": 0, "failed": 0, "skipped": 0}

    def _clean_url(self, url):
        """
        ★ 不按 http 截断链接。
        """
        if not isinstance(url, str):
            return url
        return url.strip()

    def _assign_name(self, desired_name, url):
        """
        ★ 给 URL 分配不冲突的本地文件名：
          - 名字空闲 → 直接用
          - 名字被同 URL 占用 → 复用
          - 名字被别的 URL 占用 → 加数字：spider2.jar、spider3.jar
        """
        if url in self.cache:
            return self.cache[url]

        if desired_name not in self.name_owner:
            self.name_owner[desired_name] = url
            return desired_name

        seq = 2
        while True:
            candidate = _make_numbered_name(desired_name, seq)
            if candidate not in self.name_owner:
                self.name_owner[candidate] = url
                return candidate
            if self.name_owner[candidate] == url:
                return candidate
            seq += 1

    def get(self, url, hint_name=None):
        url = self._clean_url(url)
        if not is_file_url(url):
            if SHOW_SKIPPED_LINKS:
                _log(f"   ↷ 跳过(非文件): {_short(url)}")
            return None

        desired = hint_name or url_to_name(url)
        desired = sanitize_filename(desired)

        if url in self.cache:
            if SHOW_CACHE_HIT:
                _log(f"   ♻ 缓存命中: {self.cache[url]}")
            return self.cache[url]

        name = self._assign_name(desired, url)
        local = os.path.join(self.lib_dir, name)

        if (not self.force) and os.path.exists(local):
            self.stats["skipped"] += 1
            rel = os.path.relpath(local, self.api_dir).replace("\\", "/")
            result = f"./{rel}"
            self.cache[url] = result
            if SHOW_DOWNLOAD_DETAIL:
                _log(f"   ⏭ 已存在跳过: {name}")
            return result

        if self.force and os.path.exists(local):
            try:
                os.remove(local)
            except OSError:
                pass

        if SHOW_DOWNLOAD_DETAIL:
            _log(f"   ⬇ 开始下载: {name}")
            _log(f"       源: {_short(url)}")

        t0 = time.time()
        data = self._raw_download(url)
        elapsed = time.time() - t0

        if data is None or len(data) == 0:
            self.stats["failed"] += 1
            self.cache[url] = None
            _log(f"   ✗ 下载失败: {name}  ({elapsed:.1f}s)")
            return None

        tmp = local + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, local)
        except OSError as e:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            self.stats["failed"] += 1
            self.cache[url] = None
            _log(f"   ✗ 写盘失败: {name}  ({e})")
            return None

        self.stats["downloaded"] += 1
        rel = os.path.relpath(local, self.api_dir).replace("\\", "/")
        result = f"./{rel}"
        self.cache[url] = result

        size = len(data)
        size_str = f"{size/1024:.1f}K" if size >= 1024 else f"{size}B"
        if name != desired:
            _log(f"   ✓ 下载成功: {name}  (原名 {desired} 已被占用, {size_str}, {elapsed:.1f}s) -> {result}")
        else:
            _log(f"   ✓ 下载成功: {name}  ({size_str}, {elapsed:.1f}s) -> {result}")
        return result

    def _raw_download(self, url):
        for attr in ("download_file", "download", "_get", "raw_get", "get_bytes"):
            fn = getattr(sys.modules[__name__], attr, None)
            if callable(fn):
                try:
                    ret = fn(url)
                except Exception:
                    ret = None
                if isinstance(ret, bytes) and len(ret) > 0:
                    return ret
                if isinstance(ret, str) and len(ret) > 0:
                    return ret.encode("utf-8", errors="ignore")

        headers = {}
        ua_pool = TVBOX_UAS
        if ua_pool:
            first = ua_pool[0]
            if isinstance(first, tuple):
                headers["User-Agent"] = first[0]
                if len(first) > 1 and first[1]:
                    headers["X-Requested-With"] = first[1]
            elif isinstance(first, dict):
                headers.update(first)

        if "requests" in sys.modules:
            try:
                import requests
                r = requests.get(url, headers=headers, timeout=20, verify=False)
                if r.status_code == 200 and len(r.content) > 0:
                    return r.content
            except Exception:
                pass

        try:
            from urllib.request import Request, urlopen
            req = Request(url, headers=headers)
            with urlopen(req, timeout=20) as resp:
                return resp.read()
        except Exception:
            return None


def localize_value(value, dl, path_prefix=""):
    if isinstance(value, str):
        base_url, md5_suffix = split_md5(value)
        if is_file_url(base_url):
            local = dl.get(base_url, hint_name=url_to_name(base_url))
            if local:
                if SHOW_SITE_PROGRESS and path_prefix:
                    _log(f"     · {path_prefix} -> {local}")
                return local + md5_suffix
        return value

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            child = f"{path_prefix}.{k}" if path_prefix else k
            out[k] = localize_value(v, dl, child)
        return out

    if isinstance(value, list):
        return [localize_value(v, dl, f"{path_prefix}[{i}]") for i, v in enumerate(value)]

    return value


def stage2(name, data, out_root):
    api_dir = os.path.join(out_root, name)
    os.makedirs(api_dir, exist_ok=True)
    os.makedirs(os.path.join(api_dir, "lib"), exist_ok=True)

    dl = Downloader(api_dir, force=FORCE_REDOWNLOAD)

    spider_val = data.get("spider")
    if isinstance(spider_val, str) and spider_val.startswith(("http://", "https://")):
        base_url, md5_suffix = split_md5(spider_val)
        _log(f"   ⬇ 开始下载 spider: {_short(base_url)}")
        local = dl.get(base_url, hint_name=SPIDER_LOCAL_NAME if SPIDER_FORCE_RENAME else None)
        if local:
            data["spider"] = local + md5_suffix
            _log(f"   ✓ spider 本地化: {data['spider']}")
        else:
            data["spider"] = spider_val
            dl.stats["kept"] += 1
            _log(f"   ✗ spider 下载失败，保持在线")
    else:
        dl.stats["kept"] += 1

    sites = data.get("sites")
    if isinstance(sites, list):
        total = len(sites)
        _log(f"   ▶ 开始处理 sites，共 {total} 个条目")
        new_sites = []
        for idx, site in enumerate(sites):
            if not isinstance(site, dict):
                new_sites.append(site)
                continue

            site_name = site.get("name", "?")
            site_key = site.get("key", "?")
            if SHOW_SITE_PROGRESS:
                _log(f"   [{idx+1}/{total}] 处理: {site_key} | {site_name}")

            new_site = localize_value(site, dl, f"sites[{idx}]")
            new_sites.append(new_site)

            if SHOW_SITE_PROGRESS:
                _log(f"   [{idx+1}/{total}] 完成: {site_key}")
        data["sites"] = new_sites
        _log(f"   ✓ sites 处理完毕（{total} 个）")
    else:
        _log(f"   ⚠ 无 sites 字段或不是列表")

    for f in KEEP_ONLINE_FIELDS:
        if f in data:
            dl.stats["kept"] += 1

    logo_url = data.get("logo")
    if isinstance(logo_url, str) and is_file_url(logo_url):
        base_url, md5_suffix = split_md5(logo_url)
        local = dl.get(base_url)
        if local:
            data["logo"] = local + md5_suffix
        else:
            dl.stats["kept"] += 1
    else:
        dl.stats["kept"] += 1

    out_path = os.path.join(api_dir, "api.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _log(f"   💾 已写入: {out_path}")

    return dl.stats


# ============================================================
# ========== 第三部分：统一主入口 ==========
# ============================================================

def main_localize():
    """本地化主流程（调用内置的 API_LIST / fetch_json）"""
    print("=" * 60)
    print("  TVBox 接口本地化工具 v7.0（单文件合并版）")
    print("  阶段一【获取】= 内置 tvbox_get_api.py 全部逻辑")
    print("  阶段二【本地化】= 内置 localize_tool6.9.py 全部逻辑")
    print("=" * 60)
    print(f"  配置文件   : api_list.json（优先）/ api_list.py（备用）")
    print(f"  输出根目录: {OUTPUT_ROOT}")
    print(f"  强制覆盖 : {'是' if FORCE_REDOWNLOAD else '否'}")
    print(f"  后缀白名单: {FILE_EXT_WHITELIST}")
    print(f"  后缀黑名单: {FILE_EXT_BLACKLIST}")
    print(f"  保留链接数: {len(KEEP_ONLINE_KEYWORDS)}")
    print(f"  接口数量 : {len(API_LIST)}")
    print(f"  实时动态 : site进度={'开' if SHOW_SITE_PROGRESS else '关'}"
          f" 下载细节={'开' if SHOW_DOWNLOAD_DETAIL else '关'}"
          f" 缓存命中={'开' if SHOW_CACHE_HIT else '关'}")
    print()

    out_root = os.path.join(HERE, OUTPUT_ROOT)
    os.makedirs(out_root, exist_ok=True)

    results = []
    total_iface = len(API_LIST)

    for iface_idx, item in enumerate(API_LIST, 1):
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        name, urls = item[0], item[1]
        if isinstance(urls, str):
            urls = [urls]

        print()
        print("=" * 60)
        print(f"  [{iface_idx}/{total_iface}] 接口: {name}")
        print(f"  源数量: {len(urls)}")
        print("=" * 60)

        _log(f"▶ 开始抓取（{len(urls)} 个源）")
        for i, u in enumerate(urls, 1):
            _log(f"   源{i}: {_short(u)}")

        t_fetch = time.time()
        try:
            data, used_url = fetch_json(urls)
        except Exception as e:
            data, used_url = None, None
            _log(f"✗ 抓取异常: {e}")
        fetch_elapsed = time.time() - t_fetch

        if not data or not isinstance(data, (dict, list)):
            _log(f"✗ {name}: 所有地址均无法获取 JSON（耗时 {fetch_elapsed:.1f}s）")
            api_dir = os.path.join(out_root, name)
            if os.path.exists(api_dir):
                shutil.rmtree(api_dir)
            results.append({
                "name": name, "success": False,
                "error": "所有地址均无法获取 JSON",
                "used_url": None, "downloaded": 0, "kept": 0,
            })
            continue

        _log(f"✓ 抓取成功（{fetch_elapsed:.1f}s）源: {_short(used_url or '')}")
        if isinstance(data, dict):
            _log(f"   顶层字段: {list(data.keys())}")
            if "sites" in data and isinstance(data["sites"], list):
                _log(f"   sites 数量: {len(data['sites'])}")

        _log(f"▶ 开始本地化...")
        t_stage2 = time.time()
        try:
            stats = stage2(name, data, out_root)
        except Exception as e:
            _log(f"✗ 本地化异常: {e}")
            api_dir = os.path.join(out_root, name)
            if os.path.exists(api_dir):
                shutil.rmtree(api_dir)
            results.append({
                "name": name, "success": False,
                "error": f"本地化异常: {e}",
                "used_url": used_url, "downloaded": 0, "kept": 0,
            })
            continue
        stage2_elapsed = time.time() - t_stage2

        _log(f"✓ {name} 完成: 下载 {stats['downloaded']} / 保留 {stats['kept']}"
             + (f" / 失败 {stats['failed']}" if stats["failed"] else "")
             + f"（本地化耗时 {stage2_elapsed:.1f}s，总计 {fetch_elapsed+stage2_elapsed:.1f}s）")

        results.append({
            "name": name, "success": True, "used_url": used_url,
            "downloaded": stats["downloaded"], "kept": stats["kept"],
            "failed": stats["failed"],
        })

    manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "fetch_script": "内置",
            "force_redownload": FORCE_REDOWNLOAD,
            "output_root": OUTPUT_ROOT,
            "file_ext_whitelist": FILE_EXT_WHITELIST,
            "file_ext_blacklist": FILE_EXT_BLACKLIST,
            "keep_online_keywords": KEEP_ONLINE_KEYWORDS,
        },
        "summary": {
            "total": len(results),
            "success": sum(1 for r in results if r["success"]),
            "failed": sum(1 for r in results if not r["success"]),
        },
        "results": results,
    }
    manifest_path = os.path.join(HERE, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 60)
    print("  汇总")
    print("=" * 60)
    for r in results:
        if r["success"]:
            print(f"  ✓ {r['name']:10s} | 下载 {r['downloaded']:3d} / 保留 {r['kept']:3d}")
        else:
            print(f"  ✗ {r['name']:10s} | {r.get('error', '未知')}")
    s = manifest["summary"]
    print()
    print(f"  成功: {s['success']}  失败: {s['failed']}  总计: {s['total']}")
    print(f"  合集报告: {manifest_path}")
    print("=" * 60)


# ============================================================
# ========== 第四部分：自测 ==========
# ============================================================

def selftest():
    print("\n" + "=" * 62)
    print("  自测：解密逻辑 + URL规范化 + 分组 + list.txt 验证（离线）")
    print("=" * 62)

    global RAW_API_LIST, API_MIRRORS, API_LIST
    RAW_API_LIST = [
    ["天神", "https://gh-proxy.com/raw.githubusercontent.com/IY-CPU/IY/main/天神IY.png"],
    ["嗷呜", ""],
    ]
    API_MIRRORS = {
        "饭太硬": [
            "",
            "",
        ],
        "嗷呜": [""],
    }
    API_LIST = build_api_list(RAW_API_LIST, API_MIRRORS)
    print("  [准备] 已加载内置测试配置")

    plain = json.dumps({
        "sites": [{"key": "demo", "name": "测试源", "api": "http://x.com/api", "type": 1}],
        "parses": [], "rules": [], "spider": ""
    }, ensure_ascii=False)

    key_str = right_padding("mySecretKey123", "0", 16)
    ts = "1788447203629"
    iv_str = right_padding(ts, "0", 16)
    key_b = key_str.encode("utf-8")
    iv_b = iv_str.encode("utf-8")

    plain_bytes = plain.encode("utf-8")
    pad_len = 16 - (len(plain_bytes) % 16)
    ct = AES128._encrypt_cbc(plain_bytes + bytes([pad_len] * pad_len), key_b, iv_b)
    data_hex = binascii.hexlify(ct).decode("utf-8").lower()

    ts_hex = binascii.hexlify(ts.encode("utf-8")).decode("utf-8")
    payload_hex = "2423" + "mySecretKey123" + "2324" + data_hex + ts_hex

    print(f"\n[测试1] 构造 2423 hex形态 报文 (明文 {len(plain)} 字节)")
    result = find_result(payload_hex)
    parsed = json.loads(result)
    assert parsed["sites"][0]["key"] == "demo"
    print("  ✓ 2423 hex形态 解密正确")

    assert find_result('{"a":1}') == '{"a":1}'
    print("[测试2] ✓ 已是 JSON 直接返回")

    assert normalize_url("https://gh-proxy.com/https://raw.githubusercontent.com/foo/bar") == \
           "https://raw.githubusercontent.com/foo/bar"
    print("[测试3] ✓ normalize_url 去除双 https:// 冗余前缀")
    assert normalize_url("http://example.com/path/") == "http://example.com/path"
    print("[测试4] ✓ normalize_url 去除末尾斜杠")

    grouped = API_LIST
    gdict = dict(grouped)
    assert len(gdict["饭太硬"]) == 4, gdict["饭太硬"]
    assert "http://www.饭太硬.net/tv" in gdict["饭太硬"]
    assert len(gdict["嗷呜"]) == 1
    print(f"[测试5] ✓ build_api_list 分组+去重正确")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        lp = os.path.join(tmp, "list.txt")
        fake_results = [
            {"name": "嗨哥魔改", "ok": True,  "bytes": 15360, "date": "20260905",
             "success_url": "https://api.hgyx.vip/hgyx.json"},
            {"name": "饭太硬",  "ok": True,  "bytes": 20480, "date": "20260905",
             "success_url": "http://fty.xxooo.cf/tv"},
            {"name": "嗷呜",    "ok": False, "bytes": 0, "success_url": ""},
        ]
        update_list_txt(fake_results, lp)
        latest = load_list_txt(lp)
        assert latest["嗨哥魔改.json"][2] == "https://api.hgyx.vip/hgyx.json", latest["嗨哥魔改.json"]
        assert latest["饭太硬.json"][2] == "http://fty.xxooo.cf/tv", latest["饭太硬.json"]
        assert latest["嗷呜.json"][2] == "", latest["嗷呜.json"]
        with open(lp, "r", encoding="utf-8") as f:
            content = f.read()
        for line in content.strip().split("\n"):
            assert line.count("|") == 3, line
        print("[测试6] ✓ list.txt 格式正确")

    # ★ ext 纯标识符不被污染测试
    test_json = json.dumps({
        "sites": [{
            "key": "花信", "api": "csp_NiuLai", "type": 3,
            "ext": {
                "site": "huaxin",
                "playname": "ly",
                "real_path": "./sub/real.php",
            }
        }]
    }, ensure_ascii=False)
    result = absolutize_json(test_json, "https://0.wdzb.eu.cc/tvbox/xxx.json")
    obj = json.loads(result)
    ext = obj["sites"][0]["ext"]
    assert ext["site"] == "huaxin", ext["site"]
    assert ext["playname"] == "ly", ext["playname"]
    assert ext["real_path"] == "https://0.wdzb.eu.cc/tvbox/sub/real.php", ext["real_path"]
    print("[测试7] ✓ ext 纯标识符(huaxin/ly)不补全，真路径(./sub/real.php)正常补全")

    print("\n" + "=" * 62)
    print("  全部自测通过 ✓")
    print("=" * 62)


# ============================================================
# ========== 第五部分：命令行入口 ==========
# ============================================================

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--check-config" in sys.argv:
        print("=" * 62)
        print("  配置检查（URL 规范化 + 同名分组 + 去重，不抓包）")
        print("=" * 62)
        for name, urls in API_LIST:
            print(f"\n  [{name}] {len(urls)} 个源（去重后）")
            for i, u in enumerate(urls, 1):
                print(f"    {i}. {u}")
        print("\n" + "=" * 62)
        print(f"  共 {len(API_LIST)} 个接口")
        print("=" * 62)
    elif "--fetch-only" in sys.argv:
        # 只抓取，不本地化（tvbox_get_api.py 原功能）
        main_fetch_only()
        print("\n完成! 按回车退出...")
        try:
            input()
        except EOFError:
            pass
    else:
        # 默认：抓取 + 本地化（合并工具的主功能）
        main_localize()
        print("\n完成! 按回车退出...")
        try:
            input()
        except EOFError:
            pass