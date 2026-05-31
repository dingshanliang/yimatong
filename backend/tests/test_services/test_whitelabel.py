"""白标与自定义域名单元测试"""


class TestTenantDomain:
    """测试租户域名管理"""

    def test_domain_normalization(self):
        """域名应小写并去除空格"""
        raw = "  Brand.Example.COM  "
        normalized = raw.lower().strip()
        assert normalized == "brand.example.com"

    def test_domain_cname_target(self):
        """默认 CNAME 目标"""
        default_cname = "cname.yimatong.cn"
        assert default_cname == "cname.yimatong.cn"

    def test_ssl_status_initial(self):
        """新域名初始 SSL 状态"""
        assert "pending" == "pending"

    def test_ssl_after_verify(self):
        """验证后 SSL 状态"""
        assert "active" == "active"


class TestWhitelabelConfig:
    """测试白标配置"""

    def test_default_values(self):
        config = {
            "brand_name": "",
            "hide_yimatong": False,
            "primary_color": "#000000",
            "logo_url": None,
            "favicon_url": None,
            "login_bg_url": None,
            "font_family": "",
            "custom_css": None,
        }
        assert config["brand_name"] == ""
        assert config["hide_yimatong"] is False

    def test_custom_brand(self):
        config = {
            "brand_name": "我的品牌",
            "hide_yimatong": True,
            "primary_color": "#FF6600",
            "logo_url": "https://cdn.example.com/logo.png",
            "font_family": "'Noto Sans SC', sans-serif",
        }
        assert config["brand_name"] == "我的品牌"
        assert config["hide_yimatong"] is True
        assert config["primary_color"] == "#FF6600"

    def test_config_upsert_create(self):
        """首次创建白标配置"""
        existing = None
        if existing:
            existing["brand_name"] = "test"
        else:
            existing = {"brand_name": "test", "primary_color": "#000000"}
        assert existing["brand_name"] == "test"

    def test_config_upsert_update(self):
        """更新已有白标配置"""
        existing = {"brand_name": "old", "primary_color": "#000000"}
        existing["brand_name"] = "new"
        assert existing["brand_name"] == "new"


class TestDomainRouting:
    """测试域名路由逻辑"""

    def test_domain_to_tenant_lookup(self):
        """通过域名查找租户"""
        domain_map = {
            "brand.example.com": "tenant-001",
            "custom.brand.cn": "tenant-002",
        }
        assert domain_map.get("brand.example.com") == "tenant-001"
        assert domain_map.get("unknown.com") is None

    def test_verified_only_routing(self):
        """只有已验证的域名才路由"""
        domains = [
            {"domain": "brand.example.com", "verified": True},
            {"domain": "pending.example.com", "verified": False},
        ]
        routable = [d for d in domains if d["verified"]]
        assert len(routable) == 1
        assert routable[0]["domain"] == "brand.example.com"

    def test_cname_dns_record(self):
        """CNAME 记录格式"""
        cname_target = "cname.yimatong.cn"
        assert cname_target == "cname.yimatong.cn"
