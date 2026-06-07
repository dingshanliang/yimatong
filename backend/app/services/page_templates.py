"""HTML 页面模板常量和构建函数（从 resolver 模块提取）"""

# 外码引流页模板
OUTER_LANDING_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>产品引流页</title>
<style>
body {{ font-family: sans-serif; margin: 0; padding: 16px; background: #f8f9fa; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
h2 {{ color: #1a1a1a; margin: 0 0 12px; }}
.hint {{ background: #e6f7ff; border: 1px solid #91d5ff; border-radius: 8px; padding: 12px; margin-top: 16px; }}
.hint p {{ margin: 0; color: #0050b3; }}
</style>
</head>
<body>
<div class="card">
<h2>产品信息</h2>
<p>码编号: {public_id}</p>
<div class="hint">
<p>请刮开包装内侧涂层，扫描内码验真领奖</p>
</div>
</div>
</body></html>"""

# 内码验真页模板
INNER_VERIFY_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>验真结果</title>
<style>
body {{ font-family: sans-serif; margin: 0; padding: 16px; background: #f8f9fa; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.badge {{ display: inline-block; background: #52c41a; color: #fff;
  padding: 4px 12px; border-radius: 12px; font-size: 14px; }}
h2 {{ color: #1a1a1a; margin: 0 0 12px; }}
</style>
</head>
<body>
<div class="card">
<h2>验真结果 <span class="badge">正品保障</span></h2>
<p>码编号: {public_id}</p>
<p>该产品为正品，请放心使用</p>
</div>
</body></html>"""

# 通用降级/提示页面
NOT_FOUND_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>码不存在</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px 16px;">
<h2>该二维码无效</h2><p>请核实后重试</p>
</body></html>"""

REVOKED_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>码已作废</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px 16px;">
<h2>该二维码已作废</h2><p>如有疑问请联系客服</p>
</body></html>"""

NOT_ACTIVE_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>尚未启用</title></head>
<body style="font-family:sans-serif;text-align:center;padding:40px 16px;">
<h2>此码尚未启用</h2><p>请联系厂家激活</p>
</body></html>"""

RISK_FROZEN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>风险冻结</title>
<style>
body { font-family: sans-serif; text-align: center; padding: 40px 16px; background: #fff2f0; }
.warn { color: #cf1322; }
</style>
</head>
<body>
<h2 class="warn">该码已被风险冻结</h2>
<p>系统检测到异常行为，该码已被临时冻结</p>
<p>如有疑问请联系客服</p>
</body>
</html>"""

DEFAULT_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>产品信息</title></head>
<body style="font-family:sans-serif;padding:16px;">
<h2>产品信息</h2><p>请稍后访问获取详细信息</p>
</body></html>"""


def build_code_page(data: dict) -> str:
    """根据码数据构建默认 HTML 页面"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>产品信息</title>
<style>
body {{ font-family: sans-serif; margin: 0; padding: 16px; }}
.info {{ background: #f5f5f5; padding: 12px; border-radius: 8px; margin-top: 12px; }}
</style>
</head>
<body>
<h2>产品信息</h2>
<div class="info">
<p>码编号: {data["public_id"]}</p>
<p>状态: {data["status"]}</p>
</div>
</body>
</html>"""
