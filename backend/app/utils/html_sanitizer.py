"""HTML 消毒工具 — 防止存储型 XSS

使用 nh3 (Rust 实现的 HTML 消毒库) 对 config_json 中 custom_html 模块的
html 字段进行消毒，剥离 <script>、<iframe>、on* 事件属性等危险内容。
"""

import nh3

# 允许的 HTML 标签白名单
ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
    "hr",
    "sub",
    "sup",
    "details",
    "summary",
}

# 允许的属性白名单（按标签）
ALLOWED_ATTRIBUTES = {
    "*": {"class", "style", "id"},
    "a": {"href", "target", "rel", "title"},
    "img": {"src", "alt", "width", "height", "loading"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}

# 允许的 URL 协议
ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "tel"}


def sanitize_html(html: str) -> str:
    """消毒 HTML 内容，剥离危险标签和属性。

    Args:
        html: 原始 HTML 字符串

    Returns:
        消毒后的安全 HTML 字符串
    """
    if not html:
        return html

    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        clean_content_tags={"script", "style", "iframe", "object", "embed", "form"},
        link_rel=None,
    )


def sanitize_config_html(config_json: dict) -> dict:
    """递归扫描 config_json，对 custom_html 类型模块的 html 字段消毒。

    config_json 结构：{ modules: [{type, config: {html}}, ...], ... }

    Args:
        config_json: 页面 DSL 配置

    Returns:
        原地修改并返回 config_json
    """
    modules = config_json.get("modules")
    if not isinstance(modules, list):
        return config_json

    for module in modules:
        if not isinstance(module, dict):
            continue
        if module.get("type") != "custom_html":
            continue
        config = module.get("config")
        if not isinstance(config, dict):
            continue
        html_value = config.get("html")
        if isinstance(html_value, str):
            config["html"] = sanitize_html(html_value)

    return config_json
